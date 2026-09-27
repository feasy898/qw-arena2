# -*- coding: utf-8 -*-
"""tests/unit/test_deterministic_extractor.py — 确定性基线抽取器。

夹具：raw/dataset_sample/Data_for_Users。不调用模型。
"""
from __future__ import annotations

import json
from pathlib import Path

from src.deterministic_extractor import (
    deterministic_product_draft,
    deterministic_profile,
)
from src.input_adapter import load_input
from src.product_extractor import empty_draft
from src.product_registry import build_registry
from src.profile_extractor import LISTS, SCALARS
from src.schemas import SourceType
from src.source_index import SourceIndex

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
MODULE_PATH = REPO_ROOT / "src" / "deterministic_extractor.py"

_PROFILE_LIST_KEYS = (
    "purchase_purposes", "devices", "brand_preferences", "brand_avoidances",
    "appearance_preferences", "sound_preferences", "functional_preferences",
    "service_preferences", "other_preferences",
)


def _load():
    bundle = load_input(SAMPLE_DIR)
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    return bundle, registry, index


def _drafts():
    _bundle, registry, index = _load()
    drafts = {}
    records = {}
    for product in registry.all():
        rows = index.by_product(product.canonical_id)
        records[product.canonical_id] = rows
        drafts[product.canonical_id] = deterministic_product_draft(product, rows)
    return registry, records, drafts


def _blob(value) -> str:
    return json.dumps(value, ensure_ascii=False, default=str)


def assert_evidence_refs_verbatim(draft: dict, records: list) -> None:
    """遍历草稿中全部 evidence_refs，断言 exact_quote 是来源原文的逐字子串。"""
    by_id = {record.source_id: record for record in records}
    found = 0

    def walk(node):
        nonlocal found
        if isinstance(node, dict):
            refs = node.get("evidence_refs")
            if isinstance(refs, list):
                for ref in refs:
                    found += 1
                    assert isinstance(ref, dict)
                    quote = ref.get("exact_quote")
                    source_id = ref.get("source_id")
                    span_id = ref.get("span_id")
                    assert isinstance(quote, str) and quote
                    assert source_id in by_id, source_id
                    record = by_id[source_id]
                    assert quote in record.original_text
                    span_map = {item[0]: (item[1], item[2]) for item in record.spans}
                    assert span_id in span_map, span_id
                    start, end = span_map[span_id]
                    assert quote in record.original_text[start:end]
            raw_value = node.get("raw_value")
            if isinstance(raw_value, str) and refs:
                assert raw_value in by_id[refs[0]["source_id"]].original_text
            items = node.get("items")
            if isinstance(items, list) and refs:
                source_text = by_id[refs[0]["source_id"]].original_text
                for item in items:
                    assert isinstance(item, str) and item in source_text
            variant = node.get("applicable_variant")
            if variant and refs:
                assert variant in by_id[refs[0]["source_id"]].original_text
            for child in node.values():
                walk(child)
        elif isinstance(node, list):
            for child in node:
                walk(child)

    walk(draft)
    assert found > 0


def _user_text(bundle, number: int) -> str:
    suffix = f"User_Description_{number}.txt"
    for record in bundle.source_records:
        if record.source_type == SourceType.USER_DESCRIPTION and record.source_id.endswith(suffix):
            return record.original_text
    raise AssertionError(f"缺少 {suffix}")


def test_module_does_not_depend_on_model_stack():
    source = MODULE_PATH.read_text(encoding="utf-8")
    import_lines = [
        line.strip() for line in source.splitlines()
        if line.strip().startswith(("import ", "from "))
    ]
    joined = "\n".join(import_lines)
    for banned in ("model_gateway", "profile_extractor", "product_extractor", "pipeline", "requests"):
        assert banned not in joined


def test_profile_keys_match_extractor_profile():
    profile = deterministic_profile("预算1000元左右")
    assert set(profile) == set(SCALARS) | set(LISTS) | {"scenes"}
    for key in _PROFILE_LIST_KEYS:
        assert isinstance(profile[key], list)
    assert isinstance(profile["scenes"], list)


def test_user1_budget_and_commute_scene():
    bundle, _registry, _index = _load()
    text = _user_text(bundle, 1)
    profile = deterministic_profile(text)
    assert profile["budget_max"] == 2000
    assert profile["budget_min"] is None
    assert profile["budget_semantics"] == "约(单值)"
    assert profile["budget_raw"] in text
    assert "2000" in profile["budget_raw"]
    assert "元" in profile["budget_raw"]
    assert profile["currency"] == "元"
    names = [scene["scene_name"] for scene in profile["scenes"]]
    assert any("通勤" in name for name in names)
    for scene in profile["scenes"]:
        assert set(scene) == {
            "scene_name", "usage_duration", "usage_frequency",
            "scene_priority", "priority_basis", "scene_evidence",
        }
        assert scene["scene_name"] in text
        assert scene["scene_evidence"] in text
        assert scene["scene_name"] in scene["scene_evidence"]
        # 时长、频率不推断（原文虽有「40分钟」「每天」，也不写入）
        assert scene["usage_duration"] is None
        assert scene["usage_frequency"] is None
        assert scene["priority_basis"] == "优先级未明确"
    for key in _PROFILE_LIST_KEYS:
        for item in profile[key]:
            assert item in text
    assert any("Velmora" in item for item in profile["devices"])
    assert profile["ecosystem"] == "安卓"
    assert profile["ecosystem"] in text
    if profile["ecosystem_notes"]:
        assert profile["ecosystem_notes"] in text


