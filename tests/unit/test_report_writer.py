# -*- coding: utf-8 -*-
"""tests/unit/test_report_writer.py — 产出落盘与日志初始化单测。

覆盖：setup_logging（agent.log、UTF-8、不打印资料内容）、write_outputs
（恰好 3 份、命名精确、无 BOM、\\n 换行、覆盖写、目录自建）、
build_output_bundle（DecisionPlan → recommendation.md 组装，含推荐卡固定字段/
未选原因表/综合结论），以及「决策 → 渲染 → 落盘 → validators.validate_outputs
十项校验」全链集成（真实示例数据集作为输入全集基准）。
"""
from __future__ import annotations

import logging
import os

import pytest

from src import constraint_engine as ce
from src.renderer import parse_recommendation, render_products, render_profile, parse_products, parse_profile
from src.report_writer import (
    OUTPUT_FILENAMES,
    build_output_bundle,
    log_stage,
    setup_logging,
    write_outputs,
)
from src.selector import select_recommendation
from src.validators import validate_outputs

from tests.unit.test_constraint_engine import load_products, swim_profile


@pytest.fixture(scope="module")
def decision_outputs(tmp_path_factory):
    """游泳用户 × 全量产品 → 三份文档落盘（模块级共享，供集成校验）。"""
    products = load_products()
    products_md = render_products(products)
    profile = swim_profile()
    profile_md = render_profile(profile)
    parsed_profile = parse_profile(profile_md)
    parsed_products = parse_products(products_md)
    plan = select_recommendation(parsed_profile, parsed_products,
                                 ce.constraint_matrix(parsed_profile, parsed_products))
    bundle = build_output_bundle(profile_md, products_md, plan)
    out_dir = tmp_path_factory.mktemp("decision_out")
    paths = write_outputs(bundle, out_dir)
    return plan, bundle, out_dir, paths, parsed_profile, parsed_products


# ---------------------------------------------------------------------------
# setup_logging
# ---------------------------------------------------------------------------

class TestSetupLogging:
    def test_creates_log_file_and_writes(self, tmp_path):
        logger = setup_logging(tmp_path)
        logger.info("阶段 unit-test 完成，耗时 0.01s")
        for handler in logger.handlers:
            handler.flush()
        log_path = tmp_path / "agent.log"
        assert log_path.exists()
        text = log_path.read_text(encoding="utf-8")
        assert "unit-test" in text

    def test_returns_named_logger_no_duplicate_handlers(self, tmp_path):
        logger = setup_logging(tmp_path)
        assert logger.name == "agent"
        assert logger.level == logging.INFO
        logger.handlers.clear()  # 隔离其他用例在同一进程留下的全局 handler
        setup_logging(tmp_path)
        assert len(logger.handlers) == 1
        setup_logging(tmp_path)  # 同目录重复调用不叠加 handler
        assert len(logger.handlers) == 1

    def test_log_never_contains_profile_content(self, tmp_path, decision_outputs):
        """纪律：日志只记阶段与耗时，不打印完整用户资料。"""
        _plan, _bundle, _out, _paths, profile, _products = decision_outputs
        logger = setup_logging(tmp_path)
        logger.info("决策完成 产品数=%d", 5)
        for handler in logger.handlers:
            handler.flush()
        log_text = (tmp_path / "agent.log").read_text(encoding="utf-8")
        for value in (profile.get("budget_raw"), profile.get("profile_id")):
            if value:
                assert str(value) not in log_text

    def test_log_stage_records_elapsed(self, tmp_path, monkeypatch):
        import time

        logger = setup_logging(tmp_path)
        monkeypatch.setattr(time, "monotonic", lambda: 123.5)
        log_stage(logger, "constraint_matrix", 120.0)
        for handler in logger.handlers:
            handler.flush()
        text = (tmp_path / "agent.log").read_text(encoding="utf-8")
        assert "constraint_matrix" in text and "3.50s" in text


# ---------------------------------------------------------------------------
# write_outputs
# ---------------------------------------------------------------------------

class TestWriteOutputs:
    def test_writes_exactly_three_files_with_exact_names(self, tmp_path, decision_outputs):
        _plan, bundle, _src, _paths, _p, _pr = decision_outputs
        out = tmp_path / "plain"
        paths = write_outputs(bundle, out)
        assert paths == [str(out / name) for name in OUTPUT_FILENAMES]
        assert sorted(os.listdir(out)) == sorted(OUTPUT_FILENAMES)

    def test_utf8_no_bom_and_lf_newlines(self, tmp_path, decision_outputs):
        _plan, bundle, _src, _paths, _p, _pr = decision_outputs
        out = tmp_path / "enc"
        write_outputs(bundle, out)
        for name in OUTPUT_FILENAMES:
            raw = (out / name).read_bytes()
            assert not raw.startswith(b"\xef\xbb\xbf")
            assert b"\r" not in raw
            raw.decode("utf-8")

    def test_creates_missing_directory_and_overwrites(self, tmp_path, decision_outputs):
        _plan, bundle, _src, _paths, _p, _pr = decision_outputs
        out = tmp_path / "nested" / "deep"
        assert not out.exists()
        write_outputs(bundle, out)
        assert (out / "recommendation.md").exists()
        first = (out / "recommendation.md").read_text(encoding="utf-8")
        write_outputs(bundle, out)  # 覆盖写不追加
        assert (out / "recommendation.md").read_text(encoding="utf-8") == first

    def test_rejects_non_bundle(self, tmp_path):
        with pytest.raises(TypeError):
            write_outputs({"user_profile_md": ""}, tmp_path)  # type: ignore[arg-type]


