# -*- coding: utf-8 -*-
"""tests/unit/test_schema_extractor.py — Schema+Grounding 抽取架构单元测试。

覆盖 BUILD_TASK 要求的六类校验：
1 合法输出 / 2 缺 quote / 3 quote 非子串 / 4 结构缺键 / 5 重试成功 / 6 重试仍失败；
另覆盖：grounded 产品抽取全链（stub 网关）、字段级融合、Prompt 模板卫生
（禁止真实示例值）与占位符渲染。
夹具数据取自 raw/dataset_sample/Data_for_Users 真实样例（与既有测试同口径）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.input_adapter import load_input
from src.model_gateway import GatewayError
from src.schema_extractor import (
    PRODUCT_GROUNDED_PROMPT,
    PRODUCT_SUPPRESSED_FIELDS,
    PROFILE_GROUNDED_PROMPT,
    STRUCTURE_MARKER,
    _validate_with_grounding,
    extract_product_grounded,
    extract_profile_grounded,
    fuse_product_drafts,
    fuse_profile_fields,
    product_field_schema,
    profile_field_schema,
    render_grounded_profile_prompt,
)
from src.source_index import SourceIndex

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
USER1_TEXT = (SAMPLE_DIR / "00_User_Descriptions" / "User_Description_1.txt"
              ).read_text(encoding="utf-8-sig")


@pytest.fixture(scope="module")
def p001_records():
    bundle = load_input(SAMPLE_DIR)
    index = SourceIndex(bundle.source_records)
    records = sorted(index.by_product("P001"),
                     key=lambda r: (str(r.source_type), r.source_id))
    return records, "\n".join(str(r.original_text) for r in records)


def _p001_source_quote(records, needle: str) -> str:
    """从 P001 五源原文中定位含 needle 的一个逐字片段（测试夹具构造用）。"""
    for record in records:
        text = str(record.original_text)
        pos = text.find(needle)
        if pos >= 0:
            return text[pos:pos + len(needle)]
    raise AssertionError(f"P001 源文本不含锚点: {needle!r}")


class StubGateway:
    """chat_json 按序返回预置应答的桩网关（不发网络请求）。"""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls: list[dict] = []

    def chat_json(self, messages, model, *, mock_key=None):
        self.calls.append({"messages": messages, "model": model, "mock_key": mock_key})
        item = self.responses.pop(0)
        if isinstance(item, Exception):
            raise item
        return item


# ---------- 1 合法 ----------

def test_validate_legal_output():
    schema = profile_field_schema()
    out = {
        "name": {"value": "小王", "exact_quote": "小王"},
        "age": {"value": 28, "exact_quote": "28岁"},
        "budget_raw": {"value": "预算2000元左右", "exact_quote": "预算2000元左右"},
        "sound_preferences": {"value": ["对音质有一定要求"], "exact_quote": "对音质有一定要求"},
        "scenes": {"value": [{
            "scene_name": {"value": "地铁通勤", "exact_quote": "每天地铁通勤"},
            "usage_duration": {"value": "单程40分钟", "exact_quote": "单程40分钟"},
            "scene_priority": {"value": 1, "exact_quote": None},
        }], "exact_quote": None},
    }
    validated, rejected = _validate_with_grounding(out, USER1_TEXT, schema)
    assert rejected == []
    assert validated["name"] == "小王"
    assert validated["age"] == 28
    assert validated["sound_preferences"] == ["对音质有一定要求"]
    assert validated["scenes"][0]["scene_name"] == "地铁通勤"
    assert validated["scenes"][0]["scene_priority"] == 1  # grounded=False 子字段照常解包
    assert "budget_semantics" not in validated  # 模型未给 → 缺失（不记 rejected）


# ---------- 2 缺 quote ----------

def test_validate_missing_quote_rejects_field():
    schema = profile_field_schema()
    out = {
        "name": {"value": "小王"},                    # 无 exact_quote 键
        "city": {"value": "上海", "exact_quote": None},  # quote 为 null
    }
    validated, rejected = _validate_with_grounding(out, USER1_TEXT, schema)
    assert validated == {}
    assert any("name" in r and "exact_quote 缺失" in r for r in rejected)
    assert any("city" in r and "exact_quote 缺失" in r for r in rejected)


# ---------- 3 quote 非子串 ----------

def test_validate_quote_not_substring_rejects_field():
    schema = profile_field_schema()
    out = {"occupation": {"value": "产品经理",
                          "exact_quote": "这句话不在原文里"}}
    validated, rejected = _validate_with_grounding(out, USER1_TEXT, schema)
    assert validated == {}
    assert any("occupation" in r and "非原文逐字子串" in r for r in rejected)


def test_validate_exact_substring_no_fuzzy_matching():
    """空白差异/大小写变体都不算命中（禁止模糊匹配）。"""
    schema = profile_field_schema()
    out = {"name": {"value": "小王", "exact_quote": "小 王"}}  # 中间插空格
    validated, rejected = _validate_with_grounding(out, USER1_TEXT, schema)
    assert validated == {}
    assert any("非原文逐字子串" in r for r in rejected)


def test_validate_bare_value_tolerance_only_for_ungrounded_fields():
    """实测校准（真实链 flash 裸值形态）：字段级不强制 quote 的字段接受裸值；
    grounded=True 的标量仍严格要求包装（quote 即锚点）。"""
    schema = profile_field_schema()
    out = {
        "currency": "元",                       # grounded=False 标量：裸值容差
        "scenes": [{"scene_name": {"value": "地铁通勤", "exact_quote": "每天地铁通勤"},
                    "scene_priority": 1}],      # structured_list：裸列表容差
        "age": 28,                              # grounded=True 标量：裸值仍拒绝
    }
    validated, rejected = _validate_with_grounding(out, USER1_TEXT, schema)
    assert validated["currency"] == "元"
    assert validated["scenes"][0]["scene_name"] == "地铁通勤"
    assert validated["scenes"][0]["scene_priority"] == 1
    assert "age" not in validated
    assert any("age" in r and "{value, exact_quote}" in r for r in rejected)


# ---------- 4 结构缺键 ----------

def test_validate_structure_missing_keys():
    schema = profile_field_schema()
    for bad in ({}, {"unknown_key": {"value": 1, "exact_quote": None}}, "not-a-dict"):
        validated, rejected = _validate_with_grounding(bad, USER1_TEXT, schema)
        assert validated == {}
        assert rejected and all(r.startswith(STRUCTURE_MARKER) for r in rejected)


# ---------- 5/6 重试（extract 层，stub 网关） ----------

def _bad_structure_output() -> dict:
    return {"totally_wrong": {"value": "x", "exact_quote": "y"}}


def test_extract_profile_grounded_retry_success():
    first = _bad_structure_output()
    second = {
        "name": {"value": "小王", "exact_quote": "小王，28岁"},
        "budget_max": {"value": 2000, "exact_quote": "预算2000元左右"},
    }
    gateway = StubGateway([first, second])
    profile, rejected = extract_profile_grounded(USER1_TEXT, gateway, {})
    assert profile["name"] == "小王"
    assert profile["budget_max"] == 2000
    assert rejected == []
    assert len(gateway.calls) == 2
    # 修复重试必须把校验错误清单附回给模型
    retry_user_msg = gateway.calls[1]["messages"][-1]["content"]
    assert "结构" in retry_user_msg and "JSON" in retry_user_msg


def test_extract_profile_grounded_retry_still_fails_raises():
    gateway = StubGateway([_bad_structure_output(), _bad_structure_output()])
    with pytest.raises(GatewayError):
        extract_profile_grounded(USER1_TEXT, gateway, {})
    assert len(gateway.calls) == 2  # 恰一次修复重试（任务口径）


def test_extract_profile_grounded_deep_normalization_reused():
    """深度归一复用 profile_extractor：场景排序/预算回填等既有行为保持。"""
    model_out = {
        "budget_raw": {"value": "预算2000元左右", "exact_quote": "预算2000元左右"},
        "budget_max": {"value": None, "exact_quote": None},
        "scenes": {"value": [
            {"scene_name": {"value": "咖啡馆办公", "exact_quote": "咖啡馆办公"},
             "scene_priority": {"value": 2, "exact_quote": None}},
            {"scene_name": {"value": "地铁通勤", "exact_quote": "地铁通勤"},
             "scene_priority": {"value": 1, "exact_quote": None}},
        ], "exact_quote": None},
    }
    profile, rejected = extract_profile_grounded(USER1_TEXT, StubGateway([model_out]), {})
    assert rejected == []
    # 预算数值从 budget_raw 确定性回填（既有归一逻辑）
    assert profile["budget_max"] == 2000
    # 场景按优先级排序（既有归一逻辑）
    assert [s["scene_name"] for s in profile["scenes"]] == ["地铁通勤", "咖啡馆办公"]


# ---------- 产品 grounded 抽取（stub 网关全链） ----------

def _p001_grounded_output(records, concat) -> dict:
    cat_quote = _p001_source_quote(records, "产品类别")
    name_quote = _p001_source_quote(records, "Ralun Pro")
    return {
        "product_name": {"value": "Ralun Pro", "exact_quote": name_quote},
        "brand": {"value": "Zurmek", "exact_quote": name_quote},
        "model": {"value": "Ralun Pro", "exact_quote": name_quote},
        "aliases": {"value": ["P001"], "exact_quote": None},
        "category": {"value": [{
            "raw_value": "骨传导游泳耳机", "normalized_value": None, "unit": None,
            "fact_kind": "官网声明", "status": "有支持", "conditions": [],
            "applicable_variant": None, "as_of": None, "exact_quote": cat_quote,
        }], "exact_quote": None},
        "battery_by_mode": {"value": [
            {"raw_value": "最长9小时", "normalized_value": 9, "unit": "小时",
             "fact_kind": "官网声明", "status": "有支持", "conditions": [],
             "applicable_variant": "蓝牙模式", "as_of": None,
             "exact_quote": _p001_source_quote(records, "蓝牙模式最长9小时")},
        ], "exact_quote": None},
    }


def test_extract_product_grounded_full_chain(p001_records):
    records, concat = p001_records
    from src.product_registry import build_registry
    registry = build_registry(load_input(SAMPLE_DIR).source_records)
    record = next(p for p in registry.all() if p.canonical_id == "P001")
    gateway = StubGateway([_p001_grounded_output(records, concat)])
    draft, rejected = extract_product_grounded(record, records, gateway, {})
    assert rejected == []
    assert draft["canonical_id"] == "P001"
    assert draft["product_name"] == "Ralun Pro"
    assert draft["brand"] == "Zurmek"
    assert draft["aliases"] == ["P001", "P01"]  # registry 权威别名并集（P01≡P001）
    # v0.5.1：官网【型号】块五字段停采——stub 输出里带着 category，
    # 但 Schema 白名单不收录，校验层静默忽略后草稿落空列表
    for key in ("category", "generation", "core_functions", "audio_formats",
                "wearing_design"):
        assert draft.get(key) == []
    # 观察被归一为旧草稿形态，evidence 由代码定位回 (source_id, span_id)
    assert draft["battery_by_mode"][0]["applicable_variant"] == "蓝牙模式"
    ref = draft["battery_by_mode"][0]["evidence_refs"][0]
    assert ref["exact_quote"] and ref["source_id"] and "span_id" in ref


def test_extract_product_grounded_drops_unanchored_observation(p001_records):
    records, concat = p001_records
    from src.product_registry import build_registry
    registry = build_registry(load_input(SAMPLE_DIR).source_records)
    record = next(p for p in registry.all() if p.canonical_id == "P001")
    out = _p001_grounded_output(records, concat)
    out["warranty"] = {"value": [{
        "raw_value": "24个月", "normalized_value": None, "unit": None,
        "fact_kind": "官网声明", "status": "有支持", "conditions": [],
        "applicable_variant": None, "as_of": None,
        "exact_quote": "保修24个月（编造，非原文）",
    }], "exact_quote": None}
    draft, rejected = extract_product_grounded(record, records, StubGateway([out]), {})
    assert draft["warranty"] == []          # 被拒观察整条丢弃
    assert any("warranty" in r for r in rejected)
    assert draft["battery_by_mode"]         # 其余字段不受影响


def test_extract_product_grounded_identity_requires_verbatim_value(p001_records):
    """身份字段值级原样沿用：值不在五源原文中（quote 锚定不住值）回落 registry 原样值。"""
    records, concat = p001_records
    from src.product_registry import build_registry
    registry = build_registry(load_input(SAMPLE_DIR).source_records)
    record = next(p for p in registry.all() if p.canonical_id == "P001")
    out = _p001_grounded_output(records, concat)
    out["product_name"] = {"value": "Ralun 虚构变体",  # 源文与登记值中均不存在
                           "exact_quote": out["product_name"]["exact_quote"]}
    draft, rejected = extract_product_grounded(record, records, StubGateway([out]), {})
    assert draft["product_name"] == record.model_raw  # registry 权威原样值兜底
    assert out["product_name"]["value"] not in concat


def test_extract_product_grounded_structure_retry_still_fails(p001_records):
    records, concat = p001_records
    from src.product_registry import build_registry
    registry = build_registry(load_input(SAMPLE_DIR).source_records)
    record = next(p for p in registry.all() if p.canonical_id == "P001")
    gateway = StubGateway([{"oops": True}, {"oops": True}])
    with pytest.raises(GatewayError):
        extract_product_grounded(record, records, gateway, {})
    assert len(gateway.calls) == 2


# ---------- 字段级融合 ----------

def test_fuse_profile_fields_model_priority_fallback_fills():
    model = {"name": "小王", "budget_max": None, "scenes": [], "sound_preferences": ["浅色系"]}
    fallback = {"name": None, "budget_max": 2000, "budget_raw": "预算2000元左右",
                "scenes": [{"scene_name": "地铁通勤"}], "sound_preferences": []}
    merged = fuse_profile_fields(model, fallback)
    assert merged["name"] == "小王"            # 模型值优先
    assert merged["budget_max"] == 2000        # 缺失 → 正则兜底
    assert merged["budget_raw"] == "预算2000元左右"
    assert merged["scenes"] == [{"scene_name": "地铁通勤"}]
    assert merged["sound_preferences"] == ["浅色系"]  # 模型非空整字段采信（不混拼）


def test_fuse_product_drafts_model_priority_fallback_fills():
    model = {"canonical_id": "P001", "product_name": "Ralun Pro", "brand": None,
             "model": "Ralun Pro", "aliases": ["P001", "P01"],
             "category": [{"raw_value": "x"}], "charging": []}
    fallback = {"canonical_id": "P001", "product_name": "Ralun Pro", "brand": "Zurmek",
                "model": "Ralun Pro", "aliases": ["P001"],
                "category": [], "charging": [{"raw_value": "10分钟快充"}]}
    merged = fuse_product_drafts(model, fallback)
    assert merged["product_name"] == "Ralun Pro"
    assert merged["brand"] == "Zurmek"                 # 模型 None → 兜底
    assert merged["aliases"] == ["P001", "P01"]        # 并集
    assert merged["category"] == [{"raw_value": "x"}]  # 模型非空优先
    assert merged["charging"] == [{"raw_value": "10分钟快充"}]  # 模型空 → 兜底


# ---------- Prompt 模板 ----------

def test_grounded_profile_prompt_renders_user_text():
    rendered = render_grounded_profile_prompt("示例输入正文XYZ")
    assert "示例输入正文XYZ" in rendered
    assert "{{USER_TEXT}}" not in rendered


def test_prompts_contain_no_real_sample_values():
    """提示词卫生：禁止出现样例数据的真实取值（历史教训：示例值污染）。"""
    profile_banned = ["小王", "小李", "小张", "上海", "北京", "Velmora", "禾岚",
                      "Tasvin", "Norvek", "2000元", "500元", "预算3000", "地铁通勤",
                      "咖啡馆办公", "播客"]
    product_banned = ["Ralun", "Larvo", "Vondir", "Sorbik", "Zurmek", "VD-140",
                      "小王", "禾岚", "Velmora", "骨传导游泳耳机", "IP68", "IPX8"]
    profile_prompt = (REPO_ROOT / PROFILE_GROUNDED_PROMPT).read_text(encoding="utf-8")
    product_prompt = (REPO_ROOT / PRODUCT_GROUNDED_PROMPT).read_text(encoding="utf-8")
    for token in profile_banned:
        assert token not in profile_prompt, f"画像 Prompt 含真实示例值: {token}"
    for token in product_banned:
        assert token not in product_prompt, f"产品 Prompt 含真实示例值: {token}"


def test_prompts_require_wrapped_value_and_quote():
    profile_prompt = (REPO_ROOT / PROFILE_GROUNDED_PROMPT).read_text(encoding="utf-8")
    product_prompt = (REPO_ROOT / PRODUCT_GROUNDED_PROMPT).read_text(encoding="utf-8")
    for prompt in (profile_prompt, product_prompt):
        assert '"value"' in prompt and '"exact_quote"' in prompt
        assert "逐字" in prompt and "null" in prompt


def test_official_model_fields_suppressed_everywhere():
    """v0.5.1：官网【型号】块五字段在 Schema 与 Prompt 两层都停采（11 分形态对齐）。"""
    schema = product_field_schema()["fields"]
    for key in PRODUCT_SUPPRESSED_FIELDS:
        assert key not in schema, f"停采集字段仍在 Schema 白名单: {key}"
    product_prompt = (REPO_ROOT / PRODUCT_GROUNDED_PROMPT).read_text(encoding="utf-8")
    for key in PRODUCT_SUPPRESSED_FIELDS:
        assert f"`{key}`" not in product_prompt, f"产品 Prompt 字段目录仍含停采集键: {key}"
