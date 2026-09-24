# -*- coding: utf-8 -*-
"""src/model_gateway.py — 模型网关（real / mock 双模式）。

契约（contracts/interfaces.md §5、platform_contract.json）：
- 模型必须 ∈ platform_contract.model_whitelist_text，否则 ModelNotWhitelistedError；
- mock 模式确定性：按 mock_key 从 tests/fixtures/gateway/responses.json 取
  {"role": "assistant", "content": "<JSON字符串>"}；mock_key 缺失必须抛
  MockResponseMissingError，禁止任何静默兜底；
- real 模式仅用 requests 调 {OPENAI_BASE_URL}/chat/completions；
- 429/部分 5xx 有限重试 ≤ config["max_retries_per_request"]（指数退避+抖动）；
  鉴权失败/模型未授权/预算耗尽 → UpstreamFatalError（不盲目重试）；
- 上下文超限 → ContextTooLong（GatewayError 子类），由调用方缩小分块。

JSON 解析的职责边界（契约 §5「本网关不解析业务 JSON」）：chat() 只透传模型原文；
需要「容错提取 JSON + 一次『请修复 JSON』重试」的调用方使用 chat_json()（本模块
提供，内部复用 chat()），或自行对 content 调用 extract_json_object()。

速率控制分工：单请求超时（受 budget_manager 软截止约束）与 429/5xx 指数退避
在本模块实现；token 总量记账/并发槽位/时间截止由 src/budget_manager.py 实现。

真实网关校准（第二阶段，2026-09）：默认不发送 max_tokens；content 为空
（finish_reason=length 等）→ EmptyContentError（含模型名与 finish_reason），
chat_json 捕获后做一次「直接输出 JSON、不要长思考」降级重试；usage 透传并
累计到实例 usage_totals（供工具汇总成本），日志只记 token 数不记内容。

安全：密钥只从环境变量读取，不写日志/文档/代码；日志不打印消息原文。
"""
from __future__ import annotations

import json
import logging
import os
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

import requests

from src.budget_manager import BudgetManager, estimate_messages_tokens, estimate_tokens

logger = logging.getLogger("qw.model_gateway")

REPO_ROOT = Path(__file__).resolve().parent.parent
PLATFORM_CONTRACT_PATH = REPO_ROOT / "contracts" / "platform_contract.json"
DEFAULT_MOCK_FIXTURE_PATH = "tests/fixtures/gateway/responses.json"
DEFAULT_REQUEST_TIMEOUT_SECONDS = 120.0
RETRY_BACKOFF_BASE_SECONDS = 1.0

# 可重试状态码：429 限流、408 超时、网关类 5xx（501 Not Implemented 不重试）
RETRYABLE_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})
# 致命状态码：鉴权失败（401/403）、模型不存在/未授权（404）→ 不重试
FATAL_STATUS_CODES = frozenset({401, 403, 404})

_FATAL_BODY_MARKERS = (
    "invalid api key", "invalid_api_key", "invalidapikey", "unauthorized",
    "not authorized", "access denied", "forbidden", "arrearage", "in arrears",
    "model not found", "model.notfound", "model.not_exists", "model not exist",
    "model not exists", "modelnotexist",
)
_CONTEXT_OVERFLOW_MARKERS = (
    "context length", "maximum context", "max context", "input length",
    "length limit", "length exceed", "exceeds the limit", "too long",
    "too many tokens", "prompt is too long",
)
_CODE_FENCE_RE = re.compile(r"```[a-zA-Z0-9_-]*\s*(.*?)\s*```", re.DOTALL)

JSON_REPAIR_PROMPT = (
    "上一条回答无法解析为 JSON。请修复 JSON：只输出一个合法的 JSON 对象，"
    "不要使用 Markdown 代码块围栏，不要附加任何解释文字。"
)

