# -*- coding: utf-8 -*-
"""timekeeper 单元测试：全局时钟口径、env 覆盖、call_with_wallclock 墙钟语义。"""
from __future__ import annotations

import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import timekeeper  # noqa: E402


def test_default_budget_and_monotonic_clock():
    assert timekeeper.total_s() == timekeeper.TOTAL_BUDGET_DEFAULT_S == 1500.0
    assert timekeeper.PLATFORM_HARD_LIMIT_S == 1800.0
    assert timekeeper.remaining_s() > 1490.0  # 夹具刚重置，几乎满预算
    assert timekeeper.deadline() == timekeeper.start() + timekeeper.total_s()
    assert 0 <= timekeeper.elapsed_s() < 5.0


def test_init_custom_start_and_total():
    past = time.monotonic() - 100.0
    timekeeper.init(total_s=150.0, start=past)
    assert 49.0 < timekeeper.remaining_s() < 51.0
    timekeeper.init(total_s=50.0, start=past)  # 已超死线
    assert timekeeper.remaining_s() < 0


def test_init_from_env_tightens_only(monkeypatch):
    monkeypatch.setenv("QW_AGENT_BUDGET_S", "900")
    timekeeper.init_from_env()
    assert timekeeper.total_s() == 900.0
    # 非法值/越界值静默回落：不动当前预算
    for bad in ("abc", "-5", "30", "99999", ""):
        monkeypatch.setenv("QW_AGENT_BUDGET_S", bad)
        timekeeper.init_from_env()
        assert timekeeper.total_s() == 900.0, bad
    monkeypatch.delenv("QW_AGENT_BUDGET_S")
    timekeeper.init_from_env()
    assert timekeeper.total_s() == 900.0  # 未设置 = 不重置（t0/预算保持）


def test_call_with_wallclock_passthrough_result_and_exception():
    assert timekeeper.call_with_wallclock(lambda a, b: a + b, 5.0, 2, 3) == 5

    def _boom():
        raise ValueError("原样抛回")

    with pytest.raises(ValueError, match="原样抛回"):
        timekeeper.call_with_wallclock(_boom, 5.0)


def test_call_with_wallclock_caps_slow_call():
    """慢调用被墙钟截断：被包裹函数还在睡，主线程已按时抛 WallClockTimeout。"""
    started = time.monotonic()
    with pytest.raises(timekeeper.WallClockTimeout):
        timekeeper.call_with_wallclock(time.sleep, 0.3, 30)
    elapsed = time.monotonic() - started
    assert elapsed < 5.0  # 绝不陪跑被放弃的调用


def test_call_with_wallclock_zero_timeout():
    # timeout=0 对「必然完不成的慢调用」立即超时（用瞬时函数则有调度竞态）
    with pytest.raises(timekeeper.WallClockTimeout):
        timekeeper.call_with_wallclock(time.sleep, 0.0, 5)


def test_call_with_wallclock_abandoned_thread_is_daemon():
    """被放弃的调用留在守护线程：进程退出即回收，且结果入队不阻塞。"""
    done = threading.Event()

    def _slow():
        time.sleep(0.5)
        done.set()
        return 1

    with pytest.raises(timekeeper.WallClockTimeout):
        timekeeper.call_with_wallclock(_slow, 0.1)
    assert done.wait(timeout=3.0)  # 守护线程随后自行跑完（不泄漏悬挂）


def test_pipeline_budget_clamped_to_global_clock():
    """降级链预算钳到全局时钟：hard=全局总预算−收尾预留，soft=min(config, hard−120)，
    起点继承 timekeeper（直出链已消耗的时间如实计入，不再新开时钟）。"""
    from src.pipeline import _new_budget
    timekeeper.init(total_s=1500.0)
    config = {"time_limits": {"hard_seconds": 1800, "soft_seconds": 1680},
              "concurrency": 2}
    budget = _new_budget(config)
    assert budget._hard == 1470.0   # 1500 − 30 收尾预留
    assert budget._soft == 1350.0   # min(1680, 1470−120)
    # 起点继承：timekeeper 起点回拨 100s → 预算已耗 ~100s
    timekeeper.init(total_s=1500.0, start=time.monotonic() - 100.0)
    budget = _new_budget(config)
    assert 99.0 < budget.elapsed_seconds() < 105.0


def test_pipeline_budget_unclamped_when_global_budget_huge():
    """全局预算大于平台契约口径时，config 的 1680/1800 原样生效（本地工具直跑）。"""
    from src.pipeline import _new_budget
    timekeeper.init(total_s=100000.0)
    config = {"time_limits": {"hard_seconds": 1800, "soft_seconds": 1680},
              "concurrency": 2}
    budget = _new_budget(config)
    assert budget._hard == 1800.0 and budget._soft == 1680.0
