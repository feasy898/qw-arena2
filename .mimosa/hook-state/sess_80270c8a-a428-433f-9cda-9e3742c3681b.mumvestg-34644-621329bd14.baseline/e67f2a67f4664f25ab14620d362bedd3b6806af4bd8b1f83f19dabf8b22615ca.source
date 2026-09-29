# -*- coding: utf-8 -*-
"""tests/unit/test_gateway_calibration.py — 真实网关校准行为（第二阶段；网络全 fake）。

覆盖（任务「网关校准」三项）：
- 默认不发送 max_tokens（请求体恰为 {model, messages}）；
- content 为空且 finish_reason=length → EmptyContentError（含模型名与 finish_reason），
  且该次 usage 仍如实记账（token 已消耗）；
- chat_json 对空 content 做一次降级重试：重试 messages 前加「直接输出 JSON、
  不要长思考」指令；重试后仍空/仍非 JSON → 清晰 GatewayError，不二次重试；
- usage（prompt/completion/total tokens、请求次数）透传并累计到网关实例
  usage_totals，供工具汇总成本。

夹具经 fake session 注入（monkeypatch gateway._session），不真发请求；
helpers 复用 test_model_gateway（FakeSession/FakeResponse/base_config 等）。
"""
from __future__ import annotations

import pytest

from src.model_gateway import (
    EmptyContentError,
    GatewayError,
    JSON_DIRECT_OUTPUT_PROMPT,
    ModelGateway,
)

from tests.unit.test_model_gateway import (
    FakeResponse,
    base_config,
    make_real_gateway,
    ok_response,
)

MODEL = "qwen3.6-plus"


def empty_content_response(finish_reason="length", usage=None, content=None):
    """真实网关实测形态：思考耗尽 token → content 空、finish_reason=length。"""
    body = {"choices": [{"message": {"role": "assistant", "content": content},
                         "finish_reason": finish_reason}]}
    if usage is not None:
        body["usage"] = usage
    return FakeResponse(200, body)


# ---------- 默认不发送 max_tokens ----------