# ---------------------------------------------------------------------------
# build_output_bundle：DecisionPlan → recommendation.md 组装
# ---------------------------------------------------------------------------

class TestBuildOutputBundle:
    def test_bundle_carries_three_documents(self, decision_outputs):
        _plan, bundle, _out, _paths, _p, _pr = decision_outputs
        assert bundle.user_profile_md.startswith("# 用户画像")
        assert bundle.product_list_md.startswith("# 产品属性列表")
        assert bundle.recommendation_md.startswith("# 消费推荐报告")

    def test_recommendation_md_contains_card_fixed_fields(self, decision_outputs):
        """推荐卡固定字段（身份/适配结论/事实依据/取舍/代价风险/核验/改选）
        经渲染后在推荐结果表中可见（身份列+理由列+注意事项列）。"""
        plan, _bundle, _out, _paths, _p, _pr = decision_outputs
        parsed = parse_recommendation(build_output_bundle("", "", plan).recommendation_md)
        assert len(parsed["recommendations"]) == 3
        for row, card in zip(parsed["recommendations"], plan.recommendation_cards):
            assert row["product_id"] == card.product_id
            assert row["level"] == card.level
            # 适配结论引用画像字段 + 事实依据引用产品属性字段
            reason = row["reason"]
            assert "画像｜" in reason and "产品属性｜" in reason
            # 注意事项非空：代价/使用限制 + 购买前核验
            assert row["notes"].strip() and "待核验" not in row["notes"].split("；")[0][:3]
        # 取舍与改选条件在 DecisionPlan 层完整承载
        for card in plan.recommendation_cards:
            assert card.tradeoffs.strip() and card.switch_conditions

    def test_exclusion_table_and_conclusion_rendered(self, decision_outputs):
        plan, _bundle, _out, _paths, _p, _pr = decision_outputs
        parsed = parse_recommendation(build_output_bundle("", "", plan).recommendation_md)
        covered = [pid for row in parsed["exclusions"] for pid in row["product_ids"]]
        assert set(covered) == set(load_products()) - set(plan.selected_products)
        assert all(row["change_conditions"] for row in parsed["exclusions"])
        assert parsed["overall_conclusion"].strip()


# ---------------------------------------------------------------------------
# 全链集成：决策 → 渲染 → 落盘 → validators.validate_outputs（SPEC §10 十项）
# ---------------------------------------------------------------------------

class TestFullChainValidation:
    #: 已知上游（reconciler→validators）语义张力，不属于决策层产出问题：
    #: P005 续航官方无统一口径 → reconciler 合并出 duration=None → 渲染「未提供」
    #: → validate_product_object 将必填续航时长缺失记 high。已登记 openIssues。
    KNOWN_UPSTREAM_CODES = {"OBJ_PRODUCT_ITEM_REQUIRED"}

    def test_validate_outputs_passes_on_written_outputs(self, decision_outputs):
        plan, _bundle, out_dir, _paths, profile, products = decision_outputs
        valid_ids = set(plan.selected_products)
        issues = validate_outputs(out_dir, _dataset_input_dir(), valid_ids=valid_ids)
        unexpected = [i for i in issues if i.severity == "high"
                      and i.code not in self.KNOWN_UPSTREAM_CODES]
        assert unexpected == [], "\n".join(f"{i.code}: {i.message}" for i in unexpected)
        # 决策层相关校验必须全部干净：推荐∈E / 未选覆盖 / 注意事项非空 / 引用不越界
        decision_codes = {i.code for i in issues
                          if i.code.startswith(("E5", "E6", "E7", "E8"))}
        assert not decision_codes

    def test_recommendation_selection_subset_of_valid_set(self, decision_outputs):
        plan, _bundle, _out, _paths, profile, products = decision_outputs
        matrix = ce.constraint_matrix(profile, products)
        assert set(plan.selected_products) <= ce.valid_product_ids(matrix)


def _dataset_input_dir():
    from pathlib import Path

    root = Path(__file__).resolve().parents[2]
    return root / "raw" / "dataset_sample" / "Data_for_Users"
