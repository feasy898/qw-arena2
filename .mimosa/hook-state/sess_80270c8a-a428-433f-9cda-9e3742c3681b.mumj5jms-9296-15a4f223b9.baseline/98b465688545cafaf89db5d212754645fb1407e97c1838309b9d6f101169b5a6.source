# -*- coding: utf-8 -*-
"""tests/unit/test_selector.py — 排序与组合选择单测。

覆盖 SPEC §9 四组用例：3/2/1/0 款有效产品；断言推荐卡固定字段、
未选原因表覆盖全集、异常路径不放宽不虚构、排序可还原为属性×需求。
数据口径同 test_constraint_engine（黄金夹具 → 渲染 → parse 回读）。
"""
from __future__ import annotations

import pytest

from src import constraint_engine as ce
from src.renderer import CITATION_RE, LEVELS, parse_recommendation, render_recommendation
from src.selector import EXCLUSION_CATEGORIES, NO_VALID_BACKUP, rank_valid, select_recommendation
from src.schemas import DecisionPlan
from src.validators import validate_plan

from tests.unit.test_constraint_engine import (
    dorm_profile,
    load_products,
    sparse_product,
    swim_profile,
    user1_profile,
)


@pytest.fixture(scope="module")
def products() -> dict[str, dict]:
    return load_products()


def _plan_for(profile, products) -> DecisionPlan:
    matrix = ce.constraint_matrix(profile, products)
    return select_recommendation(profile, products, matrix)


def _citations(text: str) -> list[str]:
    return [match.group(0) for match in CITATION_RE.finditer(text)]


# ---------------------------------------------------------------------------
# 用例一：3+ 款有效（游泳用户，预算 1500-2000 → E={P001,P003,P004,P005}）
# ---------------------------------------------------------------------------

class TestThreeValidCase:
    def test_selects_three_from_valid_set(self, products):
        plan = _plan_for(swim_profile(), products)
        assert plan.selected_products == ["P001", "P005", "P003"]
        assert len(plan.recommendation_cards) == 3
        assert [card.level for card in plan.recommendation_cards] == list(LEVELS)
        assert set(plan.selected_products) <= {"P001", "P003", "P004", "P005"}

    def test_ranking_follows_expressed_preferences(self, products):
        """排序可还原为属性×需求：表达存储偏好 → 存储大的在前；价格并列打破。"""
        ranked = rank_valid({"P001", "P003", "P004", "P005"}, swim_profile(), products)
        assert ranked[0] == "P001"  # 32GB 且展示价 1098 < P005 的 1138

    def test_ranking_changes_when_preference_removed(self, products):
        """用户未表达存储偏好时，比较器口径切换（价格口径）→ 次序可解释地变化。"""
        plain = swim_profile()
        plain["functional_preferences"] = None  # parse 后列表字段可为 None
        ranked = rank_valid({"P001", "P003", "P004", "P005"}, plain, products)
        assert ranked[0] == "P004"  # 无存储比较口径时按展示价升序：456 元最低

    def test_cards_carry_all_fixed_fields_with_citations(self, products):
        plan = _plan_for(swim_profile(), products)
        for card in plan.recommendation_cards:
            assert card.product_id and card.product_name and card.brand
            assert card.profile_match and card.fact_references
            assert card.costs_risks.strip()
            assert card.pre_purchase_checks and card.switch_conditions
            reason = "；".join(card.profile_match) + "；".join(card.fact_references)
            assert "画像｜" in reason, "适配结论须引用画像字段"
            assert "产品属性｜" in reason, "事实依据须引用产品属性字段"
            assert card.tradeoffs.strip()

    def test_cards_cite_real_fields_of_both_documents(self, products):
        """引用（画像｜…｜… / 产品属性｜P00X｜…）必须指向前两份文档内真实字段。

        判定口径与 validators E8 一致：引用目标尾部带「）」注记时剥离后比对。
        """
        from src.renderer import catalog_fields
        from src.validators import _clean_citation_part, _strip_trailing_note

        profile = swim_profile()
        plan = _plan_for(profile, products)
        product_field_names = {f["name"] for _g, f in catalog_fields("product")}
        scene_item_names = {item["name"].format(n=n) for _g, f in
                            catalog_fields("profile") if f.get("item_fields")
                            for item in f["item_fields"] for n in (1, 2)}
        profile_field_names = {f["name"] for _g, f in catalog_fields("profile")} | scene_item_names
        for card in plan.recommendation_cards:
            text = "；".join(card.profile_match) + "；".join(card.fact_references)
            for match in CITATION_RE.finditer(text):
                kind, left, right = match.group(1), match.group(2), match.group(3)
                # 与 validators E8 相同的清洗次序：先剥收尾引号，再剥配对（…）注记
                right = _clean_citation_part(right)
                if kind == "画像":
                    assert right in profile_field_names or \
                        _strip_trailing_note(right) in profile_field_names, match.group(0)
                else:
                    assert left in products and (
                        right in product_field_names
                        or _strip_trailing_note(right) in product_field_names), match.group(0)

    def test_price_fact_appears_in_reason(self, products):
        plan = _plan_for(swim_profile(), products)
        first = plan.recommendation_cards[0]
        assert any("1098" in f for f in first.fact_references)

    def test_no_uncalibrated_score_in_outputs(self, products):
        """禁止无口径效用分（如「综合匹配度93.7」）写入报告。"""
        plan = _plan_for(swim_profile(), products)
        text = plan.overall_conclusion + "；".join(
            "；".join(c.profile_match) for c in plan.recommendation_cards)
        assert "匹配度" not in text
        for number in _citations.__defaults__ or []:
            pass

    def test_exclusion_table_covers_all_others(self, products):
        plan = _plan_for(swim_profile(), products)
        covered = [pid for record in plan.excluded_products for pid in record.product_ids]
        assert set(covered) == set(products) - set(plan.selected_products)
        assert len(covered) == len(set(covered)), "同一产品不得出现在多行"
        for record in plan.excluded_products:
            assert record.reason_category in EXCLUSION_CATEGORIES
            assert record.reason_detail.strip()
            assert record.change_conditions
            assert all("产品属性｜" in r or "画像｜" in r or "「" in r
                       for r in [record.reason_detail])

    def test_over_budget_exclusion_condition_matches_cause(self, products):
        plan = _plan_for(swim_profile(), products)
        record = next(r for r in plan.excluded_products if r.product_ids == ["P002"])
        assert record.reason_category == "硬约束不符"
        assert "2948" in record.reason_detail
        assert any("预算" in c and "2948" in c for c in record.change_conditions)

    def test_validate_plan_passes(self, products):
        plan = _plan_for(swim_profile(), products)
        assert validate_plan(plan, products) == []

    def test_render_parse_roundtrip(self, products):
        plan = _plan_for(swim_profile(), products)
        parsed = parse_recommendation(render_recommendation(plan))
        assert [row["product_id"] for row in parsed["recommendations"]] == plan.selected_products
        # 渲染器自动加「综合结论：」前缀，计划内结论不自带前缀
        assert not plan.overall_conclusion.startswith("综合结论")
        assert parsed["overall_conclusion"].strip()
        assert parsed["exclusions"]


