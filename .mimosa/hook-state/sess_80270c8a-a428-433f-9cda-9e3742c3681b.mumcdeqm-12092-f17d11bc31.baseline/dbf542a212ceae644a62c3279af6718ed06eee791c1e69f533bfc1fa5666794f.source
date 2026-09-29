# -*- coding: utf-8 -*-
"""降级链决策：网关不可达时，确定性抽取的展示价要能驱动真实推荐。

夹具：raw/dataset_sample/Data_for_Users 的用户 1（示例用户）+ 五款产品。
子进程测试按任务书构造不可达网关，不使用 mock。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

from src.constraint_engine import parse_price_scopes, text_of
from src.deterministic_extractor import deterministic_product_draft
from src.input_adapter import load_input
from src.pipeline import _decide, _freeze_and_render, prune_undated_battery_items
from src.product_registry import build_registry
from src.reconciler import reconcile_product
from src.renderer import parse_products, parse_recommendation, render_products
from src.source_index import SourceIndex

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
SOURCE_DIRS = (
    "01_Ecommerce_Listings",
    "02_Media_Coverage_and_Reviews",
    "03_User_Feedback_and_Complaints",
    "04_Brand_Official_Sites",
)
# 货架「展示价(元)」列。渠道报价区间不得冒充这个口径。
SHELF_PRICES = {"P001": 1098, "P002": 2948, "P003": 1845, "P004": 456, "P005": 1138}
FIELD_CITE_RE = re.compile(r"「(?:画像|产品属性)｜")
PRODUCT_ID_RE = re.compile(r"P\d{3}")
SUBPROCESS_TIMEOUT_SECONDS = 180


def _sample():
    bundle = load_input(SAMPLE_DIR)
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    return registry, index


def _rendered_products():
    registry, index = _sample()
    drafts = {}
    for product in registry.all():
        drafts[product.canonical_id] = deterministic_product_draft(
            product, index.by_product(product.canonical_id))
    finals = {
        cid: reconcile_product(draft, index.by_product(cid), {})
        for cid, draft in drafts.items()
    }
    rendered = render_products(prune_undated_battery_items(finals))
    return drafts, index, parse_products(rendered)


def _single_user_input(staging: Path) -> Path:
    """示例资料池 → 恰一份用户描述（User_Description_1）+ 全部产品资料。"""
    if staging.exists():
        shutil.rmtree(staging)
    (staging / "00_User_Descriptions").mkdir(parents=True)
    shutil.copy2(
        SAMPLE_DIR / "00_User_Descriptions" / "User_Description_1.txt",
        staging / "00_User_Descriptions" / "User_Description_1.txt",
    )
    for dirname in SOURCE_DIRS:
        shutil.copytree(SAMPLE_DIR / dirname, staging / dirname)
    return staging


def test_listing_display_price_has_numeric_value_and_listed_scope():
    """展示价有数值，且回读后的展示价口径等于货架数字，不是渠道区间。"""
    drafts, index, products = _rendered_products()
    for canonical_id, expected in SHELF_PRICES.items():
        listing = [
            obs for obs in drafts[canonical_id]["current_price"]
            if obs.get("normalized_value") == expected
        ]
        assert len(listing) == 1, canonical_id
        obs = listing[0]
        assert isinstance(obs["normalized_value"], int)
        assert obs["raw_value"] == str(expected)
        assert obs["fact_kind"] == "有依据归纳"
        notes = obs.get("conditions") or []
        assert notes, canonical_id
        listing_records = [
            record for record in index.by_product(canonical_id)
            if "01_Ecommerce_Listings" in record.source_id
        ]
        assert listing_records
        for note in notes:
            assert note in listing_records[0].original_text
        text = text_of(products[canonical_id].get("current_price"))
        scopes = parse_price_scopes(text or "")
        listed = [scope for scope in scopes if scope["listed"]]
        assert listed, (canonical_id, text, scopes)
        assert listed[0]["lo"] == expected
        assert listed[0]["hi"] == expected


def test_degrade_decision_on_sample_user_selects_by_shelf_price(tmp_path):
    """不经网关：示例用户的降级决策入选三款，理由引用字段，2948 因超出预算落选。"""
    from src.deterministic_extractor import deterministic_profile
    from src.renderer import render_recommendation

    input_dir = _single_user_input(tmp_path / "input")
    bundle = load_input(input_dir)
    registry = build_registry(bundle.source_records)
    index = SourceIndex(bundle.source_records)
    profile = deterministic_profile(bundle.user_text or "")
    drafts = {
        product.canonical_id: deterministic_product_draft(
            product, index.by_product(product.canonical_id))
        for product in registry.all()
    }
    finals = {
        cid: reconcile_product(draft, index.by_product(cid), {})
        for cid, draft in drafts.items()
    }
    _profile_md, _products_md, frozen_profile, frozen_products = _freeze_and_render(
        profile, prune_undated_battery_items(finals))
    _constraints, _valid, plan = _decide(frozen_profile, frozen_products)
    parsed = parse_recommendation(render_recommendation(plan))
    _assert_three_real_recommendations(parsed)


def test_agent_unreachable_gateway_recommends_real_products(tmp_path):
    """子进程：密钥无效且网关地址不可达时，推荐表 3 行仍是真实产品并引用字段。"""
    input_dir = _single_user_input(tmp_path / "input")
    output_dir = tmp_path / "output"
    output_dir.mkdir()
    prompt = f"请根据 {input_dir} 中的数据完成消费决策任务，将结果输出到 {output_dir}"
    env = dict(os.environ)
    env["DASHSCOPE_API_KEY"] = "sk-invalid"
    env["OPENAI_BASE_URL"] = "https://10.255.255.1.invalid/v1"
    env.pop("QW_FORCE_MOCK", None)
    env["AGENT_LOG_DIR"] = str(tmp_path / "logs")
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "agent" / "agent.py"), "--prompt", prompt],
        cwd=str(REPO_ROOT),
        env=env,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    assert proc.returncode == 0, (proc.returncode, proc.stderr[-2000:], proc.stdout[-500:])
    recommendation = (output_dir / "recommendation.md").read_text(encoding="utf-8")
    parsed = parse_recommendation(recommendation)
    _assert_three_real_recommendations(parsed)


def _assert_three_real_recommendations(parsed: dict) -> None:
    rows = parsed["recommendations"]
    assert len(rows) == 3
    ids = [row["product_id"] for row in rows]
    assert ids == ["P004", "P001", "P005"], ids
    for row in rows:
        assert row["product_id"] and PRODUCT_ID_RE.fullmatch(row["product_id"])
        blob = f"{row['reason']}\n{row['notes']}"
        assert FIELD_CITE_RE.search(blob), blob[:240]
        assert "当前售价" in blob
    by_id = {row["product_id"]: row for row in rows}
    assert "456" in f"{by_id['P004']['reason']}{by_id['P004']['notes']}"
    assert "1098" in f"{by_id['P001']['reason']}{by_id['P001']['notes']}"
    excluded = {
        pid: item
        for item in parsed["exclusions"]
        for pid in item["product_ids"]
    }
    assert "P002" in excluded
    reason = excluded["P002"]["reason"]
    assert "2948" in reason
    assert "当前售价" in reason
    assert FIELD_CITE_RE.search(reason)
