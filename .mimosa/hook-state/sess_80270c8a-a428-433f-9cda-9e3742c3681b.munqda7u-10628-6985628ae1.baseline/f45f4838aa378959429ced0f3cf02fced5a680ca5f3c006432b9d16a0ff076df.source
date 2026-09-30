# -*- coding: utf-8 -*-
"""tests/unit/test_constraint_engine.py — 硬约束判定四态单测。

数据口径：产品对象一律取自 tests/fixtures/gateway/responses.json 黄金夹具
（官方示例数据逐字取材）经 reconciler → renderer.render_products →
parse_products 的真实回读链，保证与管线决策阶段输入同形；
画像对象经 renderer.render_profile → parse_profile 回读（隔离铁律）。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import constraint_engine as ce
from src.reconciler import reconcile_product
from src.renderer import (
    CITATION_RE,
    parse_products,
    parse_profile,
    render_products,
    render_profile,
)
from src.schemas import ConstraintOutcome, NeedType

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "fixtures" / "gateway" / "responses.json"
PRODUCT_IDS = ("P001", "P002", "P003", "P004", "P005")


# ---------------------------------------------------------------------------
# 夹具构造（供本文件与 test_selector / test_report_writer 复用）
# ---------------------------------------------------------------------------

def load_products() -> dict[str, dict]:
    """黄金夹具 → reconciler 终稿 → 渲染 → parse 回读（决策阶段同形输入）。"""
    data = json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))
    finals = {}
    for pid in PRODUCT_IDS:
        content = json.loads(data[f"product_extraction_{pid}"]["content"])
        draft = dict(content)
        draft.update(content.pop("fields"))
        finals[pid] = reconcile_product(draft, [], {})
    return parse_products(render_products(finals))


def swim_profile(*, budget_min=1500, budget_max=2000) -> dict:
    """User_Description_5 画像（游泳/水下需求，区间预算）→ parse 回读。"""
    raw = {
        "profile_id": "User_Description_5", "gender": "男", "age": 30,
        "occupation": "游泳爱好者", "product_category": "耳机",
        "desired_product_type": "能在水下听歌的耳机",
        "purchase_purposes": ["在游泳时听音乐"],
        "budget_min": budget_min, "budget_max": budget_max, "currency": "元",
        "budget_raw": f"预算{budget_min}-{budget_max}元", "budget_semantics": "区间",
        "ecosystem": "未提供",
        "scenes": [{"scene_name": "游泳", "usage_duration": "每次1小时左右",
                    "usage_frequency": "每周3-4次", "scene_priority": 1,
                    "priority_basis": "原文明示排序",
                    "scene_evidence": "每周去游泳馆3-4次，每次游1小时左右"}],
        "functional_preferences": ["防水要好", "最好自带存储不用带手机"],
    }
    return parse_profile(render_profile(raw))


def user1_profile() -> dict:
    """User_Description_1 画像（约(单值)预算、安卓生态软偏好）→ parse 回读。"""
    raw = {
        "profile_id": "User_Description_1", "gender": "男", "age": 28,
        "occupation": "互联网公司产品经理", "product_category": "耳机",
        "purchase_purposes": ["通勤路上听听播客", "听音乐放松"],
        "budget_max": 2000, "currency": "元", "budget_raw": "预算2000元左右",
        "budget_semantics": "约(单值)",
        "devices": ["Velmora 手机", "禾岚 安卓手机"],
        "ecosystem": "之前一直用 Velmora 手机；最近换成了安卓阵营的禾岚",
        "scenes": [{"scene_name": "地铁通勤", "usage_duration": "单程40分钟",
                    "usage_frequency": "每天", "scene_priority": 1,
                    "priority_basis": "原文主次措辞",
                    "scene_evidence": "每天地铁通勤单程40分钟"}],
        "sound_preferences": ["对音质有一定要求"],
        "functional_preferences": ["希望耳机能和安卓手机配合得好一些"],
    }
    return parse_profile(render_profile(raw))


def gamer_profile() -> dict:
    """User_Description_3 画像（头戴式游戏耳机，全产品形态不符）。"""
    raw = {
        "profile_id": "User_Description_3", "gender": "男", "age": 22,
        "occupation": "大学生", "product_category": "耳机",
        "desired_product_type": "头戴式游戏耳机",
        "budget_max": 300, "currency": "元", "budget_raw": "预算300元左右",
        "budget_semantics": "约(单值)",
        "scenes": [{"scene_name": "打游戏", "usage_frequency": "每天",
                    "scene_priority": 1, "priority_basis": "原文主次措辞",
                    "scene_evidence": "重度FPS游戏玩家"}],
        "functional_preferences": ["要求听声辨位精准", "要求麦克风清晰"],
    }
    return parse_profile(render_profile(raw))


def dorm_profile() -> dict:
    """User_Description_2 画像（需要降噪功能 → 全产品硬约束不符）。"""
    raw = {
        "profile_id": "User_Description_2", "gender": "女", "age": 20,
        "occupation": "大二学生", "product_category": "耳机",
        "budget_max": 500, "currency": "元", "budget_raw": "预算500元左右",
        "budget_semantics": "约(单值)",
        "scenes": [{"scene_name": "宿舍学习", "usage_frequency": "每天",
                    "scene_priority": 1, "priority_basis": "原文主次措辞",
                    "scene_evidence": "宿舍室友有时候比较吵"}],
        "functional_preferences": ["需要降噪功能", "需要稍微防点汗"],
    }
    return parse_profile(render_profile(raw))


def sparse_product(pid: str = "P006", *, price: str | None = "399元（展示价，快照）") -> dict:
    """字段大量缺失的产品（parse 后形态）：供 UNKNOWN 判定与异常路径用。"""
    from src.renderer import load_field_catalog

    obj = {f["key"]: None for _g, f in
           ((g, f) for g in load_field_catalog()["product"]["groups"] for f in g["fields"])}
    obj.update({"canonical_id": pid, "product_name": "Sparse Pro", "brand": "Nobrand",
                "current_price": price})
    return obj


@pytest.fixture(scope="module")
def products() -> dict[str, dict]:
    return load_products()


# ---------------------------------------------------------------------------
# build_needs：硬约束准入
# ---------------------------------------------------------------------------

class TestBuildNeeds:
    def test_budget_band_becomes_hard_limit(self):
        needs = ce.build_needs(swim_profile())
        budget = [n for n in needs if n.need_id == ce.NEED_BUDGET_LIMIT]
        assert len(budget) == 1
        assert budget[0].need_type is NeedType.HARD
        assert budget[0].operator == ce.OP_BUDGET_LE
        assert budget[0].expected_value == 2000

    def test_approx_budget_not_hard_cap(self):
        """「约(单值)」不得直接当硬上限：立为歧义需求，不作 E 门槛。"""
        needs = ce.build_needs(user1_profile())
        assert [n for n in needs if n.need_id == ce.NEED_BUDGET_LIMIT] == []
        ref = [n for n in needs if n.need_id == ce.NEED_BUDGET_REFERENCE]
        assert len(ref) == 1 and ref[0].need_type is NeedType.AMBIGUOUS

    def test_no_budget_need_without_value(self):
        needs = ce.build_needs(parse_profile(render_profile({"product_category": "耳机"})))
        assert not [n for n in needs if "budget" in n.need_id]

    def test_explicit_need_marker_is_hard(self):
        needs = ce.build_needs(dorm_profile())
        noise = [n for n in needs if n.need_id == ce.NEED_NOISE]
        assert len(noise) == 1 and noise[0].need_type is NeedType.HARD

    def test_wish_marker_stays_soft(self):
        needs = ce.build_needs(user1_profile())
        storage = [n for n in needs if n.need_id == ce.NEED_ECOSYSTEM]
        assert storage and storage[0].need_type is NeedType.SOFT

    def test_swim_scene_creates_hard_underwater_need(self):
        needs = ce.build_needs(swim_profile())
        uw = [n for n in needs if n.need_id == ce.NEED_UNDERWATER]
        assert len(uw) == 1 and uw[0].need_type is NeedType.HARD
        assert uw[0].operator == ce.OP_UNDERWATER

    def test_brand_avoidance_is_hard(self):
        raw = {"product_category": "耳机", "brand_avoidances": ["珂沐"]}
        needs = ce.build_needs(parse_profile(render_profile(raw)))
        avoid = [n for n in needs if n.need_id == ce.NEED_BRAND_AVOID]
        assert len(avoid) == 1 and avoid[0].need_type is NeedType.HARD
        assert avoid[0].expected_value == ["珂沐"]

    def test_need_ids_stable_across_calls(self):
        first = [n.need_id for n in ce.build_needs(swim_profile())]
        second = [n.need_id for n in ce.build_needs(swim_profile())]
        assert first == second


# ---------------------------------------------------------------------------
# constraint_matrix：四态判定与引用
# ---------------------------------------------------------------------------

class TestConstraintMatrix:
    def test_full_coverage_and_shape(self, products):
        profile = swim_profile()
        needs = ce.build_needs(profile)
        matrix = ce.constraint_matrix(profile, products)
        assert len(matrix) == len(needs) * len(products)
        assert len({(r.product_id, r.need_id) for r in matrix}) == len(matrix)
        for row in matrix:
            assert row.result in ConstraintOutcome
            assert row.profile_reference and row.profile_reference.startswith("画像｜")
            assert row.explanation.strip()

    def test_references_follow_citation_format(self, products):
        profile = swim_profile()
        matrix = ce.constraint_matrix(profile, products)
        valid_profile_targets = {"画像｜预算｜预算上限", "画像｜场景｜场景1·名称",
                                 "画像｜偏好｜功能与性能诉求"}
        for row in matrix:
            assert CITATION_RE.search(row.profile_reference)
            if row.attribute_reference:
                assert CITATION_RE.search(row.attribute_reference)
                assert row.attribute_reference.startswith(f"产品属性｜{row.product_id}｜")
        budget_rows = [r for r in matrix if r.need_id == ce.NEED_BUDGET_LIMIT]
        assert {r.profile_reference for r in budget_rows} == {"画像｜预算｜预算上限"}

    def test_budget_over_limit_fails_with_price_citation(self, products):
        profile = swim_profile(budget_min=1500, budget_max=2000)
        matrix = ce.constraint_matrix(profile, products)
        row = next(r for r in matrix
                   if r.product_id == "P002" and r.need_id == ce.NEED_BUDGET_LIMIT)
        assert row.result is ConstraintOutcome.FAIL
        assert "产品属性｜P002｜当前售价" in row.explanation
        assert "2948" in row.explanation

    def test_budget_within_limit_passes(self, products):
        profile = swim_profile()
        matrix = ce.constraint_matrix(profile, products)
        row = next(r for r in matrix
                   if r.product_id == "P004" and r.need_id == ce.NEED_BUDGET_LIMIT)
        assert row.result is ConstraintOutcome.PASS
        assert "456" in row.explanation

    def test_price_range_straddling_budget_is_unknown(self):
        """区间价跨预算线 → UNKNOWN（具体成交 SKU 价待核验），不得放水为 PASS。"""
        products = {"P009": sparse_product("P009", price="渠道报价主要落在699-999元（测评快照）")}
        profile = swim_profile(budget_min=600, budget_max=800)
        matrix = ce.constraint_matrix(profile, products)
        row = next(r for r in matrix if r.need_id == ce.NEED_BUDGET_LIMIT)
        assert row.result is ConstraintOutcome.UNKNOWN

    def test_price_missing_is_unknown(self):
        products = {"P009": sparse_product("P009", price=None)}
        profile = swim_profile()
        matrix = ce.constraint_matrix(profile, products)
        row = next(r for r in matrix if r.need_id == ce.NEED_BUDGET_LIMIT)
        assert row.result is ConstraintOutcome.UNKNOWN

    def test_noise_cancellation_fails_for_all_sample_products(self, products):
        """五款样例资料均明确「无主动降噪」→ 降噪硬约束全 FAIL。"""
        matrix = ce.constraint_matrix(dorm_profile(), products)
        fails = [r for r in matrix
                 if r.need_id == ce.NEED_NOISE and r.result is ConstraintOutcome.FAIL]
        assert {r.product_id for r in fails} == set(PRODUCT_IDS)
        assert all("无主动降噪" in r.explanation for r in fails)

    def test_missing_capability_is_unknown_not_pass(self, products):
        """画像含「要求麦克风清晰」：通话能力未提供的产品 → UNKNOWN≠PASS。"""
        matrix = ce.constraint_matrix(gamer_profile(), products)
        row = next(r for r in matrix
                   if r.product_id == "P002" and r.need_id == ce.NEED_CALL)
        assert row.result is ConstraintOutcome.UNKNOWN
        assert "产品属性｜P002｜通话能力" in row.explanation

    def test_underwater_pass_with_condition_for_sample_products(self, products):
        profile = swim_profile()
        matrix = ce.constraint_matrix(profile, products)
        for pid in PRODUCT_IDS:
            row = next(r for r in matrix
                       if r.product_id == pid and r.need_id == ce.NEED_UNDERWATER)
            assert row.result is ConstraintOutcome.PASS, (pid, row.explanation)
        p4 = next(r for r in matrix
                  if r.product_id == "P004" and r.need_id == ce.NEED_UNDERWATER)
        assert "条件：" in p4.explanation and "游泳耳塞" in p4.explanation

    def test_underwater_no_evidence_is_unknown(self):
        products = {"P009": sparse_product("P009")}
        matrix = ce.constraint_matrix(swim_profile(), products)
        row = next(r for r in matrix
                   if r.product_id == "P009" and r.need_id == ce.NEED_UNDERWATER)
        assert row.result is ConstraintOutcome.UNKNOWN
        assert "UNKNOWN" in row.explanation

    def test_form_factor_mismatch_fails(self, products):
        matrix = ce.constraint_matrix(gamer_profile(), products)
        rows = [r for r in matrix
                if r.need_id == ce.NEED_FORM and r.result is ConstraintOutcome.FAIL]
        assert {r.product_id for r in rows} == set(PRODUCT_IDS)

    def test_ecosystem_no_evidence_is_unknown_but_missing_bluetooth_fails(self, products):
        """「无证据证明兼容」≠「兼容」：支持蓝牙仅 UNKNOWN；不支持蓝牙判 FAIL。"""
        matrix = ce.constraint_matrix(user1_profile(), products)
        for pid in ("P001", "P003", "P004", "P005"):
            row = next(r for r in matrix if r.product_id == pid
                       and r.need_id == ce.NEED_ECOSYSTEM)
            assert row.result is ConstraintOutcome.UNKNOWN, (pid, row.explanation)
        row = next(r for r in matrix if r.product_id == "P002"
                   and r.need_id == ce.NEED_ECOSYSTEM)
        assert row.result is ConstraintOutcome.FAIL
        assert "产品属性｜P002｜蓝牙能力" in row.explanation

    def test_storage_soft_need_reads_value(self, products):
        matrix = ce.constraint_matrix(swim_profile(), products)
        row = next(r for r in matrix
                   if r.product_id == "P001" and r.need_id == ce.NEED_STORAGE)
        assert row.result is ConstraintOutcome.PASS
        assert "32GB" in row.explanation

    def test_non_executable_preference_is_not_applicable(self, products):
        """听感/外观等综合诉求不立硬约束：矩阵中 NOT_APPLICABLE，不产生无据判定。"""
        matrix = ce.constraint_matrix(user1_profile(), products)
        rows = [r for r in matrix
                if r.need_id.startswith(ce.NEED_PREF_PREFIX)
                or r.need_id in (ce.NEED_ECOSYSTEM,)]
        sound_rows = [r for r in matrix
                      if r.need_id.startswith(ce.NEED_PREF_PREFIX)]
        assert sound_rows, "音质等偏好应进入矩阵"
        assert all(r.result is ConstraintOutcome.NOT_APPLICABLE for r in sound_rows)
        hard_ids = {n.need_id for n in ce.build_needs(user1_profile())
                    if n.need_type is NeedType.HARD}
        assert not any(rid.startswith(ce.NEED_PREF_PREFIX) for rid in hard_ids)


# ---------------------------------------------------------------------------
# valid_product_ids：有效集 E
# ---------------------------------------------------------------------------

class TestValidProductIds:
    def test_swim_user_valid_set_excludes_over_budget(self, products):
        matrix = ce.constraint_matrix(swim_profile(), products)
        assert ce.valid_product_ids(matrix) == {"P001", "P003", "P004", "P005"}

    def test_no_hard_constraints_means_all_valid(self, products):
        """无硬约束（仅歧义预算+软偏好+目标）→ 全集有效（不以 UNKNOWN 放大门槛）。"""
        matrix = ce.constraint_matrix(user1_profile(), products)
        assert ce.valid_product_ids(matrix) == set(PRODUCT_IDS)

    def test_gamer_user_empty_valid_set(self, products):
        matrix = ce.constraint_matrix(gamer_profile(), products)
        assert ce.valid_product_ids(matrix) == set()

    def test_unknown_hard_constraint_blocks_product(self, products):
        """UNKNOWN≠PASS：水下证据缺失的产品不得进入 E（其余产品不受影响）。"""
        extended = dict(products)
        extended["P006"] = sparse_product("P006")
        matrix = ce.constraint_matrix(swim_profile(), extended)
        valid = ce.valid_product_ids(matrix)
        assert "P006" not in valid
        assert {"P001", "P003", "P004", "P005"} <= valid

    def test_unregistered_need_id_treated_conservatively(self):
        """未登记 need_id 按硬约束保守处理：FAIL/UNKNOWN 拦截，不放水。"""
        from src.schemas import ConstraintResult

        rows = [
            ConstraintResult("P001", "unknown_need", ConstraintOutcome.FAIL),
            ConstraintResult("P002", "unknown_need", ConstraintOutcome.PASS),
        ]
        assert ce.valid_product_ids(rows) == {"P002"}

    def test_empty_constraints_empty_set(self):
        assert ce.valid_product_ids([]) == set()