def test_budget_range_upper_and_approximate_patterns():
    upper = deterministic_profile("想买耳机，预算2000元以内。")
    assert upper["budget_max"] == 2000
    assert upper["budget_semantics"] == "上限"
    assert upper["budget_raw"] in "想买耳机，预算2000元以内。"

    approx = deterministic_profile("大概800元左右")
    assert approx["budget_max"] == 800
    assert approx["budget_semantics"] == "约(单值)"
    assert approx["budget_raw"] in "大概800元左右"

    ranged = deterministic_profile("预算1500-2000元")
    assert ranged["budget_min"] == 1500
    assert ranged["budget_max"] == 2000
    assert ranged["budget_semantics"] == "区间"

    block = deterministic_profile("预算大概在1500-2000元之间")
    assert block["budget_min"] == 1500
    assert block["budget_max"] == 2000
    assert block["budget_semantics"] == "区间"
    assert "预算大概在1500-2000元之间" == block["budget_raw"]


def test_sample_users_budget_follow_source_text():
    bundle, _registry, _index = _load()
    user5 = deterministic_profile(_user_text(bundle, 5))
    assert user5["budget_semantics"] == "区间"
    assert user5["budget_min"] == 1500
    assert user5["budget_max"] == 2000
    assert any(scene["scene_name"] == "游泳" for scene in user5["scenes"])

    user6 = deterministic_profile(_user_text(bundle, 6))
    assert user6["budget_min"] == 1500
    assert user6["budget_max"] == 2000
    assert user6["budget_semantics"] == "区间"


def test_draft_shape_matches_empty_draft_when_no_sources():
    _bundle, registry, _index = _load()
    product = registry.get("P001")
    base = empty_draft(product)
    draft = deterministic_product_draft(product, [])
    assert set(draft) == set(base)
    assert draft["canonical_id"] == base["canonical_id"]
    assert draft["aliases"] == base["aliases"]
    assert draft["brand"] == base["brand"]
    assert draft["model"] == base["model"]
    assert draft["product_name"] is None
    for key, value in draft.items():
        if key in {"canonical_id", "brand", "model", "product_name"}:
            continue
        assert isinstance(value, list)
        if key != "aliases":
            assert value == []


def test_p001_brand_and_named_listing_facts():
    """P001 品牌=Zurmek。样例货架里展示价 456 在 P004，已售2000+ 在 P005。"""
    registry, records, drafts = _drafts()
    assert registry.get("P001").brand_raw == "Zurmek"
    assert drafts["P001"]["brand"] == "Zurmek"
    assert "1098" in _blob(drafts["P001"]["current_price"])
    assert "已售25" in _blob(drafts["P001"]["sales_volume"])
    assert "456" in _blob(drafts["P004"]["current_price"])
    assert "2000+" in _blob(drafts["P005"]["sales_volume"])
    for canonical_id, draft in drafts.items():
        assert_evidence_refs_verbatim(draft, records[canonical_id])
        for field_key in ("current_price", "original_price"):
            for obs in draft[field_key]:
                assert obs["fact_kind"] == "有依据归纳"
                assert obs["fact_kind"] != "实测观察"
                assert obs["fact_kind"] != "官网声明"


def test_p005_official_brand_overview_filled():
    _registry, records, drafts = _drafts()
    draft = drafts["P005"]
    assert draft["brand_origin_market"]
    assert draft["brand_category_focus"]
    origin = _blob(draft["brand_origin_market"])
    focus = _blob(draft["brand_category_focus"])
    assert "国内音频品牌" in origin
    assert "骨传导" in focus
    assert draft["brand_origin_market"][0]["fact_kind"] == "官网声明"
    assert_evidence_refs_verbatim(draft, records["P005"])


def test_official_whitelist_fields_have_verbatim_values():
    _registry, records, drafts = _drafts()
    p001 = drafts["P001"]
    assert "IP68" in _blob(p001["protection_rating"])
    assert "27" in _blob(p001["weight_g"])
    assert "无主动降噪" in _blob(p001["noise_cancellation"])
    assert "9" in _blob(p001["battery_by_mode"])
    assert "6" in _blob(p001["battery_by_mode"])
    assert "32" in _blob(p001["local_storage_gb"])
    assert "24个月" in _blob(p001["warranty"])
    assert "快充" in _blob(p001["charging"])
    assert any("淡水" in _blob(obs) for obs in p001["waterproof_conditions"])
    # P005 续航原文是未披露口径，不编造小时数
    battery = _blob(drafts["P005"]["battery_by_mode"])
    assert "未" in battery
    assert "小时" not in battery
    for canonical_id in drafts:
        base = empty_draft(_registry.get(canonical_id))
        assert set(drafts[canonical_id]) == set(base)
        assert_evidence_refs_verbatim(drafts[canonical_id], records[canonical_id])
