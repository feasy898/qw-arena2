# -*- coding: utf-8 -*-
"""tests/unit/test_profile_extractor.py — 画像抽取单元测试（离线 mock 网关）。

夹具数据全部取自 raw/dataset_sample/Data_for_Users/ 真实文件：
- 用户描述：00_User_Descriptions/User_Description_1.txt（黄金夹具 profile:user_1）；
- mock 响应：tests/fixtures/gateway/responses.json（构建时逐条校验引文可回溯）。
覆盖：契约键集、真值取值、UserNeed 原子化与证据回溯、一次修复重试后失败、
mock 缺键不兜底、确定性需求兜底派生、预算口径推导。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.model_gateway import (
    GatewayError,
    MockResponseMissingError,
    ModelGateway,
)
from src.profile_extractor import (
    MOCK_KEY,
    _derive_budget_semantics,
    derive_user_needs,
    extract_profile,
    extract_profile_bundle,
    render_profile_prompt,
)
from src.schemas import NeedType

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
USER1_PATH = SAMPLE_DIR / "00_User_Descriptions" / "User_Description_1.txt"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "gateway" / "responses.json"

USER1_TEXT = USER1_PATH.read_text(encoding="utf-8-sig")


def make_config() -> dict:
    return {"default_model": "qwen3.6-plus", "max_retries_per_request": 2,
            "mock": {"enabled": True, "force_env": "QW_FORCE_MOCK",
                     "fixture_path": str(FIXTURE_PATH)}}


@pytest.fixture(scope="module")
def gateway() -> ModelGateway:
    return ModelGateway(make_config())


@pytest.fixture(scope="module")
def extracted(gateway: ModelGateway) -> tuple[dict, list]:
    return extract_profile_bundle(USER1_TEXT, gateway, make_config())


# ---------- 契约结构与真值（夹具取自 User_Description_1 原文） ----------

def test_profile_covers_all_field_catalog_keys(extracted):
    import json
    catalog = json.loads((REPO_ROOT / "contracts" / "field_catalog.json").read_text(
        encoding="utf-8"))
    expected = {f["key"] for g in catalog["profile"]["groups"] for f in g["fields"]}
    profile = extracted[0]
    assert expected <= set(profile), sorted(expected - set(profile))
    scene_items = {f["key"] for f in
                   next(g for g in catalog["profile"]["groups"] if g["name"] == "场景")
                   ["fields"][0]["item_fields"]}
    for scene in profile["scenes"]:
        assert scene_items <= set(scene)


def test_profile_ground_truth_values(extracted):
    profile = extracted[0]
    assert profile["profile_id"] == "User_Description_1"
    assert profile["name"] == "小王"
    assert profile["gender"] == "男"
    assert profile["age"] == 28
    assert profile["city"] == "上海"
    assert profile["product_category"] == "耳机"
    assert profile["desired_product_type"] is None  # 原文未给类型，不外推
    assert profile["budget_max"] == 2000
    assert profile["budget_min"] is None
    assert profile["budget_raw"] == "预算2000元左右"
    assert profile["budget_semantics"] == "约(单值)"  # 不得直接当硬上限
    assert profile["currency"] == "元"
    assert "Velmora 手机" in profile["devices"]
    assert "禾岚 安卓手机" in profile["devices"]
    assert profile["scenes"][0]["scene_name"] == "地铁通勤"
    assert profile["scenes"][0]["usage_duration"] == "单程40分钟"
    assert profile["scenes"][0]["usage_frequency"] == "每天"
    assert profile["scenes"][0]["scene_priority"] == 1
    assert profile["scenes"][0]["scene_evidence"] in USER1_TEXT
    assert profile["sound_preferences"] == ["对音质有一定要求", "不喜欢太轰头的低音"]


def test_scenes_atomic_and_ranked(extracted):
    scenes = extracted[0]["scenes"]
    assert [s["scene_name"] for s in scenes] == ["地铁通勤", "咖啡馆办公"]
    assert scenes[0]["scene_priority"] < scenes[1]["scene_priority"]
    assert all(s["priority_basis"] in ("原文明示排序", "原文主次措辞", "优先级未明确")
               for s in scenes)


# ---------- UserNeed 原子化与证据回溯 ----------

def test_user_needs_atomic_with_traceable_evidence(extracted):
    needs = extracted[1]
    assert len(needs) >= 5  # 原子化：一句话多事实拆成多条
    for need in needs:
        assert need.need_type in tuple(NeedType)
        assert need.original_expression
        assert need.need_id
        for ref in need.evidence_refs:
            assert ref.source_id == "00_User_Descriptions/User_Description_1.txt"
            assert ref.span_id == "L1"  # 用户描述为单行文件
            assert ref.exact_quote in USER1_TEXT  # 逐字回溯


def test_budget_fuzzy_value_not_hard_constraint(extracted):
    budget_needs = [n for n in extracted[1] if n.expected_value == 2000]
    assert budget_needs, "预算需求缺失"
    # 「预算2000元左右」为约(单值)：不得直接成为硬上限（rules.json 约束准入）
    assert all(n.need_type is not NeedType.HARD for n in budget_needs)
    assert any(n.need_type is NeedType.AMBIGUOUS for n in budget_needs)


def test_android_compat_need_is_soft(extracted):
    soft = [n for n in extracted[1]
            if "安卓" in (n.original_expression or "")]
    assert soft and all(n.need_type is NeedType.SOFT for n in soft)


# ---------- 失败一次修复重试 / mock 不兜底 ----------

def test_schema_repair_retry_then_gateway_error(monkeypatch):
    import src.profile_extractor as pe
    gateway = ModelGateway(make_config())
    calls: list[str] = []
    original_chat = gateway.chat

    def counting_chat(messages, model, **kwargs):
        calls.append(kwargs.get("mock_key"))
        return original_chat(messages, model, **kwargs)

    gateway.chat = counting_chat
    monkeypatch.setattr(pe, "MOCK_KEY", "profile_extraction_bad_schema")
    with pytest.raises(GatewayError) as excinfo:
        extract_profile(USER1_TEXT, gateway, make_config())
    # 初始 1 次 + 修复重试 1 次（mock 重试同夹具，仍失败即抛错，不静默兜底）
    assert calls == ["profile_extraction_bad_schema", "profile_extraction_bad_schema"]
    assert "一次修复重试" in str(excinfo.value)


def test_missing_mock_key_raises_without_fallback(monkeypatch):
    import src.profile_extractor as pe
    gateway = ModelGateway(make_config())
    monkeypatch.setattr(pe, "MOCK_KEY", "no_such_profile_key")
    with pytest.raises(MockResponseMissingError):
        extract_profile(USER1_TEXT, gateway, make_config())


# ---------- 确定性兜底派生 ----------

def test_derive_user_needs_matches_rules(extracted):
    profile = extracted[0]
    needs = derive_user_needs(USER1_TEXT, profile)
    budget = [n for n in needs if n.expected_value == 2000]
    assert budget and all(n.need_type is NeedType.AMBIGUOUS for n in budget)
    assert budget[0].operator == "<="
    scene_needs = [n for n in needs if n.need_type is NeedType.GOAL]
    priorities = [n.priority for n in scene_needs]
    assert priorities == sorted(priorities, reverse=True)  # 高优先级场景数值更大
    assert scene_needs[0].evidence_refs[0].exact_quote in USER1_TEXT
    soft = [n for n in needs if n.need_type is NeedType.SOFT]
    assert soft and all(n.operator is None for n in soft)


def test_derive_budget_semantics_rules():
    assert _derive_budget_semantics("预算1500-2000元") == "区间"
    assert _derive_budget_semantics("大概在1500-2000元之间") == "区间"
    assert _derive_budget_semantics("预算300元以内") == "上限"
    assert _derive_budget_semantics("预算2000元左右") == "约(单值)"
    assert _derive_budget_semantics("") == "未明确"


def test_render_prompt_embeds_user_text():
    rendered = render_profile_prompt(USER1_TEXT)
    assert USER1_TEXT.strip() in rendered
    assert "{{USER_TEXT}}" not in rendered
