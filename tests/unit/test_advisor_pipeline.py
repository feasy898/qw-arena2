# -*- coding: utf-8 -*-
"""advisor_pipeline 单元测试：postprocess 规则 + 三段解析 + 文件命名约束
+ 模型选型配置化（config「advisor」段经 resolve_config 读取，缺失回退默认）。"""
from __future__ import annotations

import json
import sys

import pytest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import advisor_pipeline as advisor  # noqa: E402


def test_postprocess_removes_process_phrases():
    dirty = (
        "续航=10h（多来源取值并列保留，未取平均、未择一（取值存在差异，待核验））；"
        "降噪=无主动降噪资料未见直接记载（未证实）；"
        "听感=<UNDEFINED!>；广告=<图片:2> 限时；<div>残留</div>"
    )
    out = advisor.postprocess(dirty)
    for bad in ("多来源取值并列保留", "未证实", "资料未见直接记载",
                "UNDEFINED", "<图片", "<div>", "</div>"):
        assert bad not in out, bad


def test_postprocess_idempotent():
    sample = "价格=展示价1098元（待核验）；续航=10h"
    assert advisor.postprocess(advisor.postprocess(sample)) == advisor.postprocess(sample)


def test_postprocess_preserves_table_pipes():
    row = "| 首选 | P001 | Ralun Pro | 预算内 | 需核验 |"
    assert advisor.postprocess(row) == row  # 表格分隔符不得被破坏


def test_parse_three_docs_ok():
    raw = ("=====FILE: user_profile.md=====\n# A\n"
           "=====FILE: product_list.md=====\n# B\n"
           "=====FILE: recommendation.md=====\n# C\n")
    docs = advisor.parse_three_docs(raw)
    assert list(docs) == list(advisor.FILES)


def test_parse_three_docs_missing_raises():
    import pytest
    with pytest.raises(RuntimeError):
        advisor.parse_three_docs("=====FILE: user_profile.md=====\n# A only\n")


def test_collect_input_takes_first_user_only(tmp_path):
    root = tmp_path / "input"
    (root / "00_User_Descriptions").mkdir(parents=True)
    (root / "01_Ecommerce_Listings").mkdir(parents=True)
    (root / "00_User_Descriptions" / "User_Description_1.txt").write_text("用户一", encoding="utf-8")
    (root / "00_User_Descriptions" / "User_Description_6.txt").write_text("用户六", encoding="utf-8")
    (root / "01_Ecommerce_Listings" / "x.csv").write_text("a,b", encoding="utf-8")
    bundle = advisor.collect_input(str(root))
    assert "用户一" in bundle
    assert "用户六" not in bundle  # 只取第一份用户描述
    assert "a,b" in bundle


# ---- 画像编号回填 + 程序日志短语硬卡 + prompt 内容规则 ----

_PROFILE_MD = (
    "# 用户画像\n\n"
    "| 字段组 | 字段名 | 取值 |\n"
    "| --- | --- | --- |\n"
    "| 标识 | 画像编号 | 未提供 |\n"
)


def test_profile_no_from_bundle():
    bundle = ("\n===== 00_User_Descriptions/User_Description_1.txt =====\n正文\n"
              "\n===== 01_Ecommerce_Listings/Ecommerce_Listing_Snapshot.csv =====\nx\n")
    assert advisor.profile_no_from_bundle(bundle) == "1"
    assert advisor.profile_no_from_bundle("没有用户描述文件的输入") is None


def test_postprocess_fills_profile_number():
    filled = advisor.postprocess(_PROFILE_MD, profile_no="1")
    assert "| 标识 | 画像编号 | 1 |" in filled
    assert "未提供 |" not in filled.splitlines()[4]
    # 幂等：回填后再跑不变
    assert advisor.postprocess(filled, profile_no="1") == filled
    # 模型已写出取值时不覆盖；未给编号时不改动
    already = _PROFILE_MD.replace("| 未提供 |", "| User_Description_1 |")
    assert advisor.postprocess(already, profile_no="1") == already
    assert advisor.postprocess(_PROFILE_MD) == _PROFILE_MD