JSON_DIRECT_OUTPUT_PROMPT = (
    "请直接给出最终答案：只输出一个合法的 JSON 对象作为回答的全部内容，"
    "不要进行长思考/推理过程，不要输出思考文字，不要使用 Markdown 代码块围栏，"
    "不要附加任何解释文字。"
)


class GatewayError(Exception):
    """模型网关错误基类。"""


class MockResponseMissingError(GatewayError):
    """mock 模式下 mock_key 在夹具中缺失（或夹具文件缺失）。禁止静默兜底。"""


class ModelNotWhitelistedError(GatewayError):
    """请求的模型不在 platform_contract.model_whitelist_text 白名单内。"""


class UpstreamFatalError(GatewayError):
    """不可恢复上游错误：鉴权失败/模型未授权/预算耗尽。停止该路径并报告。"""


class ContextTooLongError(GatewayError):
    """上下文超限：由调用方缩小分块后重试（契约 §5「抛 GatewayError 子类」）。"""


ContextTooLong = ContextTooLongError
"""别名：任务细则使用的名称 ContextTooLong（契约未固定该子类名）。"""


class EmptyContentError(GatewayError):
    """200 响应的 content 为空/缺失（真实网关实测：显式限制过小的 max_tokens 时，
    思考（reasoning_content）耗尽 token，content 为空、finish_reason=length）。

    - 错误信息含模型名与 finish_reason，便于定位；密钥与消息原文不进错误信息；
    - 该响应的 usage 仍如实记账（token 已消耗）；
    - chat() 直接抛出；chat_json 捕获后做一次降级重试（要求直接输出 JSON、
      不要长思考），见 JSON_DIRECT_OUTPUT_PROMPT。
    """

    def __init__(self, message: str, finish_reason: Optional[str] = None) -> None:
        super().__init__(message)
        self.finish_reason = finish_reason


def _platform_contract() -> dict:
    """读取 contracts/platform_contract.json（缓存）。缺失/非法 raise GatewayError。"""
    global _CONTRACT_CACHE
    if _CONTRACT_CACHE is None:
        path = PLATFORM_CONTRACT_PATH
        if not path.is_file():
            raise GatewayError(f"platform_contract.json 缺失: {path}")
        try:
            with open(path, encoding="utf-8") as handle:
                _CONTRACT_CACHE = json.load(handle)
        except (OSError, ValueError) as exc:
            raise GatewayError(f"platform_contract.json 读取失败: {exc}") from exc
    return _CONTRACT_CACHE


_CONTRACT_CACHE: Optional[dict] = None


def load_model_whitelist() -> tuple[str, ...]:
    """从 contracts/platform_contract.json 的 model_whitelist_text 读白名单（缓存）。"""
    global _WHITELIST_CACHE
    if _WHITELIST_CACHE is None:
        whitelist = _platform_contract().get("model_whitelist_text")
        if not isinstance(whitelist, list) or not whitelist or \
                not all(isinstance(item, str) and item for item in whitelist):
            raise GatewayError("platform_contract.json 的 model_whitelist_text 非法")
        _WHITELIST_CACHE = tuple(whitelist)
    return _WHITELIST_CACHE


_WHITELIST_CACHE: Optional[tuple[str, ...]] = None


def is_mock_mode(config: dict) -> bool:
    """判定当前是否 mock 模式。

    True 条件（SPEC §11）：QW_FORCE_MOCK=1（键名可由 config["mock"]["force_env"]
    覆盖，默认 "QW_FORCE_MOCK"）；或 config["mock"]["enabled"] 为真；
    或 DASHSCOPE_API_KEY 缺失/空白/=="mock"。

    :param config: resolve_config 产出的配置
    :return: 是否 mock
    """
    mock_config = (config or {}).get("mock") or {}
    force_env = mock_config.get("force_env") or "QW_FORCE_MOCK"
    if os.environ.get(force_env, "").strip() == "1":
        return True
    if bool(mock_config.get("enabled", False)):
        return True
    api_key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    return (not api_key) or api_key == "mock"


