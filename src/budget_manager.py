# -*- coding: utf-8 -*-
"""src/budget_manager.py — 时间/并发/Token 预算管理。

契约（contracts/interfaces.md §6）：
- 硬截止 1800s（官方：超时任务失败）；软截止 1680s（SPEC §2/§11）；
- 并发初始 2（config["concurrency"]）；Token 预算与请求速率控制分开实现；
- 降级顺序（SPEC §11）：normal → no_polish → no_review → core_only → template_only；
  禁止用「丢部分产品」降级。

职责划分（SPEC §11「Token 预算控制 + 请求速率控制分开实现」）：
- 本模块：token 总量记账与超限抛错（TokenBudgetExceeded，由上层捕获降级）、
  时间软/硬截止、并发槽位（threading.BoundedSemaphore）；
- 请求速率控制（429/5xx 退避重试、单请求超时）在 src/model_gateway.py 实现，
  通过本模块的 soft_remaining_seconds()/hard_expired() 感知剩余时间。

Token 预估口径：中文近似 len/2（向上取整），见 estimate_tokens()。
token 总量上限读 config["token_budget"]（可选键；缺省 None = 不设上限、只记账）。
降级水位（软截止窗口消耗比例 / token 预算消耗比例）为本项目自定阈值，
契约只固定阶段顺序，不固定阈值。
"""
from __future__ import annotations

import contextlib
import threading
import time
from typing import Iterator, Optional

DEGRADATION_STAGES = ("normal", "no_polish", "no_review", "core_only", "template_only")

DEFAULT_HARD_SECONDS = 1800.0
DEFAULT_SOFT_SECONDS = 1680.0
DEFAULT_CONCURRENCY = 2

# 时间水位：软截止窗口（soft_seconds）已消耗比例达到阈值即升阶；
# template_only（最高阶）在软截止到达（剩余 ≤0）或硬截止到达时触发。
_STAGE_TIME_FRACTIONS = (0.0, 0.45, 0.70, 0.90, None)  # None = 剩余≤0/硬超时
# token 水位：token 预算消耗比例达到阈值即升阶（未设预算时不因 token 升阶）。
_STAGE_TOKEN_FRACTIONS = (0.0, 0.70, 0.85, 0.95, 1.0)


class TokenBudgetExceeded(RuntimeError):
    """token 总量超预算。调用方应捕获并按 SPEC §11 降级（禁止丢部分产品）。"""


def estimate_tokens(text: str) -> int:
    """中文近似 token 预估：len/2 向上取整；空文本为 0。

    仅用于预算记账与降级水位（近似口径，不追求分词器精度）。
    """
    if not text:
        return 0
    return (len(text) + 1) // 2


def estimate_messages_tokens(messages: Optional[list[dict]]) -> int:
    """按 message content 累计的近似 token 预估（不含模板开销的保守下界）。"""
    total = 0
    for message in messages or []:
        if isinstance(message, dict):
            total += estimate_tokens(str(message.get("content") or ""))
    return total


