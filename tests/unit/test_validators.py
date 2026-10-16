# -*- coding: utf-8 -*-
"""tests/unit/test_validators.py — 确定性校验单元测试。

- validate_profile_object / validate_product_object / validate_plan：对象级逐项报错；
- validate_outputs：以官方示例数据集 raw/dataset_sample/Data_for_Users 为输入全集，
  手造对象经 renderer 渲染出合法三份文档（0 high 基线），再逐项构造坏输出
  （缺文件、多余文件、漏产品、品牌型号改动、表结构不一致、推荐数错、推荐越集、
  未选漏覆盖、注意事项为空、引用越界/缺失、BOM/GBK 编码、坏表格行、运行超时），
  断言对应 issue code 以 high 级报出。
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src import renderer, validators
from src.schemas import DecisionPlan, ExclusionRecord, RecommendationCard
from src.validators import (
    ValidationIssue,
    validate_outputs,
    validate_plan,
    validate_product_object,
    validate_profile_object,
)

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"

# 示例数据集注册表口径（与 tests/unit/test_product_registry.py 一致）
SAMPLE_IDENTITY = {
    "P001": ("Zurmek", "RalunPro", "Ralun Pro"),
    "P002": ("Zurmek", "Ralun", "Ralun"),
    "P003": ("Yovrix", "Kandro2Pro", "Kandro 2 Pro"),
    "P004": ("Norvek", "VD-140", "VD-140"),
    "P005": ("珂沐", "SorbikPro5", "Sorbik Pro 5"),
}


# ---------------------------------------------------------------------------
# 手造对象夹具
# ---------------------------------------------------------------------------

def make_profile() -> dict:
    return {
        "profile_id": "User_Description_1",
        "name": "小王",
        "gender": "男",
        "age": 26,
        "occupation": "程序员",
        "city": None,
        "product_category": "耳机",
        "desired_product_type": None,
        "purchase_purposes": ["地铁通勤听歌", "常接电话", "不想频繁充电"],
        "budget_min": None,
        "budget_max": 800,
        "currency": "元",
        "budget_raw": "大概800左右",
        "budget_semantics": "约(单值)",
        "devices": ["禾岚 安卓手机"],
        "ecosystem": "安卓",
        "ecosystem_notes": None,
        "scenes": [
            {"scene_name": "地铁通勤", "usage_duration": "单程40分钟", "usage_frequency": "每天",
             "scene_priority": 1, "priority_basis": "原文明示排序",
             "scene_evidence": "每天地铁通勤单程40分钟常接电话"},
            {"scene_name": "游泳", "usage_duration": None, "usage_frequency": "每周1-2次",
             "scene_priority": 2, "priority_basis": "原文主次措辞",
             "scene_evidence": "每周游一两次泳"},
        ],
        "brand_preferences": [],
        "brand_avoidances": [],
        "appearance_preferences": [],
        "sound_preferences": ["对音质有一定要求"],
        "functional_preferences": ["需要续航长"],
        "service_preferences": [],
        "other_preferences": [],
    }


def make_product(cid: str) -> dict:
    brand, model, name = SAMPLE_IDENTITY[cid]
    return {
        "canonical_id": cid,
        "aliases": ["P01"] if cid == "P001" else [],
        "product_name": name,
        "brand": brand,
        "model": model,
        "category": "骨传导游泳耳机",
        "core_functions": ["骨传导", "IP68防水"],
        "generation": "2024款" if cid != "P002" else None,
        "variants": [],
        "channel_and_shop": "电商平台甲 平台自营旗舰店",
        "listing_title": f"{name} 商品页标题",
        "noise_cancellation": "无主动降噪",
        "call_capability": None,
        "acoustic_tech": "骨传导",
        "local_storage_gb": 32 if cid == "P001" else 16,
        "audio_formats": ["MP3", "WAV"],
        "bluetooth": "蓝牙5.3",
        "bluetooth_underwater": "水下不可用蓝牙，仅MP3模式",
        "battery_by_mode": (
            [
                {"mode": "蓝牙模式", "duration": "最长8小时", "conditions": "音量50%"},
                {"mode": "MP3模式", "duration": "最长6小时", "conditions": None},
            ] if cid == "P001" else
            [{"mode": "未区分模式", "duration": "约8小时", "conditions": None}]
        ),
        "charging": "10分钟快充约2小时",
        "wearing_design": "颈后式开放佩戴",
        "weight_g": "约30",
        "protection_rating": "IP68",
        "waterproof_conditions": "淡水2米内最长2小时（官方声明）",
        "accessories_fit": [],
        "special_features": [],
        "brand_origin_market": "海外音频品牌",
        "brand_category_focus": "专注骨传导",
        "brand_reputation": "待核验（销量第一为渠道宣称）",
        "sales_volume": "已售25",
        "user_rating": "待核验（资料未提供分值）",
        "common_praise": ["有一条反馈提及佩戴稳固"],
        "common_complaints": [],
        "feedback_sample_note": "摘录共3条；代表性未知",
        "marketing_claims": [],
        "current_price": "1098元（展示价，平台自营旗舰店快照）",
        "original_price": None,
        "price_trend": "待核验（展示价低于优惠前价，口径为快照）",
        "warranty": "24个月（以销售地区政策为准）",
        "after_sales": None,
        "ongoing_costs": None,
    }


def make_products() -> dict[str, dict]:
    return {cid: make_product(cid) for cid in SAMPLE_IDENTITY}


def make_plan() -> DecisionPlan:
    return DecisionPlan(
        selected_products=["P001", "P002", "P003"],
        recommendation_cards=[
            RecommendationCard(
                level="首选", product_id="P001", product_name="Ralun Pro", brand="Zurmek",
                profile_match=["命中「地铁通勤听歌/不想频繁充电」需求「画像｜场景｜场景」"],
                fact_references=["产品属性｜P001｜续航（分模式）", "产品属性｜P001｜当前售价"],
                tradeoffs="较备选存储更大、双模式续航更清晰",
                costs_risks="展示价1098元高于「约800」口径，须确认实际到手价",
                pre_purchase_checks=["确认到手价是否在预算内", "确认水下仅MP3模式可接受"],
                switch_conditions=["若预算下修到500元内应改选P002"],
            ),
            RecommendationCard(
                level="备选1", product_id="P002", product_name="Ralun", brand="Zurmek",
                profile_match=["价格显著更低「画像｜预算｜预算上限」"],
                fact_references=["产品属性｜P002｜当前售价"],
                tradeoffs="存储与续航证据弱于首选",
                costs_risks="本地存储较小",
                pre_purchase_checks=["确认防水适用条件"],
                switch_conditions=["若需要32GB本地存储应改选P001"],
            ),
            RecommendationCard(
                level="备选2", product_id="P003", product_name="Kandro 2 Pro", brand="Yovrix",
                profile_match=["多运动场景覆盖「画像｜购买目标｜购买目的」"],
                fact_references=["产品属性｜P003｜防护等级"],
                tradeoffs="价格介于首选与备选1之间",
                costs_risks="重量约30g以上",
                pre_purchase_checks=["确认佩戴舒适度"],
                switch_conditions=["若偏好轻量应改选备选1"],
            ),
        ],
        excluded_products=[
            ExclusionRecord(
                product_ids=["P004"],
                reason_category="硬约束不符",
                reason_detail="无蓝牙不能满足无线听歌诉求「产品属性｜P004｜蓝牙能力」",
                change_conditions=["若允许纯MP3本地播放方案可考虑P004"],
            ),
            ExclusionRecord(
                product_ids=["P005"],
                reason_category="软需求匹配弱",
                reason_detail="价格高于其余有效候选且无新增价值「产品属性｜P005｜当前售价」",
                change_conditions=["若预算放宽到1500元以上可考虑P005"],
            ),
        ],
        overall_conclusion="重续航与存储选P001，重价格选P002，多运动场景均衡选P003。",
    )


# ---------------------------------------------------------------------------
# 输出目录夹具：渲染并写盘 / 变异
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def valid_bundle() -> dict[str, str]:
    """合法三份文档全文（基线）。"""
    return {
        renderer.PROFILE_FILENAME: renderer.render_profile(make_profile()),
        renderer.PRODUCTS_FILENAME: renderer.render_products(make_products()),
        renderer.RECOMMENDATION_FILENAME: renderer.render_recommendation(make_plan()),
    }


def write_output(tmp_path: Path, bundle: dict[str, str]) -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    for name, md in bundle.items():
        (tmp_path / name).write_text(md, encoding="utf-8", newline="\n")
    return tmp_path


def drop_line(md: str, prefix: str, count: int = 1) -> str:
    """删除以 prefix 开头的行（前 count 行）。"""
    out_lines = []
    dropped = 0
    for line in md.splitlines(keepends=True):
        if dropped < count and line.startswith(prefix):
            dropped += 1
            continue
        out_lines.append(line)
    return "".join(out_lines)


def high_codes(issues: list[ValidationIssue]) -> set[str]:
    return {issue.code for issue in issues if issue.severity == "high"}


# ---------------------------------------------------------------------------
# validate_profile_object
# ---------------------------------------------------------------------------

class TestValidateProfileObject:
    def test_valid_object_no_issues(self):
        assert validate_profile_object(make_profile()) == []

    def test_unknown_key_medium(self):
        issues = validate_profile_object({**make_profile(), "hobby": "游泳"})
        assert [("OBJ_PROFILE_UNKNOWN_KEY", "medium")] == [(i.code, i.severity) for i in issues]

    def test_required_key_absent_high(self):
        profile = make_profile()
        del profile["budget_max"]
        issues = validate_profile_object(profile)
        assert ("OBJ_PROFILE_REQUIRED_ABSENT", "high") in {(i.code, i.severity) for i in issues}

    def test_enum_violation_high(self):
        issues = validate_profile_object({**make_profile(), "gender": "未知"})
        assert ("OBJ_PROFILE_ENUM", "high") in {(i.code, i.severity) for i in issues}

    def test_scene_item_missing_name_high(self):
        profile = make_profile()
        profile["scenes"][0]["scene_name"] = None
        issues = validate_profile_object(profile)
        assert any(i.code == "OBJ_PROFILE_ITEM_REQUIRED" and i.severity == "high" for i in issues)

    def test_scene_priority_type_high(self):
        profile = make_profile()
        profile["scenes"][0]["scene_priority"] = "最高"
        issues = validate_profile_object(profile)
        assert ("OBJ_PROFILE_TYPE", "high") in {(i.code, i.severity) for i in issues}

    def test_list_type_mismatch(self):
        issues = validate_profile_object({**make_profile(), "devices": "禾岚手机"})
        codes = {(i.code, i.severity) for i in issues}
        assert ("OBJ_PROFILE_TYPE", "medium") in codes  # 字符串 → medium（可转换）


# ---------------------------------------------------------------------------
# validate_product_object
# ---------------------------------------------------------------------------

class TestValidateProductObject:
    def test_valid_object_no_issues(self):
        assert validate_product_object(make_product("P001")) == []

    def test_non_canonical_id_high(self):
        product = make_product("P001")
        product["canonical_id"] = "P01"
        issues = validate_product_object(product)
        assert ("OBJ_PRODUCT_ID_FORMAT", "high") in {(i.code, i.severity) for i in issues}

    def test_battery_item_missing_mode_high(self):
        product = make_product("P001")
        product["battery_by_mode"] = [{"duration": "8小时"}]
        issues = validate_product_object(product)
        assert any(i.code == "OBJ_PRODUCT_ITEM_REQUIRED" and i.severity == "high" for i in issues)

    def test_battery_wrong_shape_high(self):
        issues = validate_product_object({**make_product("P001"), "battery_by_mode": "8小时"})
        assert ("OBJ_PRODUCT_TYPE", "high") in {(i.code, i.severity) for i in issues}

    def test_verify_without_note_medium(self):
        product = make_product("P001")
        product["user_rating"] = "待核验"
        issues = validate_product_object(product)
        assert ("OBJ_PRODUCT_VERIFY_NOTE", "medium") in {(i.code, i.severity) for i in issues}

    def test_factcell_valued_object_accepted(self):
        from src.schemas import FactCell, FactKind, FactStatus

        product = make_product("P001")
        product["local_storage_gb"] = FactCell(
            field_key="local_storage_gb", raw_value="32", normalized_value=32, unit="GB",
            fact_kind=FactKind.OFFICIAL_CLAIM, status=FactStatus.SUPPORTED,
        )
        product["battery_by_mode"] = [
            {"mode": "蓝牙模式",
             "duration": FactCell(field_key="duration", raw_value="最长8小时",
                                  fact_kind=FactKind.OFFICIAL_CLAIM, status=FactStatus.SUPPORTED),
             "conditions": None},
        ]
        assert validate_product_object(product) == []


# ---------------------------------------------------------------------------
# validate_plan
# ---------------------------------------------------------------------------

class TestValidatePlan:
    def product_ids(self) -> dict[str, dict]:
        return {cid: {} for cid in SAMPLE_IDENTITY}

    def test_valid_plan_no_issues(self):
        assert validate_plan(make_plan(), self.product_ids()) == []

    def test_exclusion_coverage_missing_high(self):
        plan = make_plan()
        plan.excluded_products = plan.excluded_products[:1]  # 漏 P005
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_EXCLUSION_COVERAGE", "high") in {(i.code, i.severity) for i in issues}

    def test_exclusion_category_invalid_high(self):
        plan = make_plan()
        plan.excluded_products[0].reason_category = "价格贵"
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_EXCLUSION_CATEGORY", "high") in {(i.code, i.severity) for i in issues}

    def test_card_risks_empty_high(self):
        plan = make_plan()
        plan.recommendation_cards[0].costs_risks = ""
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_CARD_RISKS", "high") in {(i.code, i.severity) for i in issues}

    def test_card_checks_empty_high(self):
        plan = make_plan()
        plan.recommendation_cards[0].pre_purchase_checks = []
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_CARD_CHECKS", "high") in {(i.code, i.severity) for i in issues}

    def test_card_facts_empty_high(self):
        plan = make_plan()
        plan.recommendation_cards[0].fact_references = []
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_CARD_FACTS", "high") in {(i.code, i.severity) for i in issues}

    def test_unknown_product_high(self):
        plan = make_plan()
        plan.recommendation_cards[0].product_id = "P099"
        plan.selected_products[0] = "P099"
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_PRODUCT_UNKNOWN", "high") in {(i.code, i.severity) for i in issues}

    def test_duplicate_product_high(self):
        plan = make_plan()
        plan.recommendation_cards[1].product_id = "P001"
        plan.selected_products = ["P001", "P001", "P003"]
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_PRODUCT_DUP", "high") in {(i.code, i.severity) for i in issues}

    def test_conclusion_empty_high(self):
        plan = make_plan()
        plan.overall_conclusion = ""
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_CONCLUSION", "high") in {(i.code, i.severity) for i in issues}

    def test_levels_wrong_order_high(self):
        plan = make_plan()
        plan.recommendation_cards[0].level = "备选1"
        issues = validate_plan(plan, self.product_ids())
        assert ("PLAN_LEVELS", "high") in {(i.code, i.severity) for i in issues}


# ---------------------------------------------------------------------------
# validate_outputs：合法基线 + 逐项坏输出
# ---------------------------------------------------------------------------

class TestValidateOutputsValid:
    def test_valid_bundle_zero_issues(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        assert validate_outputs(out_dir, SAMPLE_DIR) == []

    def test_valid_ids_respected_when_subset(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        # E = 全集 → 通过
        assert validate_outputs(out_dir, SAMPLE_DIR, valid_ids=set(SAMPLE_IDENTITY)) == []

    def test_run_time_within_limit_no_issue(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        assert validate_outputs(out_dir, SAMPLE_DIR, run_seconds=100.0) == []


class TestValidateOutputsBad:
    def test_missing_file_e1(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = {k: v for k, v in valid_bundle.items() if k != renderer.RECOMMENDATION_FILENAME}
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E1_OUTPUT_FILES" in high_codes(issues)

    def test_extra_file_e1(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        (out_dir / "notes.txt").write_text("多余文件", encoding="utf-8")
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E1_OUTPUT_FILES" in high_codes(issues)

    def test_missing_product_section_e2(self, valid_bundle: dict[str, str], tmp_path: Path):
        # 漏产品：删除 P005 整节
        products_md = valid_bundle[renderer.PRODUCTS_FILENAME]
        head, _, tail = products_md.partition("## P005 ")
        assert tail, "P005 节应存在"
        bundle = dict(valid_bundle)
        bundle[renderer.PRODUCTS_FILENAME] = head
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E2_PRODUCT_COVERAGE" in high_codes(issues)

    def test_extra_product_section_e2(self, valid_bundle: dict[str, str], tmp_path: Path):
        # 拆并错误：凭空多出 P006 节
        products_md = valid_bundle[renderer.PRODUCTS_FILENAME]
        section_p005 = "## P005 " + products_md.partition("## P005 ")[2]
        fake = section_p005.replace("P005", "P006")
        bundle = dict(valid_bundle)
        bundle[renderer.PRODUCTS_FILENAME] = products_md + fake
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E2_PRODUCT_COVERAGE" in high_codes(issues)

    def test_brand_altered_e3(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.PRODUCTS_FILENAME] = valid_bundle[renderer.PRODUCTS_FILENAME].replace(
            "| 基础标识 | 品牌 | Yovrix |", "| 基础标识 | 品牌 | Yovrik |", 1)
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E3_VERBATIM_IDENTITY" in high_codes(issues)

    def test_model_altered_e3(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.PRODUCTS_FILENAME] = valid_bundle[renderer.PRODUCTS_FILENAME].replace(
            "| 基础标识 | 型号 | RalunPro |", "| 基础标识 | 型号 | RalunX |", 1)
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E3_VERBATIM_IDENTITY" in high_codes(issues)

    def test_table_structure_broken_e4(self, valid_bundle: dict[str, str], tmp_path: Path):
        # 表结构不一致：P002 节少一行「编号别名」
        bundle = dict(valid_bundle)
        bundle[renderer.PRODUCTS_FILENAME] = drop_line(
            valid_bundle[renderer.PRODUCTS_FILENAME], "| 基础标识 | 编号别名 | 未提供 |")
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E4_TABLE_STRUCTURE" in high_codes(issues)

    def test_recommendation_row_count_wrong_e5(self, valid_bundle: dict[str, str], tmp_path: Path):
        # 推荐数错：推荐结果表只剩 2 行
        bundle = dict(valid_bundle)
        bundle[renderer.RECOMMENDATION_FILENAME] = drop_line(
            valid_bundle[renderer.RECOMMENDATION_FILENAME], "| 备选2 |")
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E5_RECOMMENDATION" in high_codes(issues)

    def test_recommendation_unknown_product_e5(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.RECOMMENDATION_FILENAME] = valid_bundle[renderer.RECOMMENDATION_FILENAME].replace(
            "| 首选 | P001 |", "| 首选 | P099 |", 1)
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E5_RECOMMENDATION" in high_codes(issues)

    def test_recommendation_not_in_valid_set_e5(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR, valid_ids={"P001", "P004", "P005"})
        assert "E5_RECOMMENDATION" in high_codes(issues)
        messages = "；".join(i.message for i in issues if i.code == "E5_RECOMMENDATION")
        assert "P002" in messages and "P003" in messages

    def test_exclusion_coverage_missing_e6(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.RECOMMENDATION_FILENAME] = drop_line(
            valid_bundle[renderer.RECOMMENDATION_FILENAME], "| P005 |")
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E6_EXCLUSION_COVERAGE" in high_codes(issues)

    def test_notes_empty_e7(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.RECOMMENDATION_FILENAME] = valid_bundle[renderer.RECOMMENDATION_FILENAME].replace(
            "本地存储较小；确认防水适用条件", "未提供", 1)
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E7_CARD_FIELDS" in high_codes(issues)

    def test_citation_out_of_scope_e8(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.RECOMMENDATION_FILENAME] = valid_bundle[renderer.RECOMMENDATION_FILENAME].replace(
            "产品属性｜P001｜续航（分模式）", "产品属性｜P001｜不存在的字段", 1)
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E8_CITATIONS" in high_codes(issues)

    def test_reason_without_citation_e8(self, tmp_path: Path):
        plan = make_plan()
        plan.recommendation_cards[0].profile_match = ["音质好、续航长"]
        plan.recommendation_cards[0].fact_references = []
        bundle = {
            renderer.PROFILE_FILENAME: renderer.render_profile(make_profile()),
            renderer.PRODUCTS_FILENAME: renderer.render_products(make_products()),
            renderer.RECOMMENDATION_FILENAME: renderer.render_recommendation(plan),
        }
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E8_CITATIONS" in high_codes(issues)

    def test_bom_encoding_e9(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        path = out_dir / renderer.PROFILE_FILENAME
        path.write_bytes(b"\xef\xbb\xbf" + path.read_bytes())
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E9_RUNTIME_ENCODING" in high_codes(issues)

    def test_non_utf8_e9(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        path = out_dir / renderer.PRODUCTS_FILENAME
        path.write_bytes(path.read_text(encoding="utf-8").encode("gbk"))
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E9_RUNTIME_ENCODING" in high_codes(issues)

    def test_run_time_over_hard_limit_e9(self, valid_bundle: dict[str, str], tmp_path: Path):
        out_dir = write_output(tmp_path / "out", valid_bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR, run_seconds=1801.0)
        assert "E9_RUNTIME_ENCODING" in high_codes(issues)

    def test_ragged_table_row_e10(self, valid_bundle: dict[str, str], tmp_path: Path):
        bundle = dict(valid_bundle)
        bundle[renderer.PRODUCTS_FILENAME] = valid_bundle[renderer.PRODUCTS_FILENAME].replace(
            "| 基础标识 | 产品编号 | P003 |", "| 基础标识 | 产品编号 P003 |", 1)
        out_dir = write_output(tmp_path / "out", bundle)
        issues = validate_outputs(out_dir, SAMPLE_DIR)
        assert "E10_MARKDOWN_TABLE" in high_codes(issues)

    def test_output_dir_missing_e1(self, tmp_path: Path):
        issues = validate_outputs(tmp_path / "not_exist", SAMPLE_DIR)
        assert "E1_OUTPUT_FILES" in high_codes(issues)

def make_exception_plan() -> DecisionPlan:
    """SPEC §9 异常路径：仅 1 款有效产品；空位说明 + 其余全部产品入未选表。"""
    plan = DecisionPlan(
        selected_products=["P001"],
        recommendation_cards=[make_plan().recommendation_cards[0]],
        excluded_products=[
            ExclusionRecord(
                product_ids=["P002", "P003"],
                reason_category="硬约束证据不足",
                reason_detail="防水与水下使用条件缺失「产品属性｜P002｜防水适用条件」",
                change_conditions=["若资料补充防水等级与水下条件可再评估"],
            ),
            ExclusionRecord(
                product_ids=["P004"],
                reason_category="硬约束不符",
                reason_detail="无蓝牙不能满足无线听歌诉求「产品属性｜P004｜蓝牙能力」",
                change_conditions=["若允许纯MP3本地播放方案可考虑P004"],
            ),
            ExclusionRecord(
                product_ids=["P005"],
                reason_category="软需求匹配弱",
                reason_detail="价格高于其余候选且无新增价值「产品属性｜P005｜当前售价」",
                change_conditions=["若预算放宽到1500元以上可考虑P005"],
            ),
        ],
        overall_conclusion="当前资料仅支持P001为首选，其余产品在证据补齐前不构成有效备选。",
    )
    return plan


class TestValidateOutputsExceptionPath:
    def make_bundle(self) -> dict[str, str]:
        return {
            renderer.PROFILE_FILENAME: renderer.render_profile(make_profile()),
            renderer.PRODUCTS_FILENAME: renderer.render_products(make_products()),
            renderer.RECOMMENDATION_FILENAME: renderer.render_recommendation(make_exception_plan()),
        }

    def test_exception_path_empty_slots_pass(self, tmp_path: Path):
        # SPEC §9 异常路径：仅 1 个有效产品，空位行说明后应通过（E 集只含 P001）
        out_dir = write_output(tmp_path / "out", self.make_bundle())
        issues = validate_outputs(out_dir, SAMPLE_DIR, valid_ids={"P001"})
        assert issues == []

    def test_exception_path_without_marker_fails_e5(self, tmp_path: Path):
        out_dir = write_output(tmp_path / "out", self.make_bundle())
        # 破坏空位行的空位说明（首个出现在备选1行的理由单元格）→ E5
        md = self.make_bundle()[renderer.RECOMMENDATION_FILENAME].replace(
            "| 当前尚不构成有效备选 |", "| 未提供 |", 1)
        (out_dir / renderer.RECOMMENDATION_FILENAME).write_text(md, encoding="utf-8", newline="\n")
        issues = validate_outputs(out_dir, SAMPLE_DIR, valid_ids={"P001"})
        assert "E5_RECOMMENDATION" in high_codes(issues)