def extract_json_object(text: str) -> dict:
    """从容错文本中提取第一个 JSON 对象（容错提取，不做业务修正）。

    依次尝试：整段直解 → Markdown 代码块围栏内直解 → 从每个 ``{`` 起
    raw_decode（容忍对象前后有说明文字）。顶层必须是 JSON 对象（dict）。

    :raises ValueError: 未找到可解析的 JSON 对象
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("内容为空，无法解析 JSON")
    decoder = json.JSONDecoder()
    candidates = [text.strip()]
    candidates.extend(match.strip() for match in _CODE_FENCE_RE.findall(text))
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except ValueError:
            continue
        if isinstance(parsed, dict):
            return parsed
    position = text.find("{")
    while position != -1:
        try:
            parsed, _ = decoder.raw_decode(text, position)
        except ValueError:
            position = text.find("{", position + 1)
            continue
        if isinstance(parsed, dict):
            return parsed
        position = text.find("{", position + 1)
    raise ValueError("未找到可解析的 JSON 对象")


class ModelGateway:
    """模型调用统一入口。"""

    def __init__(self, config: dict, budget: Optional[BudgetManager] = None) -> None:
        """读取配置与环境变量（只从 env 读密钥），确定模式。

        :param config: resolve_config 产出的配置
        :param budget: 可选，注入运行共享的 BudgetManager（契约签名的兼容扩展；
            缺省时内部自建一个，仅用于单请求超时与 token 记账）
        :raises GatewayError: real 模式下 OPENAI_BASE_URL/DASHSCOPE_API_KEY 缺失
            或格式非法、api_mode 未实现、白名单契约不可读
        """
        self._config = config or {}
        self._mock = is_mock_mode(self._config)
        self._whitelist = load_model_whitelist()
        self._sleep = time.sleep
        self._rand = random.random
        self._session = requests.Session()
        self._fixture: Optional[dict] = None
        self._fixture_loaded = False
        api_mode = self._config.get("api_mode") or "openai_compatible"
        if api_mode != "openai_compatible":
            # platform_contract.api_modes 含 dashscope_native（可选），第一阶段未实现
            raise GatewayError(f"api_mode={api_mode} 未实现（仅支持 openai_compatible）")
        self._budget = budget if budget is not None else BudgetManager(self._config)
        # usage 累计（供工具汇总成本）：只记 token 数与请求次数，不记任何内容
        self._usage_totals: dict[str, int] = {
            "prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0, "requests": 0,
        }
        self._base_url: Optional[str] = None
        self._api_key: Optional[str] = None
        if not self._mock:
            self._base_url = self._resolve_openai_base_url()
            self._api_key = self._resolve_api_key()

    # ---- 公共属性 ----

    @property
    def mock_mode(self) -> bool:
        """当前是否 mock 模式（is_mock_mode 在构造时的判定结果）。"""
        return self._mock

    @property
    def whitelist(self) -> tuple[str, ...]:
        """已加载的模型白名单（来自 platform_contract.json）。"""
        return self._whitelist

    @property
    def usage_totals(self) -> dict[str, int]:
        """网关实例累计 usage（prompt/completion/total tokens 与请求次数）。

        供工具汇总成本；只含 token 数与次数，不含任何消息内容。
        """
        return dict(self._usage_totals)

    # ---- 入口 ----

    def chat(self, messages: list[dict], model: str, *, mock_key: Optional[str] = None) -> dict:
        """执行一次对话调用。

        :param messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
        :param model: 模型 id，必须在白名单内
        :param mock_key: mock 模式必填的夹具键；real 模式忽略
        :return: {"role": "assistant", "content": str, "model": str, "mock": bool, "usage": dict}
            content 为模型原文（约定内含一个 JSON 对象，由调用方解析；本网关不解析业务 JSON）
        :raises ModelNotWhitelistedError: 模型不在白名单
        :raises MockResponseMissingError: mock 模式且 mock_key 缺失/夹具无此键/夹具文件缺失
        :raises UpstreamFatalError: 鉴权失败/模型未授权/预算耗尽（不重试）
        :raises ContextTooLongError: 上下文超限（不重试，调用方缩小分块）
        :raises EmptyContentError: 200 响应 content 为空/缺失（含模型名与
            finish_reason；真实网关在显式限制过小 max_tokens 时思考耗尽 token
            即如此）；usage 仍记账。调用方可用 chat_json 获得一次降级重试
        :raises GatewayError: 限流重试用尽/硬截止已到/响应非法等
        :raises src.budget_manager.TokenBudgetExceeded: 注入 budget 且 token 超预算
        """
        if model not in self._whitelist:
            raise ModelNotWhitelistedError(
                f"模型不在 platform_contract.model_whitelist_text 白名单内: {model}")
        self._validate_messages(messages)
        if self._mock:
            return self._chat_mock(messages, model, mock_key)
        return self._chat_real(messages, model)

    def chat_json(self, messages: list[dict], model: str, *,
                  mock_key: Optional[str] = None) -> dict:
        """chat() + 容错提取 JSON + 一次重试（仅 real 模式重试）。

        两条重试路径（各至多一次，不叠加）：
        - 空 content（EmptyContentError，finish_reason=length 等）：降级重试——
          在重试 messages 前加一句指令（JSON_DIRECT_OUTPUT_PROMPT），
          明确要求直接输出 JSON、不要长思考；
        - content 非 JSON（ValueError）：修复重试——追加失败回答与
          「请修复 JSON」指令（JSON_REPAIR_PROMPT）。

        mock 模式夹具是确定性数据，content 非法 JSON 时直接抛 GatewayError
        （不重试：重试同一夹具只会得到同一结果）。

        :raises GatewayError: 两次（含一次修复/降级重试）均无法解析为 JSON 对象，
            或 mock 夹具 content 非法
        """
        try:
            first = self.chat(messages, model, mock_key=mock_key)
        except EmptyContentError as exc:
            return self._retry_json_with_direct_output(messages, model, exc)
        try:
            return extract_json_object(first["content"])
        except ValueError:
            pass
        if self._mock:
            # 不打印 content 原文，只报告键名
            raise GatewayError(f"mock 夹具键 {mock_key} 的 content 不是合法 JSON 对象")
        repaired_messages = list(messages) + [
            {"role": "assistant", "content": first["content"]},
            {"role": "user", "content": JSON_REPAIR_PROMPT},
        ]
        second = self.chat(repaired_messages, model)
        try:
            return extract_json_object(second["content"])
        except ValueError as exc:
            raise GatewayError("模型输出两次（含一次修复重试）均无法解析为 JSON 对象") from exc

    def _retry_json_with_direct_output(self, messages: list[dict], model: str,
                                       original: EmptyContentError) -> dict:
        """空 content 后的一次降级重试：messages 前加「直接输出 JSON、不要长思考」指令。"""
        logger.warning("chat_json 捕获空 content（finish_reason=%s，模型=%s），"
                       "降级重试一次（要求直接输出 JSON，不要长思考）",
                       original.finish_reason, model)
        retry_messages = [{"role": "system", "content": JSON_DIRECT_OUTPUT_PROMPT}] \
            + list(messages)
        try:
            second = self.chat(retry_messages, model)
        except EmptyContentError as exc:
            raise GatewayError(
                f"降级重试后模型 {model} 的 content 仍为空"
                f"（finish_reason={exc.finish_reason}）；已做一次直接输出 JSON 的"
                "降级重试，不继续重试（检查是否显式限制了过小的 max_tokens）") from exc
        try:
            return extract_json_object(second["content"])
        except ValueError as exc:
            raise GatewayError(
                f"降级重试后模型 {model} 的输出仍无法解析为 JSON 对象"
                "（已做一次降级重试，不继续重试）") from exc

    def load_mock_fixture(self) -> dict[str, Any]:
        """加载 tests/fixtures/gateway/responses.json（UTF-8，结构见 interfaces.md §17）。

        解析顺序：config["mock"]["fixture_path"] 按原样（相对 cwd）→ 相对仓库根。
        结果缓存；重复调用不再读盘。

        :raises MockResponseMissingError: 文件缺失或顶层不是对象
        """
        if self._fixture_loaded:
            assert self._fixture is not None
            return self._fixture
        fixture_path = ((self._config.get("mock") or {}).get("fixture_path")
                        or DEFAULT_MOCK_FIXTURE_PATH)
        candidates = [Path(fixture_path)]
        if not Path(fixture_path).is_absolute():
            candidates.append(REPO_ROOT / fixture_path)
        path = next((candidate for candidate in candidates if candidate.is_file()), None)
        if path is None:
            raise MockResponseMissingError(f"mock 夹具文件缺失: {fixture_path}")
        try:
            with open(path, encoding="utf-8") as handle:
                fixture = json.load(handle)
        except (OSError, ValueError) as exc:
            raise MockResponseMissingError(f"mock 夹具读取/解析失败: {fixture_path}") from exc
        if not isinstance(fixture, dict):
            raise MockResponseMissingError(f"mock 夹具顶层必须是对象: {fixture_path}")
        self._fixture = fixture
        self._fixture_loaded = True
        return fixture

    # ---- mock 分支 ----

    def _chat_mock(self, messages: list[dict], model: str,
                   mock_key: Optional[str]) -> dict:
        if not mock_key:
            raise MockResponseMissingError("mock 模式下 mock_key 必填（禁止静默兜底）")
        fixture = self.load_mock_fixture()
        if mock_key not in fixture:
            raise MockResponseMissingError(f"mock 夹具缺少键: {mock_key}")
        value = fixture[mock_key]
        if not isinstance(value, dict) or not isinstance(value.get("content"), str):
            raise MockResponseMissingError(
                f"mock 夹具键 {mock_key} 格式不合法（须为 {{'role': 'assistant', 'content': str}}）")
        content = value["content"]
        usage = self._normalize_usage(None, messages, content)
        self._register_usage(usage)
        logger.info("mock chat key=%s model=%s completion_tokens≈%s",
                    mock_key, model, usage["completion_tokens"])
        return {"role": "assistant", "content": content,
                "model": model, "mock": True, "usage": usage}

    # ---- real 分支 ----

    def _chat_real(self, messages: list[dict], model: str) -> dict:
        assert self._base_url is not None and self._api_key is not None
        url = f"{self._base_url}/chat/completions"
        headers = {"Content-Type": "application/json",
                   "Authorization": f"Bearer {self._api_key}"}
        # 默认不发送 max_tokens（真实网关校准，2026-09）：显式限制过小时思考
        # （reasoning_content）会耗尽 token，导致 content 为空、finish_reason=length。
        payload = {"model": model, "messages": messages}
        # 思考开关（真实模型联调校准，2026-09-24）：仅当配置含 enable_thinking 键
        # 才发送该参数（缺省行为与契约一致，请求体恰为 {model, messages}）。
        # 真实网关实测：qwen3.6-plus 思考默认开启，产品抽取类复杂请求服务端长静默
        # >300s（思考不外发任何字节），单请求超时必然 ReadTimeout；enable_thinking
        # =False 实测 200、无 reasoning_content、耗时与 completion tokens 大幅下降。
        # 非 Qwen 系模型对该参数的兼容性未验证（openIssues 登记）。
        thinking = self._config.get("enable_thinking")
        if thinking is not None:
            payload["enable_thinking"] = bool(thinking)
        attempts = max(0, int(self._config.get("max_retries_per_request", 2))) + 1
        last_error = "未知错误"
        for attempt in range(attempts):
            if self._budget.hard_expired():
                raise GatewayError("已超硬截止，停止模型请求（budget_manager）")
            timeout = self._request_timeout()
            started = time.monotonic()
            try:
                response = self._session.post(url, headers=headers,
                                              json=payload, timeout=timeout)
            except (requests.Timeout, requests.ConnectionError) as exc:
                last_error = f"网络异常 {type(exc).__name__}"
                logger.warning("chat attempt=%d model=%s %s", attempt + 1, model, last_error)
            else:
                latency = time.monotonic() - started
                status = response.status_code
                logger.info("chat attempt=%d model=%s status=%s latency=%.2fs timeout=%.1fs",
                            attempt + 1, model, status, latency, timeout)
                if status == 200:
                    return self._parse_ok_response(response, model, messages)
                if status in RETRYABLE_STATUS_CODES:
                    last_error = f"HTTP {status}（可重试）"
                elif status in FATAL_STATUS_CODES:
                    raise UpstreamFatalError(self._describe_error(response, status))
                else:
                    body_text = self._safe_body_text(response)
                    if status in (400, 422) and _matches_any(body_text, _CONTEXT_OVERFLOW_MARKERS):
                        raise ContextTooLongError(
                            f"上下文超限（调用方应缩小分块）HTTP {status}: "
                            f"{_summarize(body_text)}")
                    if _matches_any(body_text, _FATAL_BODY_MARKERS):
                        raise UpstreamFatalError(self._describe_error(response, status))
                    raise GatewayError(
                        f"上游非预期状态 HTTP {status}: {_summarize(body_text)}")
            if attempt < attempts - 1:
                backoff = RETRY_BACKOFF_BASE_SECONDS * (2 ** attempt) + self._rand()
                logger.info("退避重试 %.2fs 后进行（attempt=%d）", backoff, attempt + 1)
                self._sleep(backoff)
        raise GatewayError(f"重试用尽（共 {attempts} 次尝试）：{last_error}")

    def _parse_ok_response(self, response: requests.Response, model: str,
                           messages: list[dict]) -> dict:
        try:
            body = response.json()
        except ValueError as exc:
            raise GatewayError("200 响应体不是合法 JSON") from exc
        try:
            choice = body["choices"][0]
            content = choice["message"]["content"]
            finish_reason = choice.get("finish_reason")
        except (KeyError, IndexError, TypeError) as exc:
            raise GatewayError("200 响应缺少 choices[0].message.content") from exc
        if content is None or (isinstance(content, str) and not content.strip()):
            # usage 先如实记账（token 已消耗），再抛清晰错误（含模型名与 finish_reason）
            usage = self._normalize_usage(body.get("usage"), messages, content or "")
            self._register_usage(usage)
            raise EmptyContentError(
                f"模型 {model} 返回 content 为空（finish_reason={finish_reason}）："
                "思考（reasoning_content）耗尽输出 token 或未产出答案；"
                "不要显式限制过小的 max_tokens（本网关默认不发送）",
                finish_reason=str(finish_reason) if finish_reason is not None else None)
        if not isinstance(content, str):
            raise GatewayError("200 响应 content 不是字符串")
        usage = self._normalize_usage(body.get("usage"), messages, content)
        self._register_usage(usage)
        return {"role": "assistant", "content": content,
                "model": model, "mock": False, "usage": usage}

    # ---- 内部工具 ----

    def _request_timeout(self) -> float:
        """单请求超时 = min(距软截止剩余, 上限)，下限 1s；软截止后仅 1s 尽力尝试。"""
        cap = float(self._config.get("request_timeout_seconds",
                                     DEFAULT_REQUEST_TIMEOUT_SECONDS))
        remaining = self._budget.soft_remaining_seconds()
        if remaining <= 0:
            return 1.0
        return max(1.0, min(remaining, cap))

    def _normalize_usage(self, raw: Any, messages: list[dict], content: str) -> dict:
        """平台 usage 缺失时按中文近似口径估算（estimated=True）。"""
        if isinstance(raw, dict):
            prompt = raw.get("prompt_tokens")
            completion = raw.get("completion_tokens")
            if isinstance(prompt, int) and isinstance(completion, int) \
                    and prompt >= 0 and completion >= 0:
                total = raw.get("total_tokens")
                return {"prompt_tokens": prompt, "completion_tokens": completion,
                        "total_tokens": total if isinstance(total, int) else prompt + completion,
                        "estimated": False}
        prompt_estimate = estimate_messages_tokens(messages)
        completion_estimate = estimate_tokens(content)
        return {"prompt_tokens": prompt_estimate,
                "completion_tokens": completion_estimate,
                "total_tokens": prompt_estimate + completion_estimate,
                "estimated": True}

    def _register_usage(self, usage: dict) -> None:
        """usage 记账：回填 budget_manager（超预算抛 TokenBudgetExceeded 由上层降级），
        并累计到网关实例 usage_totals（供工具汇总成本；只记 token 数，不记内容）。"""
        self._budget.register_tokens(usage["prompt_tokens"], usage["completion_tokens"])
        totals = self._usage_totals
        totals["prompt_tokens"] += int(usage["prompt_tokens"])
        totals["completion_tokens"] += int(usage["completion_tokens"])
        totals["total_tokens"] += int(usage["total_tokens"])
        totals["requests"] += 1
        logger.debug("usage 累计 prompt=%s completion=%s total=%s requests=%s",
                     totals["prompt_tokens"], totals["completion_tokens"],
                     totals["total_tokens"], totals["requests"])

    @staticmethod
    def _validate_messages(messages: list[dict]) -> None:
        if not isinstance(messages, list) or not messages:
            raise GatewayError("messages 必须为非空列表")
        for message in messages:
            if not isinstance(message, dict) or not isinstance(message.get("role"), str) \
                    or not isinstance(message.get("content"), str):
                raise GatewayError("messages 元素须为 {'role': str, 'content': str}")

    @staticmethod
    def _resolve_openai_base_url() -> str:
        raw = os.environ.get("OPENAI_BASE_URL", "").strip()
        if not raw:
            raise GatewayError("real 模式需要环境变量 OPENAI_BASE_URL（以 /v1 结尾）")
        base = raw.rstrip("/")
        if not base.endswith("/v1"):
            raise GatewayError(f"OPENAI_BASE_URL 必须以 /v1 结尾（平台契约），当前: {raw}")
        return base

    @staticmethod
    def _resolve_api_key() -> str:
        key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
        if not key or key == "mock":
            raise GatewayError("real 模式需要环境变量 DASHSCOPE_API_KEY（且不为 'mock'）")
        return key

    @staticmethod
    def _safe_body_text(response: requests.Response) -> str:
        try:
            return response.text or ""
        except (OSError, ValueError):
            return ""

    @staticmethod
    def _describe_error(response: requests.Response, status: int) -> str:
        body_text = ModelGateway._safe_body_text(response)
        code = ""
        try:
            body = response.json()
            if isinstance(body, dict):
                error = body.get("error") if isinstance(body.get("error"), dict) else body
                code = str(error.get("code") or "")
        except ValueError:
            pass
        return (f"上游致命错误 HTTP {status} code={code} "
                f"（鉴权失败/模型未授权/预算耗尽，不重试）: {_summarize(body_text)}")


def _matches_any(text: str, markers: tuple[str, ...]) -> bool:
    lowered = (text or "").lower()
    return any(marker in lowered for marker in markers)


def _summarize(text: str, limit: int = 200) -> str:
    """截断的响应体摘要（不含密钥；只用于错误信息定位）。"""
    flattened = " ".join((text or "").split())
    return flattened[:limit]