def test_postprocess_rewrites_program_log_phrases():
    dirty = ("当前售价=展示价(元)列直接读取（模型抽取缺失，程序按来源行补录）；"
             "共性好评点=多来源取值并列保留，未取平均、未择一；"
             "预算=约2000元不直接当硬上限；槽点=必须单列一条观察")
    out = advisor.postprocess(dirty)
    for bad in ("程序按来源行补录", "模型抽取缺失", "展示价(元)列直接读取",
                "必须单列一条观察", "多来源取值并列保留", "不直接当硬上限"):
        assert bad not in out, bad


def test_prompt_covers_v061_content_rules():
    """prompt 内容规则必须落进模板：市场验证四类字段+样本纪律、场景环境原子项、
    画像编号、保修归组、价格单位与推荐句式、需求判断声明。"""
    text = advisor._PROMPT_FILE.read_text(encoding="utf-8")
    for needle in (
        "画像编号", "User_Description_N.txt", "姓名", "公司类型",
        "场景名称+环境/条件特征", "音质要求", "低音偏好",
        "用户综合评分", "共性好评点", "共性槽点", "价格趋势",
        "不构成共性", "无公开评分数据",
        "购买与使用成本", "1000元", "重量",
        "需求判断", "形态错配", "低于预算上限", "优惠前价格", "若确认",
    ):
        assert needle in text, needle


def test_prompt_examples_neutralized():
    """格式示例中性化：prompt 内不残留样例数据的具体编号、品类味约束示例词，
    以及样例数据集任一用户/产品的具体取值（称谓/场景/偏好/价格等）。"""
    text = advisor._PROMPT_FILE.read_text(encoding="utf-8")
    for bad in ("P001", "P005", "User_Description_1", "头戴式", "入耳式",
                "骨传导", "防水", "耳机", "1098", "456"):
        assert bad not in text, bad
    # 样例数据集具体取值（6 份用户描述 + 货架/官网脱敏名）逐字比对不得命中
    for bad in ("小王", "小李", "小张", "陈女士", "赵先生", "地铁", "咖啡馆",
                "轰头", "禾岚", "Velmora", "产品经理", "游泳", "图书馆", "大二",
                "FPS", "40分钟", "28岁", "2000元", "互联网公司", "销售总监"):
        assert bad not in text, bad


def test_prompt_file_lives_in_prompts_dir():
    """Prompt 模板按包内资源定位（prompts/ 与 src/ 互为兄弟目录）。"""
    assert advisor._PROMPT_FILE == (advisor.REPO_ROOT / "prompts" / "advisor_prompt.txt")
    assert advisor._PROMPT_FILE.is_file()


# ---- 模型选型配置化：config「advisor」段 ----

def test_advisor_models_read_from_config(monkeypatch):
    import src.pipeline as pl
    monkeypatch.setattr(pl, "resolve_config",
                        lambda *a, **k: {"advisor": {"gen_model": "model-x",
                                                     "fallback_models": ["model-y", ""]}})
    monkeypatch.setattr("src.model_gateway.load_model_whitelist",
                        lambda: ("model-x", "model-y"))
    assert advisor._advisor_models() == ("model-x", ("model-y",))


def test_advisor_models_fallback_when_config_missing(monkeypatch):
    import src.pipeline as pl
    monkeypatch.setattr(pl, "resolve_config", lambda *a, **k: {})
    assert advisor._advisor_models() == (advisor.GEN_MODEL, advisor.FALLBACK_MODELS)


def test_advisor_models_fallback_when_config_broken(monkeypatch):
    import src.pipeline as pl
    def _boom(*a, **k):
        raise ValueError("配置不合法")
    monkeypatch.setattr(pl, "resolve_config", _boom)
    assert advisor._advisor_models() == (advisor.GEN_MODEL, advisor.FALLBACK_MODELS)


