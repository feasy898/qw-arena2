# -*- coding: utf-8 -*-
"""tests/unit/test_budget_manager.py — 时间/并发/Token 预算单元测试。

覆盖（任务要求）：软/硬截止（可注入 start_monotonic）、并发信号量（默认 2）、
token 预估（中文近似 len/2）与总量控制（超预算抛 TokenBudgetExceeded）、
降级阶段只升不降、预算到期。
"""
from __future__ import annotations

import threading
import time

import pytest

from src.budget_manager import (
    DEGRADATION_STAGES,
    BudgetManager,
    TokenBudgetExceeded,
    estimate_messages_tokens,
    estimate_tokens,
)


def config_with(soft=1680, hard=1800, concurrency=2, token_budget=None) -> dict:
    config = {"concurrency": concurrency,
              "time_limits": {"hard_seconds": hard, "soft_seconds": soft}}
    if token_budget is not None:
        config["token_budget"] = token_budget
    return config


# ---------- token 预估 ----------

def test_estimate_tokens_chinese_approx_half():
    assert estimate_tokens("") == 0
    assert estimate_tokens("你好世界") == 2      # 4 字 → 2
    assert estimate_tokens("三个字") == 2        # 3 字 → ceil(1.5)=2
    assert estimate_tokens("abcdef") == 3        # 6 字符 → 3
    assert estimate_tokens("a") == 1


def test_estimate_messages_tokens_sums_contents():
    messages = [{"role": "system", "content": "你好"},  # (2+1)//2 = 1
                {"role": "user", "content": "世界你好呀"},  # ceil(5/2)=3
                {"role": "assistant"}]  # 无 content → 0
    assert estimate_messages_tokens(messages) == 4


# ---------- 时间预算 ----------

def test_soft_remaining_uses_injected_monotonic_start():
    start = time.monotonic()
    budget = BudgetManager(config_with(), start_monotonic=start)
    assert 1670 <= budget.soft_remaining_seconds() <= 1680
    budget = BudgetManager(config_with(), start_monotonic=start - 100)
    assert 1570 <= budget.soft_remaining_seconds() <= 1580


def test_hard_expiry():
    start = time.monotonic()
    assert BudgetManager(config_with(), start_monotonic=start - 1800).hard_expired() is True
    assert BudgetManager(config_with(), start_monotonic=start - 1799).hard_expired() is False
    assert BudgetManager(config_with(), start_monotonic=start).hard_expired() is False


def test_defaults_without_config_keys():
    budget = BudgetManager({})  # 无 time_limits/concurrency 键 → 默认 1800/1680/2
    assert budget.soft_remaining_seconds() > 1600
    assert budget.hard_expired() is False
    assert budget.concurrency == 2
    assert budget.token_budget is None


def test_invalid_config_raises():
    with pytest.raises(ValueError):
        BudgetManager(config_with(soft=1900, hard=1800))
    with pytest.raises(ValueError):
        BudgetManager(config_with(concurrency=0))
    with pytest.raises(ValueError):
        BudgetManager(config_with(token_budget=0))


# ---------- 并发槽 ----------

def test_acquire_slot_is_context_manager():
    budget = BudgetManager(config_with(concurrency=2))
    with budget.acquire_slot():
        pass  # 正常进出即合格


def test_acquire_slot_blocks_at_concurrency_limit():
    budget = BudgetManager(config_with(concurrency=2))
    entered = threading.Event()
    release = threading.Event()

    def worker():
        with budget.acquire_slot():
            entered.set()
            release.wait(timeout=5)

    with budget.acquire_slot(), budget.acquire_slot():
        thread = threading.Thread(target=worker, daemon=True)
        thread.start()
        assert not entered.wait(timeout=0.3), "第 3 个槽位不应被立即获取（并发=2）"
    # 先退出 with 释放两个槽位，再等 worker 完成（避免主线程占槽导致的自锁）
    release.set()
    thread.join(timeout=5)
    assert entered.is_set(), "释放槽位后 worker 应已进入"
    assert not thread.is_alive(), "释放槽位后 worker 应完成"
    # 全部释放后可再次获取（信号量无泄漏）
    with budget.acquire_slot():
        pass


# ---------- token 记账与总量控制 ----------

def test_register_tokens_accumulates():
    budget = BudgetManager(config_with())
    budget.register_tokens(10, 5)
    budget.register_tokens(3, 2)
    assert budget.prompt_tokens == 13
    assert budget.completion_tokens == 7
    assert budget.total_tokens == 20


def test_register_tokens_rejects_negative():
    budget = BudgetManager(config_with())
    with pytest.raises(ValueError):
        budget.register_tokens(-1, 0)


def test_token_budget_exceeded_raises_and_records():
    budget = BudgetManager(config_with(token_budget=100))
    budget.register_tokens(60, 30)  # 90 ≤ 100
    with pytest.raises(TokenBudgetExceeded):
        budget.register_tokens(20, 0)  # 110 > 100
    assert budget.total_tokens == 110  # 超限用量仍记账（水位反映真实消耗）


def test_no_token_budget_means_accounting_only():
    budget = BudgetManager(config_with())
    budget.register_tokens(10**9, 0)  # 未设预算 → 不抛，仅记账
    assert budget.total_tokens == 10**9


# ---------- 降级阶段 ----------

def test_degradation_time_progression():
    start = time.monotonic()
    soft = 100
    stages = [
        (0, "normal"),        # 刚启动
        (44, "normal"),       # 时间水位 45% 未到
        (45, "no_polish"),    # ≥45%
        (70, "no_review"),    # ≥70%
        (90, "core_only"),    # ≥90%
        (100, "template_only"),  # 软截止到达
        (205, "template_only"),  # 硬截止到达
    ]
    for elapsed, expected in stages:
        budget = BudgetManager(config_with(soft=soft, hard=200),
                               start_monotonic=start - elapsed)
        assert budget.degradation_stage() == expected, f"elapsed={elapsed}"


def test_degradation_token_watermark_progression():
    budget = BudgetManager(config_with(token_budget=100))  # 时间水位为 0
    assert budget.degradation_stage() == "normal"
    budget.register_tokens(70, 0)
    assert budget.degradation_stage() == "no_polish"    # ≥70%
    budget.register_tokens(15, 0)
    assert budget.degradation_stage() == "no_review"    # ≥85%
    budget.register_tokens(10, 0)
    assert budget.degradation_stage() == "core_only"    # ≥95%
    budget.register_tokens(5, 0)
    assert budget.degradation_stage() == "template_only"  # ≥100%


def test_degradation_stage_only_rises():
    start = time.monotonic()
    budget = BudgetManager(config_with(soft=100, hard=200, token_budget=100),
                           start_monotonic=start - 90)
    assert budget.degradation_stage() == "core_only"
    budget.register_tokens(100, 0)  # token 也到 100%
    assert budget.degradation_stage() == "template_only"
    # 阶段只升不降：token 与时间都不回退，重复读取应稳定在最高阶
    for _ in range(3):
        assert budget.degradation_stage() == "template_only"


def test_degradation_stages_order_constant():
    assert DEGRADATION_STAGES == ("normal", "no_polish", "no_review",
                                  "core_only", "template_only")
