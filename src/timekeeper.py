# -*- coding: utf-8 -*-
"""src/timekeeper.py — 进程级全局时钟（平台 1800s 硬限的统一时间源）。

背景（2026-10-18，平台悬停提示实证）：「结果测评失败」= agent 运行超过限定
时间（contracts/platform_contract.json：time_hard_seconds=1800，超时任务失败）。
0.7.5 及之前的结构里，直出链（advisor_pipeline）与多阶段降级链（pipeline）
各自持有一份从各自起点计时的预算——直出链烧满 1500s 后再移交，降级链又新开
1680/1800s，总墙钟可达 3300s，平台 1800s 外部击杀时产物尚未落盘 → 测评失败。
此外 requests 的 timeout 是逐 socket 读口径，慢流式响应（字节间隔小于超时）
可以无限续命，单次调用本身就能越过击杀线。

本模块提供唯一时间源：
- 模块首次 import 时记录进程起点（agent.py 在一切 src 导入之前引入本模块，
  t0 ≈ 进程启动）；直出链/降级链/网关全部从同一起点推算剩余预算；
- 总预算默认 TOTAL_BUDGET_DEFAULT_S（1800 平台硬限 − 300s 安全垫，覆盖
  解释器启动、写盘、镜像与击杀延迟），可用 env QW_AGENT_BUDGET_S 覆盖
  （本地工具/调试用；非法值静默回落默认）；
- call_with_wallclock：把一次阻塞调用放进守护线程，主线程按墙钟等待，
  超时抛 WallClockTimeout——封堵慢流式响应越过 socket 超时的续命孔
  （被放弃的请求在守护线程内自生自灭，进程退出即回收）。

只依赖标准库（agent.py 防线层可安全 import）；不读密钥、不写日志。
"""
from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any, Callable, Optional

PLATFORM_HARD_LIMIT_S = 1800.0
"""平台硬限（contracts/platform_contract.json time_hard_seconds，超时任务失败）。"""

PLATFORM_SAFETY_MARGIN_S = 300.0
"""平台硬限的安全垫：解释器启动 + 产物写盘/镜像 + 击杀延迟 + 防线层执行。"""

TOTAL_BUDGET_DEFAULT_S = PLATFORM_HARD_LIMIT_S - PLATFORM_SAFETY_MARGIN_S  # 1500s
"""进程默认总预算（自进程起点）。一切阶段的死线都不得越过 起点+本值。"""

ENV_BUDGET_KEY = "QW_AGENT_BUDGET_S"
"""总预算覆盖环境变量（本地工具/调试用；平台环境不设置，走默认）。"""

_BUDGET_ENV_MIN_S = 120.0   # env 覆盖的合法下界（再低连确定性链都跑不完）
_BUDGET_ENV_MAX_S = TOTAL_BUDGET_DEFAULT_S  # env 只允许收紧，不允许放宽到安全垫内

_T0 = time.monotonic()
"""进程起点（模块首次 import 时刻；agent.py 在最前引入本模块）。"""

_total_s: float = TOTAL_BUDGET_DEFAULT_S
"""当前生效的总预算（init/init_from_env 可重设；测试可注入）。"""


class WallClockTimeout(TimeoutError):
    """call_with_wallclock 墙钟超时（被包裹调用未在限内返回）。"""


def init(total_s: Optional[float] = None, start: Optional[float] = None) -> None:
    """重设总预算/起点（默认参数 = 恢复出厂；测试用来构造时间场景）。"""
    global _T0, _total_s
    _T0 = time.monotonic() if start is None else float(start)
    _total_s = TOTAL_BUDGET_DEFAULT_S if total_s is None else float(total_s)


def init_from_env() -> None:
    """按 env QW_AGENT_BUDGET_S 收紧总预算（非法/越界值静默回落默认）。

    只在 agent.py 入口调用一次；t0 保持模块 import 时刻（≈进程启动），不重置。
    """
    raw = os.environ.get(ENV_BUDGET_KEY, "").strip()
    if not raw:
        return
    try:
        value = float(raw)
    except ValueError:
        return
    if not (_BUDGET_ENV_MIN_S <= value <= _BUDGET_ENV_MAX_S):
        return
    global _total_s
    _total_s = value


def start() -> float:
    """进程起点（time.monotonic 口径；BudgetManager 以 start_monotonic 接入）。"""
    return _T0


def total_s() -> float:
    """当前生效的总预算秒数（自起点）。"""
    return _total_s


def elapsed_s() -> float:
    """自进程起点已流逝秒数。"""
    return time.monotonic() - _T0


def remaining_s() -> float:
    """距进程级死线剩余秒数（可为负）。"""
    return _total_s - elapsed_s()


def deadline() -> float:
    """进程级死线（time.monotonic 口径）：一切模型调用与阶段不得越过。"""
    return _T0 + _total_s


def call_with_wallclock(fn: Callable[..., Any], timeout_s: float,
                        *args: Any, **kwargs: Any) -> Any:
    """在守护线程中执行 fn，主线程按墙钟等待至多 timeout_s 秒。

    - fn 正常返回 → 原样返回其结果；fn 抛异常 → 主线程原样抛出；
    - 超过 timeout_s 未返回 → raise WallClockTimeout（被放弃的调用留在
      守护线程内自生自灭，不 join、不 kill；进程退出即回收）。

    用途：requests 的 timeout 是逐 socket 读口径，慢流式响应可无限续命；
    本函数给出真正的「单次调用总墙钟」上限。timeout_s ≤ 0 时立即超时。
    """
    result: "queue.Queue[tuple[bool, Any]]" = queue.Queue()

    def _run() -> None:
        try:
            result.put_nowait((True, fn(*args, **kwargs)))
        except BaseException as exc:  # noqa: BLE001  异常原样送回主线程
            result.put_nowait((False, exc))

    worker = threading.Thread(target=_run, daemon=True)
    worker.start()
    try:
        ok, value = result.get(timeout=max(0.0, float(timeout_s)))
    except queue.Empty:
        raise WallClockTimeout(
            f"调用超过墙钟上限 {timeout_s:.0f}s 未返回（慢流式/挂起，按失败处理）") from None
    if ok:
        return value
    raise value