def test_config_example_advisor_section_matches_defaults():
    """config.example.json「advisor」段与模块兜底默认一致（配置化前后行为等价）。"""
    repo_root = Path(__file__).resolve().parents[2]
    cfg = json.loads((repo_root / "config.example.json").read_text(encoding="utf-8"))
    section = cfg.get("advisor") or {}
    assert section.get("gen_model") == advisor.GEN_MODEL
    assert list(section.get("fallback_models") or []) == list(advisor.FALLBACK_MODELS)


def test_resolve_config_exposes_advisor_defaults():
    from src.pipeline import resolve_config
    section = resolve_config().get("advisor") or {}
    assert section.get("gen_model") == advisor.GEN_MODEL
    assert list(section.get("fallback_models") or []) == list(advisor.FALLBACK_MODELS)


# ---- 成功路径遥测：AGENT_LOG_DIR/agent.log「运行完成/usage 汇总」 ----
# run() 在 write_outputs 后接出厂质检门（src.self_check.run），占位产物
# 「# A/# B/# C」过不了质检（会触发重试+RuntimeError）。本组用例只验证遥测行为，
# 故 fixture 换成能通过质检的最小三份产物（构造器见 test_self_check_gate，
# 与质检闭环用例共享同一份），全部断言（rc/调用序/日志行）保持不变。
from tests.unit.test_self_check_gate import three_docs_passing  # noqa: E402

_THREE_DOCS = three_docs_passing()


def _detach_log_handlers(log_path) -> None:
    """清理遥测用例挂在 root/"agent" logger 上的 FileHandler（不泄漏给其他用例）。"""
    import logging
    target = str(log_path)
    root = logging.getLogger()
    for handler in list(root.handlers):
        if getattr(handler, "baseFilename", None) == target:
            root.removeHandler(handler)
            handler.close()
    named = logging.getLogger("agent")
    for handler in list(named.handlers):
        if getattr(handler, "baseFilename", None) == target:
            named.removeHandler(handler)
            handler.close()


def test_run_writes_usage_telemetry_on_success(tmp_path, monkeypatch):
    """成功路径：AGENT_LOG_DIR/agent.log 记「运行完成」+「usage 汇总」两行
    （模型/耗时/token 如响应携带/请求次数），产物不受遥测影响。"""
    input_dir = tmp_path / "input"
    (input_dir / "00_User_Descriptions").mkdir(parents=True)
    (input_dir / "00_User_Descriptions" / "User_Description_1.txt").write_text(
        "用户一", encoding="utf-8")
    output_dir = tmp_path / "output"
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_LOG_DIR", str(log_dir))
    calls: list[str] = []

    def fake_chat(key, base, model, user_msg, **kwargs):
        calls.append(model)
        return _THREE_DOCS, {"prompt_tokens": 11, "completion_tokens": 22,
                             "total_tokens": 33}

    monkeypatch.setattr(advisor, "_chat", fake_chat)
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: None)
    try:
        rc = advisor.run(f"请根据 {input_dir} 中的数据完成消费决策任务，"
                         f"将结果输出到 {output_dir}")
    finally:
        _detach_log_handlers(log_dir / "agent.log")
    assert rc == 0
    assert calls == [advisor.GEN_MODEL]  # 首选模型一次成功 → requests=1
    assert all((output_dir / name).is_file() for name in advisor.FILES)
    text = (log_dir / "agent.log").read_text(encoding="utf-8")
    assert "运行完成 耗时=" in text
    assert f"模型={advisor.GEN_MODEL}" in text
    assert (f"usage 汇总 model={advisor.GEN_MODEL} prompt=11 completion=22 "
            f"total=33 requests=1") in text