# ---------------------------------------------------------------------------
# 用例二：2 款有效（预算 1000-1100 → E={P001,P004}）
# ---------------------------------------------------------------------------

class TestTwoValidCase:
    def test_two_cards_plus_empty_slot(self, products):
        plan = _plan_for(swim_profile(budget_min=1000, budget_max=1100), products)
        assert plan.selected_products == ["P001", "P004"]
        assert len(plan.recommendation_cards) == 3
        assert [c.level for c in plan.recommendation_cards] == list(LEVELS)
        empty = plan.recommendation_cards[2]
        assert empty.product_id == ""
        assert any(NO_VALID_BACKUP in m for m in empty.profile_match)
        assert "1" == "" or empty.tradeoffs  # 空位说明同时落在 tradeoffs

    def test_empty_slot_explains_shortfall_without_fabrication(self, products):
        plan = _plan_for(swim_profile(budget_min=1000, budget_max=1100), products)
        empty = plan.recommendation_cards[2]
        text = "；".join(empty.profile_match) + empty.tradeoffs
        assert "不足 3 款" in text
        assert "不放宽" in text and "不虚构" in text

    def test_validate_plan_and_render(self, products):
        plan = _plan_for(swim_profile(budget_min=1000, budget_max=1100), products)
        assert validate_plan(plan, products) == []
        parsed = parse_recommendation(render_recommendation(plan))
        assert [row["product_id"] for row in parsed["recommendations"]] == ["P001", "P004", None]
        assert "当前尚不构成有效备选" in parsed["recommendations"][2]["reason"]


# ---------------------------------------------------------------------------
# 用例三：1 款有效（预算 400-500 → E={P004}）
# ---------------------------------------------------------------------------