class BudgetManager:
    """一次 run 的时间/并发/Token 预算守卫。"""

    def __init__(self, config: dict, start_monotonic: float | None = None) -> None:
        """记录起始时刻（默认 time.monotonic()），读取时限与并发配置。

        :param config: resolve_config 产出的配置
        :param start_monotonic: 起始单调时钟值（测试可注入）
        :raises ValueError: soft_seconds > hard_seconds、concurrency < 1、
            token_budget ≤ 0
        """
        self._config = config or {}
        time_limits = self._config.get("time_limits") or {}
        self._hard = float(time_limits.get("hard_seconds", DEFAULT_HARD_SECONDS))
        self._soft = float(time_limits.get("soft_seconds", DEFAULT_SOFT_SECONDS))
        if self._soft > self._hard:
            raise ValueError("软截止 soft_seconds 必须 ≤ 硬截止 hard_seconds")
        concurrency = int(self._config.get("concurrency", DEFAULT_CONCURRENCY))
        if concurrency < 1:
            raise ValueError("concurrency 必须 ≥ 1")
        self._concurrency = concurrency
        self._semaphore = threading.BoundedSemaphore(concurrency)
        token_budget = self._config.get("token_budget")
        self._token_budget = int(token_budget) if token_budget is not None else None
        if self._token_budget is not None and self._token_budget <= 0:
            raise ValueError("token_budget 必须 > 0（或缺省不设上限）")
        self._start = float(start_monotonic) if start_monotonic is not None else time.monotonic()
        self._lock = threading.Lock()
        self._prompt_tokens = 0
        self._completion_tokens = 0
        self._stage = 0  # DEGRADATION_STAGES 下标，只升不降

    # ---- 时间 ----

    def elapsed_seconds(self) -> float:
        """自起始时刻已流逝秒数（单调时钟）。"""
        return time.monotonic() - self._start

    def soft_remaining_seconds(self) -> float:
        """距软截止剩余秒数（可为负）。"""
        return self._soft - self.elapsed_seconds()

    def hard_expired(self) -> bool:
        """是否已超硬截止。"""
        return self.elapsed_seconds() >= self._hard

    # ---- 并发 ----

    def acquire_slot(self) -> contextlib.AbstractContextManager[None]:
        """获取一个并发槽（上下文管理器），槽位上限 = config["concurrency"]。

        用法::

            with budget.acquire_slot():
                gateway.chat(...)
        """
        return self._acquire_slot_cm()

    @contextlib.contextmanager
    def _acquire_slot_cm(self) -> Iterator[None]:
        self._semaphore.acquire()
        try:
            yield
        finally:
            self._semaphore.release()

    # ---- Token 记账 ----

    def register_tokens(self, prompt_tokens: int, completion_tokens: int) -> None:
        """累计 Token 用量（供预算控制与日志，速率控制由网关另行实现）。

        累计总量超过 config["token_budget"]（如设置）时 raise TokenBudgetExceeded；
        用量仍会被记录（水位反映真实消耗），由上层捕获后降级。
        """
        if prompt_tokens < 0 or completion_tokens < 0:
            raise ValueError("token 用量必须非负")
        with self._lock:
            self._prompt_tokens += int(prompt_tokens)
            self._completion_tokens += int(completion_tokens)
            total = self._prompt_tokens + self._completion_tokens
            budget = self._token_budget
        if budget is not None and total > budget:
            raise TokenBudgetExceeded(f"token 预算超限: 累计 {total} > 预算 {budget}")

    # ---- 只读状态（供日志/测试/上层降级判断） ----

    @property
    def concurrency(self) -> int:
        return self._concurrency

    @property
    def token_budget(self) -> Optional[int]:
        return self._token_budget

    @property
    def prompt_tokens(self) -> int:
        return self._prompt_tokens

    @property
    def completion_tokens(self) -> int:
        return self._completion_tokens

    @property
    def total_tokens(self) -> int:
        return self._prompt_tokens + self._completion_tokens

    # ---- 降级 ----

    def degradation_stage(self) -> str:
        """返回当前应处的降级阶段（DEGRADATION_STAGES 之一）。

        触发依据：软截止临近程度（时间水位）+ Token 预算水位（如已设预算），
        先到先升；阶段只升不降。禁止用「丢部分产品」降级（SPEC §11）。
        """
        with self._lock:
            current = self._stage
            total = self._prompt_tokens + self._completion_tokens
        computed = max(self._time_stage_index(), self._token_stage_index(total))
        target = max(current, computed)
        with self._lock:
            self._stage = target
        return DEGRADATION_STAGES[target]

    def _time_stage_index(self) -> int:
        if self.hard_expired() or self.soft_remaining_seconds() <= 0:
            return len(DEGRADATION_STAGES) - 1
        fraction = self.elapsed_seconds() / self._soft
        index = 0
        for stage_index, threshold in enumerate(_STAGE_TIME_FRACTIONS):
            if threshold is not None and fraction >= threshold:
                index = stage_index
        return index

    def _token_stage_index(self, total_tokens: int) -> int:
        if self._token_budget is None:
            return 0
        fraction = total_tokens / self._token_budget
        index = 0
        for stage_index, threshold in enumerate(_STAGE_TOKEN_FRACTIONS):
            if fraction >= threshold:
                index = stage_index
        return index