def test_run_telemetry_counts_fallback_requests_and_missing_usage(tmp_path, monkeypatch):
    """首选模型请求失败切兜底 → requests=2；响应未携带 usage 块 → 记
    「token=响应未携带」（不编造 token 数）。"""
    input_dir = tmp_path / "input"
    (input_dir / "00_User_Descriptions").mkdir(parents=True)
    (input_dir / "00_User_Descriptions" / "User_Description_1.txt").write_text(
        "用户一", encoding="utf-8")
    output_dir = tmp_path / "output"
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_LOG_DIR", str(log_dir))
    monkeypatch.setattr(advisor, "_advisor_models", lambda: ("model-x", ("model-y",)))
    calls: list[str] = []

    def fake_chat(key, base, model, user_msg, **kwargs):
        calls.append(model)
        if model == "model-x":
            raise RuntimeError("model-x HTTP 500: boom")
        return _THREE_DOCS, None  # 响应未携带 usage 块

    monkeypatch.setattr(advisor, "_chat", fake_chat)
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: None)
    try:
        rc = advisor.run(f"请根据 {input_dir} 中的数据完成消费决策任务，"
                         f"将结果输出到 {output_dir}")
    finally:
        _detach_log_handlers(log_dir / "agent.log")
    assert rc == 0
    assert calls == ["model-x", "model-y"]
    text = (log_dir / "agent.log").read_text(encoding="utf-8")
    assert "模型=model-y" in text
    assert "usage 汇总 model=model-y token=响应未携带 requests=2" in text


def test_log_usage_skipped_without_agent_log_dir_env(tmp_path, monkeypatch):
    """AGENT_LOG_DIR 未设置 → 遥测直接跳过：不触碰日志通道、不落任何文件。"""
    import src.pipeline as pl
    monkeypatch.delenv("AGENT_LOG_DIR", raising=False)

    def _must_not_touch(*a, **k):  # 埋雷：gate 失效（继续初始化日志）即爆
        raise AssertionError("AGENT_LOG_DIR 未设置时不得初始化/写日志")

    monkeypatch.setattr(pl, "_ensure_logging", _must_not_touch)
    advisor._log_usage("model-x", 1.0,
                       {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}, 1)
    assert not (tmp_path / "agent.log").exists()


# ---------------------------------------------------------------------------
# 直出链健壮性回归：进程死线 / 限流退避 / 白名单校验 / 空 content
# ---------------------------------------------------------------------------