class TestOneValidCase:
    def test_single_card_with_two_empty_slots(self, products):
        plan = _plan_for(swim_profile(budget_min=400, budget_max=500), products)
        assert plan.selected_products == ["P004"]
        assert [c.product_id for c in plan.recommendation_cards] == ["P004", "", ""]
        assert all(NO_VALID_BACKUP in "；".join(c.profile_match)
                   for c in plan.recommendation_cards[1:])

    def test_first_choice_fields_complete(self, products):
        plan = _plan_for(swim_profile(budget_min=400, budget_max=500), products)
        card = plan.recommendation_cards[0]
        assert card.profile_match and card.fact_references and card.costs_risks
        assert card.pre_purchase_checks and card.switch_conditions
        assert validate_plan(plan, products) == []

    def test_others_reported_as_invalid_not_backup(self, products):
        plan = _plan_for(swim_profile(budget_min=400, budget_max=500), products)
        assert {pid for r in plan.excluded_products for pid in r.product_ids} == \
            set(products) - {"P004"}
        assert "仅P004" in plan.overall_conclusion


# ---------------------------------------------------------------------------
# 用例四：0 款有效（预算 200-300 全部超限 → 异常路径：无首选）
# ---------------------------------------------------------------------------

class TestZeroValidCase:
    def test_no_first_choice_no_fabrication(self, products):
        plan = _plan_for(swim_profile(budget_min=200, budget_max=300), products)
        assert plan.selected_products == []
        assert len(plan.recommendation_cards) == 1
        card = plan.recommendation_cards[0]
        assert card.product_id == ""
        text = "；".join(card.profile_match)
        assert "资料无法支持直接购买建议" in text
        assert "不放宽" in text and "不虚构" in text

    def test_all_products_listed_as_excluded(self, products):
        plan = _plan_for(swim_profile(budget_min=200, budget_max=300), products)
        covered = {pid for r in plan.excluded_products for pid in r.product_ids}
        assert covered == set(products)
        assert validate_plan(plan, products) == []

    def test_conclusion_states_no_purchase_advice(self, products):
        plan = _plan_for(swim_profile(budget_min=200, budget_max=300), products)
        assert "无法支持直接购买建议" in plan.overall_conclusion


# ---------------------------------------------------------------------------
# 四态纪律与未选原因五类
# ---------------------------------------------------------------------------

class TestExclusionCategoriesAndUnknownDiscipline:
    def test_unknown_hard_constraint_yields_insufficient_evidence_category(self, products):
        """硬约束 UNKNOWN（非 FAIL）→ 「硬约束证据不足」，不与「硬约束不符」混同。"""
        extended = dict(products)
        extended["P006"] = sparse_product("P006")
        plan = _plan_for(swim_profile(), extended)
        record = next(r for r in plan.excluded_products if r.product_ids == ["P006"])
        assert record.reason_category == "硬约束证据不足"
        assert any("水下" in c or "确证" in c for c in record.change_conditions)
        assert "P006" not in plan.selected_products

    def test_same_cause_exclusions_merge(self, products):
        """五款同因（预算硬约束不符）时合并行覆盖全部产品。"""
        plan = _plan_for(swim_profile(budget_min=200, budget_max=300), products)
        assert len(plan.excluded_products) < 5
        covered = [pid for r in plan.excluded_products for pid in r.product_ids]
        assert sorted(covered) == sorted(products)

    def test_all_five_categories_in_vocabulary(self):
        assert EXCLUSION_CATEGORIES == (
            "硬约束不符", "硬约束证据不足", "软需求匹配弱", "同条件下被压过", "无足够新增价值")

    def test_ranked_out_product_explained_by_comparator(self, products):
        plan = _plan_for(swim_profile(), products)
        record = next(r for r in plan.excluded_products if r.product_ids == ["P004"])
        assert record.reason_category in ("无足够新增价值", "同条件下被压过")
        assert "4GB" in record.reason_detail

    def test_change_conditions_never_predict_price_drop(self, products):
        plan = _plan_for(swim_profile(), products)
        for record in plan.excluded_products:
            for condition in record.change_conditions:
                assert "降价" not in condition and "可能降价" not in condition

    def test_empty_products_yields_exception_path(self):
        plan = _plan_for(swim_profile(), {})
        assert plan.selected_products == []
        assert plan.excluded_products == []
        assert validate_plan(plan, {}) == []


# ---------------------------------------------------------------------------
# 隔离铁律：入参形态（parse 后对象）即可驱动，不需要任何原始资料结构
# ---------------------------------------------------------------------------

class TestIsolation:
    def test_plan_is_decisionplan(self, products):
        plan = _plan_for(user1_profile(), products)
        assert isinstance(plan, DecisionPlan)

    def test_deterministic_across_calls(self, products):
        profile = swim_profile()
        first = _plan_for(profile, products)
        second = _plan_for(profile, products)
        assert first.to_dict() == second.to_dict()
