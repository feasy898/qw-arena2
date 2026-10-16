# -*- coding: utf-8 -*-
"""tests/unit/test_product_registry.py — 产品注册表与编号归一单元测试。

夹具：官方示例数据集 raw/dataset_sample/Data_for_Users（编号两位/三位混用、
5 款产品）+ tmp_path 合成记录（身份冲突、反馈不入册等样例数据不含的场景）。
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from src.input_adapter import InputBundle, load_input
from src.product_registry import (
    ProductRegistry,
    build_registry,
    normalize_product_id,
    record_product_ids,
)
from src.schemas import ProductRecord, SourceRecord, SourceType

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"


@pytest.fixture(scope="module")
def bundle() -> InputBundle:
    assert SAMPLE_DIR.is_dir(), f"示例数据集缺失：{SAMPLE_DIR}"
    return load_input(str(SAMPLE_DIR))


@pytest.fixture(scope="module")
def registry(bundle: InputBundle) -> ProductRegistry:
    return build_registry(bundle.source_records)


def official_record(filename: str) -> SourceRecord:
    """构造官网来源记录（注册表只解析文件名，无需真实文件存在）。"""
    rel = f"04_Brand_Official_Sites/{filename}"
    return SourceRecord(
        source_id=rel,
        source_type=SourceType.OFFICIAL_SITE,
        original_text="官网摘录",
        spans=[],
        path=rel.replace("/", os.sep),
    )


def coverage_record(filename: str) -> SourceRecord:
    rel = f"02_Media_Coverage_and_Reviews/{filename}"
    return SourceRecord(
        source_id=rel,
        source_type=SourceType.MEDIA_COVERAGE,
        original_text="报道正文",
        spans=[],
        path=rel.replace("/", os.sep),
    )


def make_csv(tmp_path: Path, dirname: str, filename: str, text: str) -> Path:
    dir_path = tmp_path / dirname
    dir_path.mkdir(parents=True, exist_ok=True)
    csv_path = dir_path / filename
    csv_path.write_text(text, encoding="utf-8")
    return csv_path


def csv_row_records(csv_path: Path, source_type: SourceType, rows: list[list[str]]) -> list[SourceRecord]:
    """按行构造 CSV 来源记录（raw_text 与 cells 同口径，供 record.path 读表头）。"""
    records = []
    for offset, cells in enumerate(rows, start=1):
        raw_text = ",".join(cells)  # 测试数据不含逗号/引号，直拼即可
        records.append(
            SourceRecord(
                source_id=f"{csv_path.parent.name}/{csv_path.name}#row{offset}",
                source_type=source_type,
                original_text=raw_text,
                spans=[(f"L{offset + 1}", 0, len(raw_text))],
                path=str(csv_path),
            )
        )
    return records


# ---------------------------------------------------------------------------
# normalize_product_id
# ---------------------------------------------------------------------------

class TestNormalizeProductId:
    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("P1", "P001"),
            ("P01", "P001"),
            ("P001", "P001"),
            ("P12", "P012"),
            ("P012", "P012"),
            ("P123", "P123"),
            ("P000", "P000"),
            (" P01 ", "P001"),
            ("p01", "P001"),
        ],
    )
    def test_valid(self, raw, expected):
        assert normalize_product_id(raw) == expected

    @pytest.mark.parametrize(
        "raw", ["", "X01", "P", "P1234", "P01a", "编号1", "PP01", "-1", None, 1]
    )
    def test_invalid_returns_none(self, raw):
        assert normalize_product_id(raw) is None


# ---------------------------------------------------------------------------
# build_registry：官方示例数据集
# ---------------------------------------------------------------------------

class TestRegistryOnSample:
    def test_exactly_five_products_p001_p005(self, registry: ProductRegistry):
        assert registry.ids() == ["P001", "P002", "P003", "P004", "P005"]
        assert [p.canonical_id for p in registry.all()] == registry.ids()

    def test_alias_two_and_three_digit_merged(self, registry: ProductRegistry):
        # 货架/反馈/官网为三位（P001），报道/实测为两位（P01）→ 同一款产品
        p1 = registry.get("P001")
        assert p1.original_ids == ["P001", "P01"]
        assert registry.get("P005").original_ids == ["P005", "P05"]

    def test_brand_raw_verbatim_from_listing(self, registry: ProductRegistry):
        assert registry.get("P001").brand_raw == "Zurmek"
        assert registry.get("P002").brand_raw == "Zurmek"
        assert registry.get("P003").brand_raw == "Yovrix"
        assert registry.get("P004").brand_raw == "Norvek"
        assert registry.get("P005").brand_raw == "珂沐"  # 脱敏名原样，不改写

    def test_model_raw_from_official_filename(self, registry: ProductRegistry):
        assert registry.get("P001").model_raw == "RalunPro"
        assert registry.get("P002").model_raw == "Ralun"
        assert registry.get("P003").model_raw == "Kandro2Pro"
        assert registry.get("P004").model_raw == "VD-140"
        assert registry.get("P005").model_raw == "SorbikPro5"

    def test_no_identity_conflicts_in_sample(self, registry: ProductRegistry):
        # 样例数据为一致数据集：官网型号均为货架「型号」列的子串（SKU 前后缀差异）
        assert registry.identity_conflicts == []

    def test_fields_left_empty(self, registry: ProductRegistry):
        assert all(p.fields == {} for p in registry.all())

    def test_get_missing_raises_keyerror(self, registry: ProductRegistry):
        with pytest.raises(KeyError):
            registry.get("P099")


# ---------------------------------------------------------------------------
# build_registry：合成场景（样例数据不含）
# ---------------------------------------------------------------------------

class TestRegistrySynthetic:
    def test_model_identity_conflict_registered_not_merged(self):
        records = [
            official_record("P001_ModelA_Official_Site.txt"),
            official_record("P001_ModelB_Official_Site.txt"),
        ]
        reg = build_registry(records)
        # 不静默合并：产品仍在册，冲突登记含两个来源的型号原文
        assert reg.ids() == ["P001"]
        assert len(reg.identity_conflicts) == 1
        conflict = reg.identity_conflicts[0]
        assert conflict["canonical_id"] == "P001"
        assert conflict["conflict"] == "同编号不同型号"
        values = {item["value"] for item in conflict["detail"]}
        assert values == {"ModelA", "ModelB"}
        assert all("source_id" in item and "source_type" in item for item in conflict["detail"])

    def test_model_priority_official_over_listing(self, tmp_path: Path):
        csv_path = make_csv(
            tmp_path,
            "01_Ecommerce_Listings",
            "Ecommerce_Listing_Snapshot.csv",
            "测试产品ID,品牌,型号\nP001,Zurmek,SR-210 / Ralun Pro\n",
        )
        records = csv_row_records(
            csv_path,
            SourceType.ECOMMERCE_LISTING,
            [["P001", "Zurmek", "SR-210 / Ralun Pro"]],
        ) + [official_record("P001_RalunPro_Official_Site.txt")]
        reg = build_registry(records)
        product = reg.get("P001")
        assert product.model_raw == "RalunPro"  # 官网文件名优先
        assert product.brand_raw == "Zurmek"
        assert reg.identity_conflicts == []  # SKU 前缀含官网型号 → 不算冲突

    def test_listing_only_model_used_when_no_official(self, tmp_path: Path):
        csv_path = make_csv(
            tmp_path,
            "01_Ecommerce_Listings",
            "Ecommerce_Listing_Snapshot.csv",
            "测试产品ID,品牌,型号\nP001,Zurmek,Kandro 2 Pro 多运动版\n",
        )
        reg = build_registry(csv_row_records(
            csv_path, SourceType.ECOMMERCE_LISTING, [["P001", "Zurmek", "Kandro 2 Pro 多运动版"]]
        ))
        assert reg.get("P001").model_raw == "Kandro 2 Pro 多运动版"

    def test_same_model_with_p7_p007_aliases_single_product(self, tmp_path: Path):
        # P1 评分项：同型号不同写法编号（P7/P007）归一为同一产品，不得拆成两款
        csv_path = make_csv(
            tmp_path,
            "01_Ecommerce_Listings",
            "Ecommerce_Listing_Snapshot.csv",
            "测试产品ID,品牌,型号\nP7,Zurmek,ModelX\n",
        )
        records = csv_row_records(
            csv_path, SourceType.ECOMMERCE_LISTING, [["P7", "Zurmek", "ModelX"]]
        ) + [official_record("P007_ModelX_Official_Site.txt"), coverage_record("P07_Product_Coverage.txt")]
        reg = build_registry(records)
        assert reg.ids() == ["P007"]
        # original_ids 为原始写法集合的字典序（'P007' < 'P07' < 'P7'）
        assert reg.get("P007").original_ids == ["P007", "P07", "P7"]

    def test_brand_conflict_registered(self, tmp_path: Path):
        csv_path = make_csv(
            tmp_path,
            "01_Ecommerce_Listings",
            "Ecommerce_Listing_Snapshot.csv",
            "测试产品ID,品牌,型号\nP001,BrandA,ModelX\nP001,BrandB,ModelX\n",
        )
        reg = build_registry(csv_row_records(
            csv_path,
            SourceType.ECOMMERCE_LISTING,
            [["P001", "BrandA", "ModelX"], ["P001", "BrandB", "ModelX"]],
        ))
        kinds = [c["conflict"] for c in reg.identity_conflicts]
        assert "同编号不同品牌" in kinds
        assert reg.get("P001").brand_raw == "BrandA"  # 原文保留，取装载序首个

    def test_feedback_csv_never_creates_products(self, tmp_path: Path):
        # rules.json registry_scope：产品全集 = 官网 ∪ 货架 ∪ 报道 ∪ 实测，反馈不入选
        csv_path = make_csv(
            tmp_path,
            "03_User_Feedback_and_Complaints",
            "User_Feedback_Excerpts.csv",
            "测试产品ID,型号,渠道,发布时间,反馈内容\nP009,M9,平台商品页公开评价,2026/1/1,好评\n",
        )
        reg = build_registry(csv_row_records(
            csv_path, SourceType.USER_FEEDBACK, [["P009", "M9", "平台商品页公开评价", "2026/1/1", "好评"]]
        ))
        assert reg.ids() == []

    def test_feedback_enriches_existing_product(self, tmp_path: Path):
        listing = make_csv(
            tmp_path,
            "01_Ecommerce_Listings",
            "Ecommerce_Listing_Snapshot.csv",
            "测试产品ID,品牌,型号\nP001,Zurmek,SR-210 / Ralun Pro\n",
        )
        feedback = make_csv(
            tmp_path,
            "03_User_Feedback_and_Complaints",
            "User_Feedback_Excerpts.csv",
            "测试产品ID,型号,渠道,发布时间,反馈内容\nP001,SR-210 / Ralun Pro,平台商品页公开评价,2025/5/20,好\n",
        )
        records = csv_row_records(
            listing, SourceType.ECOMMERCE_LISTING, [["P001", "Zurmek", "SR-210 / Ralun Pro"]]
        ) + csv_row_records(
            feedback,
            SourceType.USER_FEEDBACK,
            [["P001", "SR-210 / Ralun Pro", "平台商品页公开评价", "2025/5/20", "好"]],
        )
        reg = build_registry(records)
        assert reg.ids() == ["P001"]
        assert reg.get("P001").model_raw == "SR-210 / Ralun Pro"  # 无官网时回退货架
        assert reg.identity_conflicts == []

    def test_malformed_id_cells_ignored(self, tmp_path: Path):
        csv_path = make_csv(
            tmp_path,
            "01_Ecommerce_Listings",
            "Ecommerce_Listing_Snapshot.csv",
            "测试产品ID,品牌,型号\nP0012,Zurmek,M\nXX,Yovrix,M2\n",
        )
        reg = build_registry(csv_row_records(
            csv_path,
            SourceType.ECOMMERCE_LISTING,
            [["P0012", "Zurmek", "M"], ["XX", "Yovrix", "M2"]],
        ))
        assert reg.ids() == []

    def test_duplicate_canonical_id_rejected(self):
        with pytest.raises(ValueError):
            ProductRegistry(
                [ProductRecord(canonical_id="P001"), ProductRecord(canonical_id="P001")],
                [],
            )


# ---------------------------------------------------------------------------
# record_product_ids（SourceIndex 检索依据）
# ---------------------------------------------------------------------------

class TestRecordProductIds:
    def test_official_and_media_filenames(self):
        assert record_product_ids(official_record("P001_RalunPro_Official_Site.txt")) == ["P001"]
        assert record_product_ids(coverage_record("P01_Product_Coverage.txt")) == ["P001"]

    def test_unexpected_filename_returns_empty(self):
        weird = SourceRecord(
            source_id="04_Brand_Official_Sites/其他.txt",
            source_type=SourceType.OFFICIAL_SITE,
            original_text="",
            spans=[],
            path="04_Brand_Official_Sites" + os.sep + "其他.txt",
        )
        assert record_product_ids(weird) == []

    def test_user_description_not_associated(self):
        record = SourceRecord(
            source_id="00_User_Descriptions/User_Description_1.txt",
            source_type=SourceType.USER_DESCRIPTION,
            original_text="小王",
            spans=[],
            path=None,
        )
        assert record_product_ids(record) == []
