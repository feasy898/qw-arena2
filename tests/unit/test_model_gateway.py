# -*- coding: utf-8 -*-
"""tests/unit/test_model_gateway.py — 模型网关单元测试（网络全 fake，不真发请求）。

覆盖（任务要求）：mock 取数、重试（429/5xx/网络异常，指数退避+抖动）、
致命错误分类（鉴权/模型不存在 → UpstreamFatalError，不重试）、上下文超限
（ContextTooLong → 调用方分块）、JSON 容错提取与一次「请修复 JSON」重试、
预算到期联动。monkeypatch gateway._session/_sleep/_rand 注入。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest
import requests

from src.budget_manager import BudgetManager
from src.model_gateway import (
    ContextTooLong,
    ContextTooLongError,
    GatewayError,
    MockResponseMissingError,
    ModelGateway,
    ModelNotWhitelistedError,
    UpstreamFatalError,
    extract_json_object,
    is_mock_mode,
    load_model_whitelist,
)

FIXTURE_PATH = Path(__file__).resolve().parent / "fixtures" / "gateway_responses.json"
PLATFORM_CONTRACT = Path(__file__).resolve().parents[2] / "contracts" / "platform_contract.json"
OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    """隔离环境变量：测试不依赖、不污染本机真实密钥。"""
    for name in ("DASHSCOPE_API_KEY", "DASHSCOPE_BASE_URL", "OPENAI_BASE_URL", "QW_FORCE_MOCK"):
        monkeypatch.delenv(name, raising=False)


def base_config(**overrides) -> dict:
    config = {
        "default_model": "qwen3.6-plus",
        "fallback_models": ["qwen3.6-flash"],
        "api_mode": "openai_compatible",
        "concurrency": 2,
        "max_retries_per_request": 2,
        "time_limits": {"hard_seconds": 1800, "soft_seconds": 1680},
        "mock": {"enabled": False, "force_env": "QW_FORCE_MOCK",
                 "fixture_path": str(FIXTURE_PATH)},
    }
    config.update(overrides)
    return config


class FakeResponse:
    def __init__(self, status_code=200, body=None, text=""):
        self.status_code = status_code
        self._body = body
        self.text = text or (json.dumps(body, ensure_ascii=False) if body is not None else "")

    def json(self):
        if self._body is None:
            raise ValueError("no json body")
        return self._body


def ok_response(content, usage=None):
    body = {"choices": [{"message": {"role": "assistant", "content": content}}]}
    if usage is not None:
        body["usage"] = usage
    return FakeResponse(200, body)


class FakeSession:
    """按序回放预设响应/异常；多发的请求直接断言失败。"""

    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = []

    def post(self, url, headers=None, json=None, timeout=None):
        self.calls.append({"url": url, "headers": headers, "json": json, "timeout": timeout})
        if not self.outcomes:
            raise AssertionError("FakeSession 收到超出预期的请求")
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


def make_real_gateway(monkeypatch, outcomes, config=None):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-unit-test-fake")
    monkeypatch.setenv("OPENAI_BASE_URL", OPENAI_BASE_URL)
    gateway = ModelGateway(config or base_config())
    fake = FakeSession(outcomes)
    gateway._session = fake
    sleeps: list[float] = []
    gateway._sleep = sleeps.append
    gateway._rand = lambda: 0.0
    return gateway, fake, sleeps


def _ok_body(content: str) -> dict:
    """构造最小合法 200 响应体（choices[0].message.content=content）。"""
    return {"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1, "completion_tokens": 1}}


# ---------- is_mock_mode ----------

def test_is_mock_mode_without_key(monkeypatch):
    assert is_mock_mode(base_config()) is True


def test_is_mock_mode_with_key_is_real(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-real")
    assert is_mock_mode(base_config()) is False


def test_is_mock_mode_key_literal_mock(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "mock")
    assert is_mock_mode(base_config()) is True


def test_is_mock_mode_force_env_wins(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-real")
    monkeypatch.setenv("QW_FORCE_MOCK", "1")
    assert is_mock_mode(base_config()) is True


def test_is_mock_mode_config_enabled(monkeypatch):
    config = base_config()
    config["mock"]["enabled"] = True
    assert is_mock_mode(config) is True


# ---------- 白名单 ----------

def test_whitelist_loaded_from_platform_contract():
    contract = json.loads(PLATFORM_CONTRACT.read_text(encoding="utf-8"))
    assert set(load_model_whitelist()) == set(contract["model_whitelist_text"])
    assert "qwen3.6-plus" in load_model_whitelist()


def test_model_not_whitelisted_rejected_in_mock_mode(monkeypatch):
    gateway = ModelGateway(base_config())
    with pytest.raises(ModelNotWhitelistedError):
        gateway.chat([{"role": "user", "content": "hi"}], "gpt-4o", mock_key="unit_greeting")


def test_model_not_whitelisted_rejected_in_real_mode(monkeypatch):
    gateway, fake, _ = make_real_gateway(monkeypatch, [])
    with pytest.raises(ModelNotWhitelistedError):
        gateway.chat([{"role": "user", "content": "hi"}], "not-a-model")
    assert fake.calls == []


# ---------- mock 模式 ----------

def test_mock_chat_returns_fixture_content_by_key(monkeypatch):
    gateway = ModelGateway(base_config())
    assert gateway.mock_mode is True
    out = gateway.chat([{"role": "user", "content": "你好"}], "qwen3.6-plus",
                       mock_key="unit_greeting")
    assert out == {"role": "assistant", "content": "{\"hello\": \"world\"}",
                   "model": "qwen3.6-plus", "mock": True, "usage": out["usage"]}
    assert out["usage"]["estimated"] is True
    assert out["usage"]["completion_tokens"] == (len(out["content"]) + 1) // 2


def test_mock_chat_unknown_key_raises(monkeypatch):
    gateway = ModelGateway(base_config())
    with pytest.raises(MockResponseMissingError):
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus",
                     mock_key="no_such_key")


def test_mock_chat_requires_mock_key(monkeypatch):
    gateway = ModelGateway(base_config())
    with pytest.raises(MockResponseMissingError):
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")


def test_mock_chat_bad_fixture_format_raises(monkeypatch):
    gateway = ModelGateway(base_config())
    with pytest.raises(MockResponseMissingError):
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus",
                     mock_key="unit_bad_format")


def test_mock_fixture_file_missing_raises(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)  # 相对路径在别处也解析不到 → 必须抛错，不静默兜底
    config = base_config()
    config["mock"]["fixture_path"] = "no/such/responses.json"
    gateway = ModelGateway(config)
    with pytest.raises(MockResponseMissingError):
        gateway.load_mock_fixture()


def test_mock_fixture_resolves_relative_to_repo_root(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    config = base_config()
    config["mock"]["fixture_path"] = "tests/unit/fixtures/gateway_responses.json"
    gateway = ModelGateway(config)
    fixture = gateway.load_mock_fixture()
    assert fixture["unit_greeting"]["content"] == "{\"hello\": \"world\"}"


# ---------- real 模式：请求形状与成功路径 ----------

def test_real_chat_request_shape_and_result(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch, [ok_response("{\"a\": 1}",
                                  usage={"prompt_tokens": 10, "completion_tokens": 5,
                                         "total_tokens": 15})])
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out == {"role": "assistant", "content": "{\"a\": 1}", "model": "qwen3.6-plus",
                   "mock": False,
                   "usage": {"prompt_tokens": 10, "completion_tokens": 5,
                             "total_tokens": 15, "estimated": False}}
    assert len(fake.calls) == 1
    call = fake.calls[0]
    assert call["url"] == f"{OPENAI_BASE_URL}/chat/completions"
    assert call["headers"]["Authorization"].startswith("Bearer ")
    assert call["json"] == {"model": "qwen3.6-plus",
                            "messages": [{"role": "user", "content": "hi"}]}
    assert 0 < call["timeout"] <= 120


def test_real_chat_usage_falls_back_to_estimate(monkeypatch):
    gateway, fake, _ = make_real_gateway(monkeypatch, [ok_response("你好世界")])
    out = gateway.chat([{"role": "user", "content": "abcd"}], "qwen3.6-plus")
    assert out["usage"]["estimated"] is True
    assert out["usage"]["completion_tokens"] == 2  # 中文近似 len/2 向上取整


def test_real_chat_usage_registered_into_budget(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch, [ok_response("ok", usage={"prompt_tokens": 100,
                                               "completion_tokens": 50})])
    gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert gateway._budget.total_tokens == 150


# ---------- real 模式：重试 ----------

def test_retry_on_429_then_success(monkeypatch):
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch, [FakeResponse(429, {"error": {"code": "Throttling",
                                                   "message": "Requests rate limit exceeded."}}),
                      ok_response("done")])
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out["content"] == "done"
    assert len(fake.calls) == 2
    assert len(sleeps) == 1 and sleeps[0] >= 1.0  # 指数退避 + 抖动


def test_retry_on_5xx_then_success(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch, [FakeResponse(503, {"error": {"message": "Service Unavailable"}}),
                      FakeResponse(502, {"error": {"message": "Bad Gateway"}}),
                      ok_response("recovered")])
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out["content"] == "recovered"
    assert len(fake.calls) == 3


def test_retry_exhausted_raises_gateway_error(monkeypatch):
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch, [FakeResponse(429, {"error": {"message": "rate limit"}}) for _ in range(3)])
    with pytest.raises(GatewayError) as excinfo:
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert "重试用尽" in str(excinfo.value)
    assert len(fake.calls) == 3      # 1 次初始 + max_retries_per_request=2 次重试
    assert len(sleeps) == 2          # 指数退避：1s, 2s（抖动注入为 0）


def test_network_exception_is_retried(monkeypatch):
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch, [requests.ConnectionError("boom"), ok_response("fine")])
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out["content"] == "fine"
    assert len(fake.calls) == 2 and len(sleeps) == 1


# ---------- real 模式：致命错误（不重试） ----------

@pytest.mark.parametrize("status,code", [(401, "InvalidApiKey"), (403, "AccessDenied")])
def test_fatal_status_no_retry(monkeypatch, status, code):
    # v0.3.0 起 404/模型不可用走候选链降级（见 test_model_404_falls_back），
    # 致命状态码仅剩鉴权类（401/403）。
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch, [FakeResponse(status, {"error": {"code": code,
                                                      "message": "fatal"}})])
    with pytest.raises(UpstreamFatalError):
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert len(fake.calls) == 1
    assert sleeps == []


def test_model_404_falls_back_to_next_candidate(monkeypatch):
    # v0.3.0（评测联调）：fallback_models 此前从未被使用，单模型不可用=全局失败。
    # 现在 404/模型不可用标记 → 降级到候选链下一个模型继续（不消耗重试次数）。
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch,
        [FakeResponse(404, {"error": {"code": "Model.NotFound",
                                      "message": "model not found"}}),
         FakeResponse(200, _ok_body("{\"ok\": 1}"))])
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out["model"] == "qwen3.6-flash" and out["content"] == "{\"ok\": 1}"
    assert len(fake.calls) == 2 and sleeps == []
    # 本会话内已降级模型不再作为首选：再次调用直接从候选模型开始
    fake.outcomes.append(FakeResponse(200, _ok_body("x")))
    gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert fake.calls[-1]["json"]["model"] == "qwen3.6-flash"


def test_model_chain_exhausted_raises_gateway_error(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch,
        [FakeResponse(404, {"error": {"code": "Model.NotFound",
                                      "message": "model not found"}}),
         FakeResponse(404, {"error": {"code": "Model.NotFound",
                                      "message": "model not found"}})])
    with pytest.raises(GatewayError) as excinfo:
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert "候选链耗尽" in str(excinfo.value) or "模型不可用" in str(excinfo.value)


def test_fatal_body_marker_no_retry(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch, [FakeResponse(400, {"error": {"code": "Arrearage",
                                                   "message": "The account is in arrears."}})])
    with pytest.raises(UpstreamFatalError):
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert len(fake.calls) == 1


def test_error_message_never_contains_api_key(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch, [FakeResponse(401, {"error": {"code": "InvalidApiKey",
                                                   "message": "Invalid API-key provided."}})])
    with pytest.raises(UpstreamFatalError) as excinfo:
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert "sk-unit-test-fake" not in str(excinfo.value)


# ---------- 上下文超限 / 其他 4xx ----------

def test_context_too_long_no_retry(monkeypatch):
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch, [FakeResponse(400, {"error": {
            "code": "InvalidParameter",
            "message": "the input length exceeds the maximum context length (6144 tokens)"}})])
    with pytest.raises(ContextTooLongError) as excinfo:
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert isinstance(excinfo.value, GatewayError)
    assert ContextTooLong is ContextTooLongError
    assert len(fake.calls) == 1 and sleeps == []


def test_param_reject_400_suppresses_extras_and_retries_minimal(monkeypatch):
    # v0.3.0（评测联调）：400 拒绝附加参数（enable_thinking）不再致命——本会话
    # 停用附加参数并以最小载荷免费重试一次；此后所有请求直接最小载荷。
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch,
        [FakeResponse(400, {"error": {"code": "InvalidParameter",
                                      "message": "unknown parameter 'enable_thinking'"}}),
         FakeResponse(200, _ok_body("{\"ok\": 1}"))],
        config=base_config(enable_thinking=False))
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out["content"] == "{\"ok\": 1}"
    assert len(fake.calls) == 2 and sleeps == []
    assert "enable_thinking" in fake.calls[0]["json"]
    assert "enable_thinking" not in fake.calls[1]["json"]
    fake.outcomes.append(FakeResponse(200, _ok_body("x")))
    gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert "enable_thinking" not in fake.calls[-1]["json"]


# ---------- 预算联动 ----------

def test_hard_deadline_aborts_before_request(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-unit-test-fake")
    monkeypatch.setenv("OPENAI_BASE_URL", OPENAI_BASE_URL)
    config = base_config()
    budget = BudgetManager(config, start_monotonic=__import__("time").monotonic() - 1801)
    gateway = ModelGateway(config, budget=budget)
    fake = FakeSession([])
    gateway._session = fake
    with pytest.raises(GatewayError) as excinfo:
        gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert "硬截止" in str(excinfo.value)
    assert fake.calls == []


def test_request_timeout_clamped_to_soft_remaining(monkeypatch):
    import time as time_mod
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-unit-test-fake")
    monkeypatch.setenv("OPENAI_BASE_URL", OPENAI_BASE_URL)
    config = base_config()
    budget = BudgetManager(config, start_monotonic=time_mod.monotonic() - 1600)
    gateway = ModelGateway(config, budget=budget)
    fake = FakeSession([ok_response("ok")])
    gateway._session = fake
    gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    timeout = fake.calls[0]["timeout"]
    assert 70 < timeout <= 81  # 软剩余 ≈80s，上限 120s 不生效


# ---------- JSON 容错提取与「请修复 JSON」重试 ----------

def test_extract_json_object_variants():
    assert extract_json_object('{"a": 1}') == {"a": 1}
    assert extract_json_object('```json\n{"a": 1}\n```') == {"a": 1}
    assert extract_json_object('结果如下：{"a": [1, 2]} 以上。') == {"a": [1, 2]}
    with pytest.raises(ValueError):
        extract_json_object('残缺对象：{"a": {"b": 2]}')
    with pytest.raises(ValueError):
        extract_json_object("完全没有 JSON")
    with pytest.raises(ValueError):
        extract_json_object("")


def test_chat_json_real_repairs_once(monkeypatch):
    gateway, fake, sleeps = make_real_gateway(
        monkeypatch, [ok_response("好的，结果：{\"a\": [1,2"),
                      ok_response("{\"a\": [1, 2]}")])
    out = gateway.chat_json([{"role": "user", "content": "给 JSON"}], "qwen3.6-plus")
    assert out == {"a": [1, 2]}
    assert len(fake.calls) == 2 and sleeps == []
    repair_messages = fake.calls[1]["json"]["messages"]
    assert repair_messages[0] == {"role": "user", "content": "给 JSON"}
    assert repair_messages[-2]["role"] == "assistant"
    assert repair_messages[-1]["role"] == "user"
    assert "JSON" in repair_messages[-1]["content"]


def test_chat_json_real_fails_after_single_repair(monkeypatch):
    gateway, fake, _ = make_real_gateway(
        monkeypatch, [ok_response("still { not json"),
                      ok_response("still { not json")])
    with pytest.raises(GatewayError) as excinfo:
        gateway.chat_json([{"role": "user", "content": "给 JSON"}], "qwen3.6-plus")
    assert "两次" in str(excinfo.value)
    assert len(fake.calls) == 2  # 只修复重试一次，不多重试


def test_chat_json_mock_bad_content_raises_without_repair(monkeypatch):
    gateway = ModelGateway(base_config())
    with pytest.raises(GatewayError) as excinfo:
        gateway.chat_json([{"role": "user", "content": "hi"}], "qwen3.6-plus",
                          mock_key="unit_bad_json")
    assert "unit_bad_json" in str(excinfo.value)


def test_chat_json_mock_fenced_content_parses(monkeypatch):
    gateway = ModelGateway(base_config())
    out = gateway.chat_json([{"role": "user", "content": "hi"}], "qwen3.6-plus",
                            mock_key="unit_fenced_json")
    assert out == {"ok": True}


# ---------- 输入校验 ----------

def test_chat_rejects_empty_or_malformed_messages(monkeypatch):
    gateway = ModelGateway(base_config())
    with pytest.raises(GatewayError):
        gateway.chat([], "qwen3.6-plus", mock_key="unit_greeting")
    with pytest.raises(GatewayError):
        gateway.chat([{"role": "user"}], "qwen3.6-plus", mock_key="unit_greeting")


def test_real_mode_requires_openai_base_url_suffix(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-unit-test-fake")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode")
    with pytest.raises(GatewayError) as excinfo:
        ModelGateway(base_config())
    assert "/v1" in str(excinfo.value)


def test_real_mode_requires_api_key(monkeypatch):
    monkeypatch.setenv("OPENAI_BASE_URL", OPENAI_BASE_URL)
    # DASHSCOPE_API_KEY 缺失时构造即判为 mock（is_mock_mode），故 real 缺 Key 只能由
    # 键值非法暴露：这里验证 mock 判定优先，网关构造不报错。
    gateway = ModelGateway(base_config())
    assert gateway.mock_mode is True


# ---------- v0.4.4：base_url 候选切换（平台评测联调） ----------

def test_connection_error_switches_base_url_candidate(monkeypatch):
    """env 指向不可达地址 → 连接失败切换官方默认候选（不消耗重试次数）后成功。"""
    monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-unit-test-fake")
    monkeypatch.setenv("OPENAI_BASE_URL", "https://env-fake-gateway.invalid/v1")
    gateway = ModelGateway(base_config())
    fake = FakeSession([requests.exceptions.ConnectionError("refused"),
                        FakeResponse(200, _ok_body("{\"ok\": 1}"))])
    gateway._session = fake
    sleeps: list[float] = []
    gateway._sleep = sleeps.append
    gateway._rand = lambda: 0.0
    out = gateway.chat([{"role": "user", "content": "hi"}], "qwen3.6-plus")
    assert out["content"] == "{\"ok\": 1}"
    assert len(fake.calls) == 2 and sleeps == []
    # 两次调用的 url 不同（第二个是官方默认候选）
    assert fake.calls[0]["url"] != fake.calls[1]["url"]
    assert fake.calls[1]["url"].endswith("/chat/completions")
