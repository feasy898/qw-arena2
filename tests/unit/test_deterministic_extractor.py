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
from src.input_adapter import line_spans, load_input
from src.product_extractor import empty_draft
from src.product_registry import build_registry
from src.profile_extractor import LISTS, SCALARS
from src.schemas import ProductRecord, SourceRecord, SourceType
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


def _items(observations) -> list[str]:
    found = []
    for obs in observations:
        for item in obs.get("items") or []:
            found.append(item)
    return found


def _text_record(source_id: str, source_type: SourceType, text: str, path=None) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        source_type=source_type,
        original_text=text,
        spans=line_spans(text),
        path=str(path) if path else None,
    )


def _product(canonical_id: str) -> ProductRecord:
    return ProductRecord(
        canonical_id=canonical_id,
        original_ids=[canonical_id],
        brand_raw="测牌",
        model_raw="测型",
    )


def test_sample_media_price_ranges_and_marketing_claims():
    """示例报道：至少两款渠道报价区间，以及两款卖点宣称（宣传表述）。"""
    _registry, records, drafts = _drafts()
    ranges = {
        "P002": "699-999",
        "P003": "1499-1799",
        "P004": "999-1499",
        "P005": "1099-1499",
    }
    for canonical_id, token in ranges.items():
        matched = [obs for obs in drafts[canonical_id]["current_price"] if token in _blob(obs)]
        assert matched, canonical_id
        for obs in matched:
            assert obs["fact_kind"] == "有依据归纳"
            assert obs["fact_kind"] != "实测观察"
            assert obs["unit"] == "元"
            assert "Product_Coverage" in obs["evidence_refs"][0]["source_id"]
        assert_evidence_refs_verbatim(drafts[canonical_id], records[canonical_id])

    p001_claims = _items(drafts["P001"]["marketing_claims"])
    p002_claims = _items(drafts["P002"]["marketing_claims"])
    p004_claims = _items(drafts["P004"]["marketing_claims"])
    assert any("采用" in item for item in p001_claims)
    assert any("支持" in item and not item.startswith("不支持") for item in p002_claims)
    assert any("支持" in item for item in p004_claims)
    assert "不支持蓝牙" not in p002_claims
    # 仅「宣称」而无主打/采用/搭载/支持的句子不单列（如 P003 续航 18 小时）
    assert drafts["P003"]["marketing_claims"] == []
    for canonical_id in ("P001", "P002", "P004"):
        assert drafts[canonical_id]["marketing_claims"]
        assert all(obs["fact_kind"] == "宣传表述" for obs in drafts[canonical_id]["marketing_claims"])


def test_sample_feedback_praise_traceable_and_sample_note():
    """示例反馈：P001/P005 好评可回溯；不足 2 条注明样本不足；否定槽点不入库。"""
    _registry, records, drafts = _drafts()
    p001_items = _items(drafts["P001"]["common_praise"])
    p005_items = _items(drafts["P005"]["common_praise"])
    assert any("不错" in item for item in p001_items)
    assert any("好" in item for item in p005_items)
    assert any("舒服" in item for item in p005_items)
    assert all(obs["fact_kind"] == "用户反馈" for obs in drafts["P001"]["common_praise"])
    assert all(obs["fact_kind"] == "用户反馈" for obs in drafts["P005"]["common_praise"])
    assert any(
        "样本不足，不构成共性" in (obs.get("conditions") or [])
        for obs in drafts["P001"]["feedback_sample_note"]
    )
    assert drafts["P005"]["feedback_sample_note"]
    assert all(
        "样本不足，不构成共性" not in (obs.get("conditions") or [])
        for obs in drafts["P005"]["feedback_sample_note"]
    )
    # 「果断」「不会磨疼」不是槽点
    assert drafts["P005"]["common_complaints"] == []
    for canonical_id in ("P002", "P003", "P004"):
        assert drafts[canonical_id]["common_praise"] == []
        assert drafts[canonical_id]["common_complaints"] == []
        assert drafts[canonical_id]["feedback_sample_note"] == []
    for canonical_id in ("P001", "P005"):
        assert_evidence_refs_verbatim(drafts[canonical_id], records[canonical_id])
    # 样例实测转述规格，没有「实测/测得」，不得升级成实测续航
    for draft in drafts.values():
        assert all(obs["fact_kind"] != "实测观察" for obs in draft["battery_by_mode"])


def test_eval_fixture_measured_battery_two_products():
    """评测夹具两款都有「实测…小时」，记为附条件的实测观察。"""
    eval_dir = REPO_ROOT / "tests" / "fixtures" / "eval" / "valid" / "input"
    bundle = load_input(eval_dir)
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    expected = {"P001": ("7.5", "蓝牙模式"), "P002": ("6.8", "未区分模式")}
    for canonical_id, (hours, mode) in expected.items():
        rows = index.by_product(canonical_id)
        draft = deterministic_product_draft(registry.get(canonical_id), rows)
        measured = [
            obs for obs in draft["battery_by_mode"]
            if obs["fact_kind"] == "实测观察" and hours in (obs.get("raw_value") or "")
        ]
        assert measured, canonical_id
        for obs in measured:
            assert obs["status"] == "附条件"
            assert "第三方实测" in obs["conditions"]
            assert obs.get("applicable_variant") == mode
            assert "实测" in obs["evidence_refs"][0]["exact_quote"]
        assert_evidence_refs_verbatim(draft, rows)