class _FakeResponse:
    def __init__(self, status_code, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text

    def json(self):
        return self._payload


_OK_PAYLOAD = {"choices": [{"message": {"content": "正文"}}],
               "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}


def test_chat_retries_on_429_then_succeeds(monkeypatch):
    """429 限流：原地退避重试后成功；退避次数与等待可被观察。"""
    responses = [_FakeResponse(429, text="rate limited"),
                 _FakeResponse(200, _OK_PAYLOAD)]
    calls = []

    class _FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            calls.append(json["model"])
            return responses.pop(0)

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    sleeps: list[float] = []
    monkeypatch.setattr(advisor.time, "sleep", sleeps.append)
    text, usage = advisor._chat("k", "https://example/v1", "m1", "msg")
    assert text == "正文" and usage["total_tokens"] == 3
    assert calls == ["m1", "m1"]          # 同模型原地重试 1 次
    assert len(sleeps) == 1 and 5.0 <= sleeps[0] < 6.1


def test_chat_404_no_retry(monkeypatch):
    """非限流类错误（404 模型不存在）不原地重试，直接抛错换模型。"""
    class _FakeRequests:
        calls = 0

        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            _FakeRequests.calls += 1
            return _FakeResponse(404, text="model not found")

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    monkeypatch.setattr(advisor.time, "sleep", lambda s: None)
    with pytest.raises(RuntimeError, match="404"):
        advisor._chat("k", "https://example/v1", "m-bad", "msg")
    assert _FakeRequests.calls == 1


def test_chat_backoff_aborted_by_deadline(monkeypatch):
    """退避等待将越过进程死线时放弃重试（不在死线外睡觉）。"""
    class _FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            return _FakeResponse(429, text="rate limited")

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    monkeypatch.setattr(advisor.time, "sleep", lambda s: None)
    past_deadline = advisor.time.monotonic() - 1.0
    with pytest.raises(RuntimeError, match="越过死线"):
        advisor._chat("k", "https://example/v1", "m1", "msg",
                      deadline=past_deadline)


def test_chat_empty_content_raises(monkeypatch):
    """M3：content 为空/缺失视为模型失败（网关校准结论：不发送 max_tokens 后
    仍防御思考预算耗尽或拦截导致的空正文）。"""
    class _FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            return _FakeResponse(200, {"choices": [{"message": {"content": "  "}}]})

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    with pytest.raises(RuntimeError, match="空 content"):
        advisor._chat("k", "https://example/v1", "m1", "msg")


def test_chat_no_max_tokens_in_payload(monkeypatch):
    """M3：请求体不得携带 max_tokens（网关校准：显式限制致思考耗尽 content 为空）。"""
    captured: dict = {}

    class _FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            captured.update(json)
            return _FakeResponse(200, _OK_PAYLOAD)

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    advisor._chat("k", "https://example/v1", "m1", "msg")
    assert "max_tokens" not in captured
    assert captured["seed"] == 42 and captured["temperature"] == 0.05


def test_advisor_models_whitelist_filter(monkeypatch):
    """M2：advisor 段配置里的白名单外模型被剔除/降级，不在运行期 404。"""
    whitelist = advisor._advisor_models  # noqa: F841（占位防误删）
    monkeypatch.setattr("src.pipeline.resolve_config",
                        lambda: {"advisor": {"gen_model": "not-a-model",
                                             "fallback_models": ["qwen3.7-max",
                                                                 "also-bad"]}})
    gen, fallbacks = advisor._advisor_models()
    assert gen == advisor.GEN_MODEL                       # gen 违规 → 降回默认
    assert fallbacks == ("qwen3.7-max",)                  # 白名单外被剔除


def test_advisor_models_whitelist_unreadable_no_block(monkeypatch):
    """白名单不可读时不增设拦截（fail-open），配置原样返回。"""
    monkeypatch.setattr("src.pipeline.resolve_config",
                        lambda: {"advisor": {"gen_model": "custom-model",
                                             "fallback_models": ["x"]}})
    monkeypatch.setattr("src.model_gateway.load_model_whitelist",
                        lambda: (_ for _ in ()).throw(RuntimeError("契约缺失")))
    gen, fallbacks = advisor._advisor_models()
    assert gen == "custom-model" and fallbacks == ("x",)


def test_run_aborts_near_deadline(tmp_path, monkeypatch):
    """H1：死线临近时不再发起模型调用，抛错移交回退链（产物缺席由防线接管）。"""
    input_dir = tmp_path / "input"
    (input_dir / "00_User_Descriptions").mkdir(parents=True)
    (input_dir / "00_User_Descriptions" / "User_Description_1.txt").write_text(
        "用户一", encoding="utf-8")
    output_dir = tmp_path / "output"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.delenv("AGENT_LOG_DIR", raising=False)
    monkeypatch.setattr(advisor, "DEADLINE_S", -1.0)  # 死线已过
    called: list[str] = []
    monkeypatch.setattr(advisor, "_chat",
                        lambda *a, **k: called.append("x") or ("", None))
    with pytest.raises(RuntimeError, match="死线临近|全部候选模型失败"):
        advisor.run(f"请根据 {input_dir} 中的数据完成消费决策任务，"
                    f"将结果输出到 {output_dir}")
    assert not called  # 一次调用都未发起


def test_chat_wallclock_caps_trickling_call(monkeypatch):
    """墙钟口径（0.7.6）：慢流式/挂起的调用在 timeout_s 到点即抛错换模型——
    requests 的 socket 读超时被慢流式续命架空时，墙钟是唯一的真实上限。"""
    import time as _time

    class _FakeRequests:
        @staticmethod
        def post(url, headers=None, json=None, timeout=None):
            _time.sleep(30)  # 慢流式挂起（socket 读超时永不生效的场景）
            raise AssertionError("不应到达")

    monkeypatch.setitem(sys.modules, "requests", _FakeRequests)
    started = _time.monotonic()
    with pytest.raises(RuntimeError, match="墙钟"):
        advisor._chat("k", "https://example/v1", "m1", "msg", timeout_s=0.3)
    assert _time.monotonic() - started < 5.0  # 按时截断，不陪跑