class TestNoMaxTokensByDefault:
    def test_request_payload_has_no_max_tokens(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(monkeypatch, [ok_response("ok")])
        gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        payload = fake.calls[0]["json"]
        assert "max_tokens" not in payload
        assert set(payload) == {"model", "messages"}

    def test_retry_after_empty_content_also_has_no_max_tokens(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [empty_content_response(), ok_response("{\"a\": 1}")])
        out = gateway.chat_json([{"role": "user", "content": "给 JSON"}], MODEL)
        assert out == {"a": 1}
        for call in fake.calls:
            assert "max_tokens" not in call["json"]


# ---------- content 为空 + finish_reason=length：清晰错误 ----------

class TestEmptyContentError:
    def test_chat_raises_with_model_and_finish_reason(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(monkeypatch, [empty_content_response()])
        with pytest.raises(EmptyContentError) as excinfo:
            gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        assert isinstance(excinfo.value, GatewayError)
        assert MODEL in str(excinfo.value)
        assert "length" in str(excinfo.value)
        assert excinfo.value.finish_reason == "length"
        assert len(fake.calls) == 1  # chat 本身不重试，重试属 chat_json 路径

    def test_content_none_also_raises(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [empty_content_response(content=None, finish_reason="length")])
        with pytest.raises(EmptyContentError):
            gateway.chat([{"role": "user", "content": "hi"}], MODEL)

    def test_empty_content_with_stop_also_raises(self, monkeypatch):
        gateway, _, _ = make_real_gateway(
            monkeypatch, [empty_content_response(finish_reason="stop")])
        with pytest.raises(EmptyContentError) as excinfo:
            gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        assert "stop" in str(excinfo.value)

    def test_error_never_contains_api_key(self, monkeypatch):
        gateway, _, _ = make_real_gateway(monkeypatch, [empty_content_response()])
        with pytest.raises(EmptyContentError) as excinfo:
            gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        assert "sk-unit-test-fake" not in str(excinfo.value)

    def test_usage_still_registered_on_empty_content(self, monkeypatch):
        """token 已消耗：空 content 响应的 usage 必须如实记账（费用纪律）。"""
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [empty_content_response(
                usage={"prompt_tokens": 12, "completion_tokens": 0, "total_tokens": 12})])
        with pytest.raises(EmptyContentError):
            gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        assert gateway._budget.total_tokens == 12
        assert gateway.usage_totals == {"prompt_tokens": 12, "completion_tokens": 0,
                                        "total_tokens": 12, "requests": 1}


# ---------- chat_json：一次降级重试（直接输出 JSON、不要长思考） ----------

class TestChatJsonDegradedRetry:
    def test_retries_once_with_prepended_directive(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [empty_content_response(), ok_response("{\"a\": [1, 2]}")])
        messages = [{"role": "user", "content": "给 JSON"}]
        out = gateway.chat_json(messages, MODEL)
        assert out == {"a": [1, 2]}
        assert len(fake.calls) == 2
        retry_messages = fake.calls[1]["json"]["messages"]
        # 重试 messages 前加一句指令：明确要求直接输出 JSON、不要长思考
        assert retry_messages[0] == {"role": "system", "content": JSON_DIRECT_OUTPUT_PROMPT}
        assert "JSON" in JSON_DIRECT_OUTPUT_PROMPT and "长思考" in JSON_DIRECT_OUTPUT_PROMPT
        assert retry_messages[1:] == messages

    def test_retry_still_empty_raises_without_second_retry(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [empty_content_response(), empty_content_response()])
        with pytest.raises(GatewayError) as excinfo:
            gateway.chat_json([{"role": "user", "content": "给 JSON"}], MODEL)
        assert "仍为空" in str(excinfo.value)
        assert "降级重试" in str(excinfo.value)
        assert len(fake.calls) == 2  # 只降级重试一次

    def test_retry_non_json_raises(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [empty_content_response(), ok_response("抱歉，无法输出")])
        with pytest.raises(GatewayError) as excinfo:
            gateway.chat_json([{"role": "user", "content": "给 JSON"}], MODEL)
        assert "降级重试" in str(excinfo.value)
        assert len(fake.calls) == 2

    def test_mock_mode_unaffected_by_empty_content_path(self):
        """mock 夹具行为不变：content 合法即返回，不触发空 content 重试路径。"""
        gateway = ModelGateway(base_config())
        out = gateway.chat_json([{"role": "user", "content": "hi"}], MODEL,
                                mock_key="unit_greeting")
        assert out == {"hello": "world"}


# ---------- usage 透传并累计到网关实例 ----------

class TestUsageTotals:
    def test_totals_accumulate_across_mock_calls(self):
        gateway = ModelGateway(base_config())
        first = gateway.chat([{"role": "user", "content": "hi"}], MODEL,
                             mock_key="unit_greeting")
        second = gateway.chat([{"role": "user", "content": "hi"}], MODEL,
                              mock_key="unit_greeting")
        totals = gateway.usage_totals
        assert totals["requests"] == 2
        assert totals["prompt_tokens"] == first["usage"]["prompt_tokens"] * 2
        assert totals["completion_tokens"] == \
            first["usage"]["completion_tokens"] + second["usage"]["completion_tokens"]
        assert totals["total_tokens"] == \
            first["usage"]["total_tokens"] + second["usage"]["total_tokens"]

    def test_totals_accumulate_in_real_mode(self, monkeypatch):
        gateway, fake, _ = make_real_gateway(
            monkeypatch, [ok_response("a", usage={"prompt_tokens": 10,
                                                  "completion_tokens": 5,
                                                  "total_tokens": 15}),
                          ok_response("bb", usage={"prompt_tokens": 7,
                                                   "completion_tokens": 3,
                                                   "total_tokens": 10})])
        gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        gateway.chat([{"role": "user", "content": "hi"}], MODEL)
        assert gateway.usage_totals == {"prompt_tokens": 17, "completion_tokens": 8,
                                        "total_tokens": 25, "requests": 2}

    def test_totals_snapshot_is_a_copy(self):
        gateway = ModelGateway(base_config())
        snapshot = gateway.usage_totals
        snapshot["requests"] = 999
        assert gateway.usage_totals["requests"] == 0
