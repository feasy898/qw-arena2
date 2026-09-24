# -*- coding: utf-8 -*-
"""tests/unit/test_product_extractor.py — 五源产品事实抽取单元测试（离线 mock 网关）。

夹具：tests/fixtures/gateway/responses.json（黄金夹具，全部数据逐字取自
raw/dataset_sample/Data_for_Users/ 真实文件，构建时逐条校验引文可回溯）。
覆盖：编号归一后产品全覆盖（P01≡P001）、草稿字段键完整、evidence_refs 可回溯到
真实 span、宣传话术不当事实、无统一口径标待核验、一次修复重试后失败。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.input_adapter import load_input
from src.model_gateway import (
    GatewayError,
    MockResponseMissingError,
    ModelGateway,
)
from src.product_extractor import (
    LIST_FIELDS,
    _field_keys,
    extract_products,
    render_product_prompt,
    render_sources_block,
)
from src.product_registry import ProductRecord, build_registry
from src.schemas import SourceType
from src.source_index import SourceIndex

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "gateway" / "responses.json"


def make_config() -> dict:
    return {"default_model": "qwen3.6-plus", "max_retries_per_request": 2,
            "mock": {"enabled": True, "force_env": "QW_FORCE_MOCK",
                     "fixture_path": str(FIXTURE_PATH)}}


@pytest.fixture(scope="module")
def env():
    bundle = load_input(SAMPLE_DIR)  # 资料池含 6 份用户描述 → 仅 warning，不影响产品五源
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    gateway = ModelGateway(make_config())
    drafts = extract_products(registry.all(), index, gateway, make_config())
    return bundle, registry, index, drafts


# ---------- 编号归一后产品全覆盖 ----------

def test_all_products_covered_after_id_normalization(env):
    _bundle, registry, _index, drafts = env
    assert registry.ids() == ["P001", "P002", "P003", "P004", "P005"]
    assert sorted(drafts) == ["P001", "P002", "P003", "P004", "P005"]


def test_p01_alias_records_indexed_under_p001(env):
    _bundle, _registry, index, drafts = env
    records = index.by_product("P001")  # P01 ≡ P001（尾号对齐）
    source_ids = {r.source_id for r in records}
    assert any("P01_Product_Coverage" in s for s in source_ids)
    assert any("P01_Third_Party_Review" in s for s in source_ids)
    assert any("P001_RalunPro_Official_Site" in s for s in source_ids)
    assert drafts["P001"]["aliases"] == ["P001", "P01"]
    assert drafts["P002"]["aliases"] == ["P002", "P02"]


def test_five_sources_collected_per_product(env):
    _bundle, _registry, index, _drafts = env
    types = {str(r.source_type) for r in index.by_product("P005")}
    assert types == {str(SourceType.OFFICIAL_SITE), str(SourceType.ECOMMERCE_LISTING),
                     str(SourceType.MEDIA_COVERAGE), str(SourceType.THIRD_PARTY_REVIEW),
                     str(SourceType.USER_FEEDBACK)}


# ---------- 草稿结构与真值 ----------

def test_draft_keys_complete_and_observations_are_lists(env):
    _bundle, _registry, _index, drafts = env
    expected = set(_field_keys())
    identity = {"canonical_id", "aliases", "product_name", "brand", "model"}
    for cid, draft in drafts.items():
        assert expected <= set(draft), cid
        for key in expected - identity:
            assert isinstance(draft[key], list), (cid, key)
            for obs in draft[key]:
                assert isinstance(obs, dict), (cid, key)


def test_p001_ground_truth_values(env):
    _bundle, _registry, _index, drafts = env
    draft = drafts["P001"]
    assert draft["brand"] == "Zurmek"
    assert draft["model"] == "Ralun Pro"
    assert draft["product_name"] == "Ralun Pro"
    storage = [o for o in draft["local_storage_gb"] if o.get("normalized_value") == 32]
    assert storage and storage[0]["unit"] == "GB"
    modes = {o["applicable_variant"]: o["raw_value"] for o in draft["battery_by_mode"]}
    assert modes["蓝牙模式"] == "最长9小时"
    assert modes["MP3模式"] == "最长6小时"  # 不同模式并列，禁止合并
    assert any(o["raw_value"] == "IP68" for o in draft["protection_rating"])
    assert any("24个月" in o["raw_value"] for o in draft["warranty"])
    assert draft["category"][0]["raw_value"] == "骨传导游泳耳机"


def test_p004_multi_mode_battery(env):
    _bundle, _registry, _index, drafts = env
    modes = {o["applicable_variant"]: o["raw_value"] for o in drafts["P004"]["battery_by_mode"]}
    assert modes["本地MP3"] == "最长约12小时"
    assert modes["蓝牙"] == "约4小时"


# ---------- evidence_refs 可回溯到真实 span ----------

def test_every_evidence_ref_traceable_to_real_span(env):
    _bundle, _registry, index, drafts = env
    checked = 0
    for cid, draft in drafts.items():
        records = {r.source_id: r for r in index.by_product(cid)}
        for key, observations in draft.items():
            if not isinstance(observations, list):
                continue
            for obs in observations:
                if not isinstance(obs, dict):
                    continue
                for ref in obs.get("evidence_refs") or []:
                    record = records[ref["source_id"]]
                    span = {s[0]: (s[1], s[2]) for s in record.spans}[ref["span_id"]]
                    span_text = record.original_text[span[0]:span[1]]
                    assert ref["exact_quote"] in span_text, (cid, key, ref)
                    checked += 1
    assert checked > 100  # 五源 × 5 产品，证据密度充足


def test_fact_kind_and_status_separated(env):
    _bundle, _registry, _index, drafts = env
    weight = drafts["P001"]["weight_g"][0]
    assert weight["fact_kind"] == "官网声明"   # 官网写约27g：有支持的官网声明
    assert weight["status"] == "有支持"        # 不自动升级为实测
    feedback_obs = drafts["P001"]["common_praise"][0]
    assert feedback_obs["fact_kind"] == "用户反馈"


# ---------- 宣传话术不当事实 / 待核验 ----------

def test_marketing_claim_not_leaked_into_facts_p002(env):
    _bundle, _registry, _index, drafts = env
    draft = drafts["P002"]
    # 官网+实测：不支持蓝牙；渠道宣称「支持蓝牙5.4和32GB」不得进入事实字段
    assert all(o["raw_value"] == "不支持蓝牙" for o in draft["bluetooth"])
    assert all(o["normalized_value"] == 4 for o in draft["local_storage_gb"])
    claims = " ".join(draft["marketing_claims"][0]["items"])
    assert "蓝牙5.4" in claims and "Ralun Pro" in claims


def test_p005_battery_no_unified_criteria_marked_pending(env):
    _bundle, _registry, _index, drafts = env
    obs = drafts["P005"]["battery_by_mode"]
    assert obs and all(o["raw_value"] is None for o in obs)
    assert all(o["status"] == "附条件" for o in obs)
    assert any("统一测试口径" in c for o in obs for c in o["conditions"])


def test_p004_channel_rating_claim_registered_as_marketing(env):
    _bundle, _registry, _index, drafts = env
    rating = drafts["P004"]["user_rating"]
    assert rating and rating[0]["fact_kind"] == "宣传表述"
    assert "5.0" in rating[0]["raw_value"]
    claims = " ".join(drafts["P004"]["marketing_claims"][0]["items"])
    assert "5万名" in claims  # 渠道宣称销量/评分不作为热度与评分事实


# ---------- 修复重试与缺失夹具 ----------

def test_structure_repair_retry_then_gateway_error(monkeypatch):
    import src.product_extractor as pe
    bundle = load_input(SAMPLE_DIR)
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    gateway = ModelGateway(make_config())
    monkeypatch.setattr(pe, "_mock_key", lambda cid: "profile_extraction_bad_schema")
    with pytest.raises(GatewayError) as excinfo:
        extract_products([registry.get("P001")], index, gateway, make_config())
    assert "一次修复重试仍不合法" in str(excinfo.value)


def test_missing_mock_key_raises_without_fallback(monkeypatch):
    import src.product_extractor as pe
    bundle = load_input(SAMPLE_DIR)
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    gateway = ModelGateway(make_config())
    monkeypatch.setattr(pe, "_mock_key", lambda cid: "no_such_product_key")
    with pytest.raises(MockResponseMissingError):
        extract_products([registry.get("P001")], index, gateway, make_config())


def test_product_without_sources_yields_empty_draft_without_model_call(monkeypatch):
    gateway = ModelGateway(make_config())

    def boom(*args, **kwargs):
        raise AssertionError("无来源产品不得调用模型")

    monkeypatch.setattr(gateway, "chat", boom)
    drafts = extract_products([ProductRecord(canonical_id="P099", original_ids=["P099"])],
                              SourceIndex([]), gateway, make_config())
    assert drafts["P099"]["canonical_id"] == "P099"
    assert drafts["P099"]["local_storage_gb"] == []


# ---------- Prompt 渲染 ----------

def test_render_product_prompt_embeds_sources(env):
    _bundle, _registry, index, _drafts = env
    records = index.by_product("P001")
    rendered = render_product_prompt("P001", ["P001", "P01"], records)
    assert "P001" in rendered
    assert "{{PRODUCT_ID}}" not in rendered and "{{SOURCES_BLOCK}}" not in rendered
    assert "[L15]" in rendered  # 官网续航行
    assert "source_id=04_Brand_Official_Sites/P001_RalunPro_Official_Site.txt" in rendered


def test_sources_block_keeps_verbatim_lines(env):
    bundle = load_input(SAMPLE_DIR)
    records = [r for r in bundle.source_records
               if r.source_type is SourceType.OFFICIAL_SITE
               and r.source_id.endswith("P001_RalunPro_Official_Site.txt")]
    block = render_sources_block(records)
    assert "[L17] 保修：24个月（以销售地区政策为准）" in block