def test_condition_phrase_noise_sentence_and_complaint_rows():
    """「在…条件下」、实测降噪/音质、槽点关键词各至少两例（原文可回溯）。"""
    media_a = _text_record(
        "02_Media_Coverage_and_Reviews/P09_Product_Coverage.txt",
        SourceType.MEDIA_COVERAGE,
        "发布日期：2026-03-01\n在音量50%条件下续航约8小时。\n",
    )
    media_b = _text_record(
        "02_Media_Coverage_and_Reviews/P08_Product_Coverage.txt",
        SourceType.MEDIA_COVERAGE,
        "在淡水2米条件下防水可用。\n",
    )
    draft_a = deterministic_product_draft(_product("P009"), [media_a])
    battery = draft_a["battery_by_mode"]
    assert any("8" in (obs.get("raw_value") or "") and "小时" in (obs.get("raw_value") or "") for obs in battery)
    assert any("在音量50%条件下" in (obs.get("conditions") or []) for obs in battery)
    assert all(obs["fact_kind"] == "有依据归纳" for obs in battery)
    assert_evidence_refs_verbatim(draft_a, [media_a])

    draft_b = deterministic_product_draft(_product("P008"), [media_b])
    waterproof = _blob(draft_b["waterproof_conditions"])
    assert "在淡水2米条件下" in waterproof
    assert draft_b["waterproof_conditions"][0]["fact_kind"] == "有依据归纳"
    assert draft_b["waterproof_conditions"][0]["status"] == "附条件"
    assert_evidence_refs_verbatim(draft_b, [media_b])

    review_a = _text_record(
        "02_Media_Coverage_and_Reviews/P09_Third_Party_Review.txt",
        SourceType.THIRD_PARTY_REVIEW,
        "测试日期：2026-03-02\n测试中降噪一般，地铁里仍能听到报站。\n",
    )
    review_b = _text_record(
        "02_Media_Coverage_and_Reviews/P08_Third_Party_Review.txt",
        SourceType.THIRD_PARTY_REVIEW,
        "实测音质偏薄，人声发闷。\n",
    )
    noise = deterministic_product_draft(_product("P009"), [review_a])
    sound = deterministic_product_draft(_product("P008"), [review_b])
    assert noise["noise_cancellation"]
    assert noise["noise_cancellation"][0]["fact_kind"] == "实测观察"
    assert noise["noise_cancellation"][0]["status"] == "附条件"
    assert noise["noise_cancellation"][0]["conditions"] == ["第三方实测"]
    assert "降噪" in noise["noise_cancellation"][0]["raw_value"]
    assert sound["acoustic_tech"]
    assert sound["acoustic_tech"][0]["fact_kind"] == "实测观察"
    assert "音质" in sound["acoustic_tech"][0]["raw_value"]
    assert "第三方实测" in sound["acoustic_tech"][0]["conditions"]
    assert_evidence_refs_verbatim(noise, [review_a])
    assert_evidence_refs_verbatim(sound, [review_b])

    feedback_path = SAMPLE_DIR / "03_User_Feedback_and_Complaints" / "User_Feedback_Excerpts.csv"
    row_poor = "P007,测型,渠道甲,2026/1/1,音质很差，听着闷，戴久了耳朵疼"
    row_drop = "P007,测型,渠道甲,2026/1/2,蓝牙老是断，用着很失望"
    feedback = [
        _text_record(
            "03_User_Feedback_and_Complaints/User_Feedback_Excerpts.csv#row90",
            SourceType.USER_FEEDBACK, row_poor, feedback_path,
        ),
        _text_record(
            "03_User_Feedback_and_Complaints/User_Feedback_Excerpts.csv#row91",
            SourceType.USER_FEEDBACK, row_drop, feedback_path,
        ),
    ]
    complained = deterministic_product_draft(_product("P007"), feedback)
    complaint_items = _items(complained["common_complaints"])
    assert any("差" in item for item in complaint_items)
    assert any("闷" in item for item in complaint_items)
    assert any("疼" in item for item in complaint_items)
    assert any("断" in item for item in complaint_items)
    assert any("失望" in item for item in complaint_items)
    assert all(obs["fact_kind"] == "用户反馈" for obs in complained["common_complaints"])
    for item in complaint_items:
        assert item in row_poor or item in row_drop
    assert_evidence_refs_verbatim(complained, feedback)
    # 两行槽点，样本条数不少于 2，不标「样本不足」
    assert all(
        "样本不足，不构成共性" not in (obs.get("conditions") or [])
        for obs in complained["feedback_sample_note"]
    )
