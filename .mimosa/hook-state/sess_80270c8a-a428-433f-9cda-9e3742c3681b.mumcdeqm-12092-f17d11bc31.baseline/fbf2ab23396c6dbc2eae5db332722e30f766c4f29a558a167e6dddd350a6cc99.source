# -*- coding: utf-8 -*-
"""tests/regression/test_evaluate.py — 本地评估工具（tools/evaluate.py）回归测试。

夹具：tests/fixtures/eval/
- valid/：mini 输入目录（产品全集 {P001, P002}）+ 合法三份文档（2 款推荐 + SPEC §9
  异常路径空位行）→ evaluate 退出码 0、报告全部检查通过、交叉核对 X1/X2/X3 通过；
- invalid/：三种坏法各一夹具（漏产品 / 表结构不一致 / 推荐数错），断言退出码 1 且
  对应检查项被正确点名（failed_checks 包含期望检查 id，且该 id 下有 high 级问题）。

另断言：报告结构自洽（id 唯一、状态合法、exit_code 与 failed 一致）、CLI 子进程
入口（任意 cwd 下可运行）、运行时长硬截止（E9）、评估器不依赖模型/网关/requests。
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_PATH = REPO_ROOT / "tools" / "evaluate.py"
EVAL_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "eval"
VALID_INPUT = EVAL_FIXTURES / "valid" / "input"
VALID_OUTPUT = EVAL_FIXTURES / "valid" / "output"

# 每种坏法必须被点名的检查项 id（除主码外，报告派生的连带失败见各夹具 README 说明）
INVALID_CASES = {
    "missing_product": {"E2", "X2"},
    "bad_table_structure": {"E4"},
    "wrong_recommendation_count": {"E5", "X3"},
}

EXPECTED_PASS_IDS = ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10", "OBJ", "X1", "X2", "X3"]
LEGAL_STATUSES = {"pass", "fail", "skipped"}


@pytest.fixture(scope="module")
def evaluate_mod():
    """按文件路径装载 tools/evaluate.py（tools/ 非包，不经 import tools.evaluate）。"""
    spec = importlib.util.spec_from_file_location("evaluate_under_test", EVAL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def parse_report(capsys) -> dict:
    captured = capsys.readouterr()
    return json.loads(captured.out)


def check_by_id(report: dict, check_id: str) -> dict:
    return next(c for c in report["checks"] if c["id"] == check_id)


def high_ids(report: dict) -> set[str]:
    return {
        c["id"] for c in report["checks"]
        if any(i["severity"] == "high" for i in c["issues"])
    }


# ---------------------------------------------------------------------------
# valid 夹具：退出码 0，全部检查通过
# ---------------------------------------------------------------------------

class TestValidFixture:
    def test_exit_zero_and_all_checks_pass(self, evaluate_mod, capsys):
        exit_code = evaluate_mod.evaluate(VALID_INPUT, VALID_OUTPUT)
        report = parse_report(capsys)
        assert exit_code == 0
        assert report["exit_code"] == 0
        assert report["passed"] is True
        assert report["failed_checks"] == []
        assert report["product_universe"] == ["P001", "P002"]
        assert [c["id"] for c in report["checks"]] == EXPECTED_PASS_IDS
        for check in report["checks"]:
            assert check["status"] == "pass", f"{check['id']} 应通过：{check['summary']}"
            assert check["summary"], "每项检查须带说明"

    def test_cross_checks_verify_universe(self, evaluate_mod, capsys):
        evaluate_mod.evaluate(VALID_INPUT, VALID_OUTPUT)
        report = parse_report(capsys)
        # X1：从 input_dir 独立重建全集
        assert check_by_id(report, "X1")["status"] == "pass"
        # X2：product_list.md 节集合与全集无差集
        assert check_by_id(report, "X2")["status"] == "pass"
        # X3：recommendation.md 推荐+未选并集 = 全集（3+其余 口径）
        x3 = check_by_id(report, "X3")
        assert x3["status"] == "pass"
        assert "3+其余" in x3["summary"]

    def test_valid_bundle_zero_high_issues_baseline(self, evaluate_mod):
        """不经报告分组，直接核对 validators 基线：合法夹具 0 issue。"""
        report = evaluate_mod.run_checks(VALID_INPUT, VALID_OUTPUT)
        assert report["issue_count"] == {"high": 0, "medium": 0, "low": 0}

    def test_cli_subprocess_exit_zero_from_any_cwd(self, tmp_path):
        """CLI 子进程：在仓库外 cwd 运行（验证 sys.path 自举），退出码 0。"""
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, str(EVAL_PATH), str(VALID_INPUT), str(VALID_OUTPUT)],
            capture_output=True, text=True, encoding="utf-8", cwd=tmp_path, env=env,
        )
        assert proc.returncode == 0, proc.stdout + proc.stderr
        report = json.loads(proc.stdout)
        assert report["passed"] is True
        assert report["exit_code"] == 0
        assert proc.stderr == ""


# ---------------------------------------------------------------------------
# invalid 夹具：退出码 1，坏法被正确点名
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case,expected_ids", sorted(INVALID_CASES.items()), ids=list(INVALID_CASES))
class TestInvalidFixtures:
    def test_exit_one_and_named_checks_fail(self, evaluate_mod, case, expected_ids):
        case_root = EVAL_FIXTURES / "invalid" / case
        exit_code = evaluate_mod.evaluate(case_root / "input", case_root / "output")
        assert exit_code == 1
        # 再经 run_checks 复核报告内容（evaluate 内部同源）
        report = evaluate_mod.run_checks(case_root / "input", case_root / "output")
        assert report["exit_code"] == 1
        assert report["passed"] is False
        assert set(expected_ids) <= set(report["failed_checks"]), (
            f"{case} 应点名 {sorted(expected_ids)}，实报 {report['failed_checks']}"
        )
        assert set(expected_ids) <= high_ids(report), (
            f"{case} 被点名检查项应含 high 级问题"
        )
        for check_id in expected_ids:
            check = check_by_id(report, check_id)
            assert check["status"] == "fail"
            assert check["issues"], f"{case}/{check_id} 应附问题明细"


# ---------------------------------------------------------------------------
# 报告结构 / 资源限制 / 依赖纪律
# ---------------------------------------------------------------------------

class TestReportContract:
    def test_report_shape(self, evaluate_mod):
        report = evaluate_mod.run_checks(VALID_INPUT, VALID_OUTPUT)
        ids = [c["id"] for c in report["checks"]]
        assert len(ids) == len(set(ids)), "检查项 id 须唯一"
        for check in report["checks"]:
            assert set(check) == {"id", "name", "status", "issues", "summary"}
            assert check["status"] in LEGAL_STATUSES
            for issue in check["issues"]:
                assert set(issue) == {"code", "severity", "message"}
                assert issue["severity"] in {"high", "medium", "low"}
        assert report["passed"] == (report["exit_code"] == 0)
        assert report["failed_checks"] == [c["id"] for c in report["checks"] if c["status"] == "fail"]

    def test_output_dir_missing_fails_e1(self, evaluate_mod, tmp_path):
        report = evaluate_mod.run_checks(VALID_INPUT, tmp_path / "not_exist")
        assert report["exit_code"] == 1
        assert "E1" in report["failed_checks"]
        # 产物缺失时交叉核对如实标注 skipped，不伪造通过
        assert check_by_id(report, "X2")["status"] == "skipped"
        assert check_by_id(report, "X3")["status"] == "skipped"

    def test_run_seconds_over_hard_limit_fails_e9(self, evaluate_mod):
        report = evaluate_mod.run_checks(VALID_INPUT, VALID_OUTPUT, run_seconds=1801.0)
        assert report["exit_code"] == 1
        assert "E9" in report["failed_checks"]

    def test_run_seconds_within_limit_passes(self, evaluate_mod):
        report = evaluate_mod.run_checks(VALID_INPUT, VALID_OUTPUT, run_seconds=100.0)
        assert report["exit_code"] == 0
        assert check_by_id(report, "E9")["status"] == "pass"

    def test_cli_accepts_run_seconds_flag(self, tmp_path):
        env = dict(os.environ, PYTHONIOENCODING="utf-8")
        proc = subprocess.run(
            [sys.executable, str(EVAL_PATH), str(VALID_INPUT), str(VALID_OUTPUT),
             "--run-seconds", "1801"],
            capture_output=True, text=True, encoding="utf-8", cwd=tmp_path, env=env,
        )
        assert proc.returncode == 1
        report = json.loads(proc.stdout)
        assert "E9" in report["failed_checks"]

    def test_no_model_or_gateway_dependency(self):
        """评估器纪律：不依赖模型/网关，不发网络请求（仅标准库 + src 确定性模块）。"""
        source = EVAL_PATH.read_text(encoding="utf-8")
        assert "model_gateway" not in source
        assert not re.search(r"^\s*(import|from)\s+requests\b", source, re.M)
        assert not re.search(r"^\s*(import|from)\s+(urllib\.request|socket)\b", source, re.M)
