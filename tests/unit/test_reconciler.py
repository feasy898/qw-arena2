# -*- coding: utf-8 -*-
"""tests/unit/test_reconciler.py — 五源归并与冲突处理单元测试（确定性，无模型）。

覆盖：merge_fact_cells（空/单/一致/多模式并列/宣传冲突/宣传兜底/多口径并列/dict 形态）、
reconcile_product（终稿键集=field_catalog、品牌型号原样、续航分模式并列、
P001 水下蓝牙冲突三步法③）、find_coverage_gaps（缺失/冲突/条件未确认/产品整体缺失）。
夹具数据取自 raw/dataset_sample/Data_for_Users/ 真实文件（黄金夹具）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.input_adapter import load_input
from src.model_gateway import ModelGateway
from src.product_extractor import extract_products
from src.product_registry import build_registry
from src.reconciler import find_coverage_gaps, merge_fact_cells, reconcile_product
from src.schemas import EvidenceRef, FactCell, FactKind, FactStatus, SourceType
from src.source_index import SourceIndex

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
FIXTURE_PATH = REPO_ROOT / "tests" / "fixtures" / "gateway" / "responses.json"
CATALOG_KEYS = {f["key"]
                for g in json.loads(
                    (REPO_ROOT / "contracts" / "field_catalog.json").read_text(encoding="utf-8")
                )["product"]["groups"]
                for f in g["fields"]}


def make_config() -> dict:
    return {"default_model": "qwen3.6-plus", "max_retries_per_request": 2,
            "mock": {"enabled": True, "force_env": "QW_FORCE_MOCK",
                     "fixture_path": str(FIXTURE_PATH)}}


def cell(raw=None, kind=FactKind.OFFICIAL_CLAIM, status=FactStatus.SUPPORTED,
         variant=None, conditions=None, quote="q", source="s", normalized=None, unit=None):
    return FactCell(field_key="f", raw_value=raw, normalized_value=normalized, unit=unit,
                    fact_kind=kind, status=status, conditions=list(conditions or []),
                    applicable_variant=variant,
                    evidence_refs=[EvidenceRef(source_id=source, span_id="L1",
                                               exact_quote=quote)])


@pytest.fixture(scope="module")
def final_products():
    bundle = load_input(SAMPLE_DIR)
    registry = build_registry(bundle.source_records)
    gateway = ModelGateway(make_config())
    drafts = extract_products(registry.all(), SourceIndex(bundle.source_records),
                              gateway, make_config())
    final = {cid: reconcile_product(d, [], make_config())
             for cid, d in drafts.items()}
    return registry, drafts, final


# ---------- merge_fact_cells：冲突三步法 ----------

def test_merge_empty_is_missing():
    merged = merge_fact_cells([])
    assert merged.status is FactStatus.MISSING
    assert merged.raw_value is None


def test_merge_single_cell_passthrough():
    merged = merge_fact_cells([cell(raw="IP68")])
    assert merged.raw_value == "IP68"
    assert merged.status is FactStatus.SUPPORTED
    assert len(merged.evidence_refs) == 1


def test_merge_agreeing_sources_keeps_official_kind_unions_evidence():
    official = cell(raw="约27g", kind=FactKind.OFFICIAL_CLAIM, source="官网")
    measured = cell(raw="约27g", kind=FactKind.MEASURED, source="实测")
    merged = merge_fact_cells([measured, official])
    assert merged.raw_value == "约27g"
    assert merged.fact_kind is FactKind.OFFICIAL_CLAIM  # 一致取值不升级为实测（SPEC §4）
    assert merged.status is FactStatus.SUPPORTED
    assert {e.source_id for e in merged.evidence_refs} == {"官网", "实测"}


def test_merge_different_modes_parallel_not_averaged():
    blue = cell(raw="最长9小时", variant="蓝牙模式", normalized=9)
    mp3 = cell(raw="最长6小时", variant="MP3模式", normalized=6)
    merged = merge_fact_cells([blue, mp3])
    assert merged.status is FactStatus.CONDITIONAL
    assert "蓝牙模式" in merged.raw_value and "最长9小时" in merged.raw_value
    assert "MP3模式" in merged.raw_value and "最长6小时" in merged.raw_value
    assert merged.normalized_value is None  # 禁止取平均/合并成单一数字
    assert "7.5" not in (merged.raw_value or "")


def test_merge_marketing_vs_measured_conflict_marked_pending():
    measured = cell(raw="水下只能使用MP3模式", kind=FactKind.MEASURED, source="实测")
    claim = cell(raw="蓝牙5.4可在水下2米稳定串流", kind=FactKind.MARKETING,
                 status=FactStatus.CONDITIONAL, source="渠道")
    merged = merge_fact_cells([measured, claim])
    assert merged.status is FactStatus.CONFLICTED  # 三步法③：保留冲突并标待核验
    assert "水下只能使用MP3模式" in merged.raw_value
    assert "蓝牙5.4" in merged.raw_value  # 两组表述并列，不由模型择一
    assert any("待核验" in c for c in merged.conditions)


def test_merge_marketing_only_becomes_pending_without_value():
    claim = cell(raw="全球游泳耳机销量第一", kind=FactKind.MARKETING,
                 status=FactStatus.CONDITIONAL, source="渠道")
    merged = merge_fact_cells([claim])
    assert merged.raw_value is None  # 宣传不当事实
    assert merged.status is FactStatus.CONDITIONAL
    assert any("宣传" in c for c in merged.conditions)
    assert merged.evidence_refs[0].source_id == "渠道"  # 原文出处保留可溯


def test_merge_reliable_different_values_parallel_with_conditions():
    listing = cell(raw="展示价2948元", kind=FactKind.MEASURED,
                   conditions=["货架快照"], source="货架")
    report = cell(raw="渠道报价699-999元", kind=FactKind.MEASURED,
                  conditions=["测评快照"], source="报道")
    merged = merge_fact_cells([listing, report])
    assert merged.status is FactStatus.CONDITIONAL  # 三步法②：并列保留条件
    assert "2948" in merged.raw_value and "699-999" in merged.raw_value
    assert "货架快照" in merged.raw_value and "测评快照" in merged.raw_value


def test_merge_accepts_dict_cells():
    merged = merge_fact_cells([
        {"field_key": "f", "raw_value": "IP68", "fact_kind": "官网声明",
         "status": "有支持", "conditions": [],
         "evidence_refs": [{"source_id": "官网", "span_id": "L1", "exact_quote": "q"}]},
        cell(raw="IP68", kind=FactKind.MEASURED, source="实测"),
    ])
    assert isinstance(merged, FactCell)
    assert merged.raw_value == "IP68"
    assert merged.fact_kind is FactKind.OFFICIAL_CLAIM
    assert {e.source_id for e in merged.evidence_refs} == {"官网", "实测"}


# ---------- reconcile_product（真实 P001 草稿 → 终稿） ----------

def test_final_key_set_matches_field_catalog_exactly(final_products):
    _registry, _drafts, final = final_products
    for cid, obj in final.items():
        assert set(obj) == CATALOG_KEYS, cid


def test_final_identity_verbatim(final_products):
    _registry, _drafts, final = final_products
    obj = final["P001"]
    assert obj["canonical_id"] == "P001"
    assert obj["aliases"] == ["P001", "P01"]  # 原始写法全保留
    assert obj["brand"] == "Zurmek"           # 脱敏名原样
    assert obj["model"] == "Ralun Pro"
    assert obj["product_name"] == "Ralun Pro"


def test_final_battery_by_mode_parallel(final_products):
    _registry, _drafts, final = final_products
    items = final["P001"]["battery_by_mode"]
    modes = {i["mode"]: i["duration"] for i in items}
    assert modes == {"蓝牙模式": "最长9小时", "MP3模式": "最长6小时"}
    assert all(i["evidence_refs"] for i in items)


def test_final_bluetooth_underwater_conflict_three_steps(final_products):
    _registry, _drafts, final = final_products
    uw = final["P001"]["bluetooth_underwater"]
    assert uw["status"] == "存在冲突"
    assert "水下只能使用MP3模式" in uw["raw_value"]
    assert "蓝牙5.4" in uw["raw_value"]
    assert any("待核验" in c for c in uw["conditions"])


def test_final_brand_reputation_pending_not_fact(final_products):
    _registry, _drafts, final = final_products
    repu = final["P005"]["brand_reputation"]
    assert repu["raw_value"] is None
    assert repu["status"] == "附条件"
    assert any("全球游泳耳机销量第一" in c for c in repu["conditions"])
    claims = final["P005"]["marketing_claims"]
    assert any("销量第一" in c for c in claims)  # 宣传原文登记在案


def test_final_common_praise_carries_level_wording(final_products):
    _registry, _drafts, final = final_products
    praise = final["P001"]["common_praise"]
    assert len(praise) == 1
    assert "有一条反馈提及" in praise[0]      # 单条层级
    assert "代表性未知" in praise[0]          # 明示不能推及整体
    p5 = final["P005"]["common_praise"]
    assert len(p5) == 2 and all("代表性未知" in c for c in p5)


def test_final_price_dual_scope_kept(final_products):
    _registry, _drafts, final = final_products
    price = final["P002"]["current_price"]
    assert price["status"] == "附条件"
    assert "2948" in price["raw_value"] and "699-999" in price["raw_value"]


def test_reconcile_accepts_minimal_draft():
    draft = {"canonical_id": "P042", "aliases": ["P042"], "product_name": None,
             "brand": None, "model": None}
    final = reconcile_product(draft, [], {})
    assert set(final) == CATALOG_KEYS
    assert final["canonical_id"] == "P042"
    assert final["noise_cancellation"]["status"] == "输入缺失"


# ---------- find_coverage_gaps：反向覆盖检查 ----------

def test_gaps_classify_missing_conflict_pending(final_products):
    registry, _drafts, final = final_products
    gaps = find_coverage_gaps(final, registry)
    by_key = {(g["canonical_id"], g["field_key"]): g["gap"] for g in gaps}
    assert by_key[("P001", "bluetooth_underwater")] == "冲突"
    assert by_key[("P005", "battery_by_mode")] == "条件未确认"  # 无统一口径→待核验
    assert by_key[("P003", "bluetooth_underwater")] == "条件未确认"  # 仅渠道宣称
    assert by_key[("P002", "sales_volume")] == "缺失"  # 资料确实无销量
    assert by_key[("P005", "common_complaints")] == "缺失"  # 摘录无投诉内容


def test_gaps_report_whole_missing_product(final_products):
    registry, _drafts, final = final_products
    partial = {cid: obj for cid, obj in final.items() if cid != "P003"}
    gaps = find_coverage_gaps(partial, registry)
    assert {"canonical_id": "P003", "field_key": None, "gap": "缺失"} in gaps


def test_gaps_empty_for_synthetic_fully_covered_product(final_products):
    registry, _drafts, final = final_products

    def filled(value):
        return {"field_key": "k", "raw_value": value, "status": "有支持",
                "fact_kind": "官网声明", "conditions": [], "evidence_refs": []}

    complete = dict(final["P001"])
    for key in CATALOG_KEYS:
        if key in ("canonical_id", "aliases", "product_name", "brand", "model"):
            continue
        if key == "battery_by_mode":
            complete[key] = [{"mode": "蓝牙模式", "duration": "最长9小时",
                              "conditions": None, "evidence_refs": []}]
        elif key in ("core_functions", "audio_formats", "variants", "accessories_fit",
                     "special_features", "marketing_claims", "common_praise",
                     "common_complaints"):
            complete[key] = ["条目"]
        else:
            complete[key] = filled("取值")
    gaps = find_coverage_gaps({"P001": complete}, registry)
    assert [g for g in gaps if g["canonical_id"] == "P001"] == []
