# -*- coding: utf-8 -*-
"""tests/regression/test_eval_set.py — eval 集全量回归（2026-10-01 扩充批）。

eval 集 = tests/fixtures/eval/：1 个 valid 夹具（评估应全过）+ invalid/ 下 16 个
坏法夹具（评估应退出 1 且点名各自期望检查项）。本文件是全集的统一跑法与清单：

- valid：exit 0、全部检查 pass（EXPECTED_PASS_IDS 与 test_evaluate.py 同清单）；
- 每个 invalid：exit 1，声明的期望检查项被点名、status=fail、且含 high 级问题
  （子集口径，与 test_evaluate.py INVALID_CASES 同约定；未声明的连带失败不锁死，
  保持对评估器演进鲁棒）；
- 集合盘点：invalid/ 目录与声明清单双向一致（夹具静默增删即红）；
- 确定性：同一 case 连跑两次 failed_checks 完全一致；
- CLI 全量：每个 case 经 ``python tools/evaluate.py`` 子进程实跑一遍，
  valid=0 / invalid=1 逐案断言（退出码为证）。

扩充批 13 案覆盖的失分场景类型与 3 条真实 badcase 的出处（submission_log 行号 /
GROK_ASK5 / progress.md）见 EVAL_SET_CASES 各注释与 tests/fixtures/eval/README.md
「2026-10-01 扩充批」一节。评估器与全部夹具均为确定性判定，不依赖模型/网关。
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_PATH = REPO_ROOT / "tools" / "evaluate.py"
EVAL_FIXTURES = REPO_ROOT / "tests" / "fixtures" / "eval"
VALID_INPUT = EVAL_FIXTURES / "valid" / "input"
VALID_OUTPUT = EVAL_FIXTURES / "valid" / "output"

EXPECTED_PASS_IDS = ["E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9", "E10",
                     "OBJ", "X1", "X2", "X3"]

# ---------------------------------------------------------------------------
# eval 集清单：case 名 → (期望被点名检查项, 出处/失分场景说明)
# ---------------------------------------------------------------------------

LEGACY_CASES = {
    # —— 首批三案（tests/regression/test_evaluate.py 已断言，此处入盘点清单）——
    "missing_product": ({"E2", "X2"}, "首批：product_list 漏产品节"),
    "bad_table_structure": ({"E4"}, "首批：产品表缺「编号别名」行"),
    "wrong_recommendation_count": ({"E5", "X3"}, "首批：推荐结果表行数错"),
}

EXPANSION_CASES = {
    # —— 10 个失分场景类型（策略回归锁；出处=submission_log/progress.md 各失分阶段）——
    "degrade_empty_product_table": (
        {"E10", "E8", "X2"},
        "降级空产品表（v0.4.5 前降级链产物：product_list 只剩标题；submission_log 行13/14"
        " 确定性抽取取代空表的失分形态）"),
    "recommendation_outside_valid_set": (
        {"E5"},
        "推荐∉有效集 E（预算收紧→P001 出 E 仍被推荐；审计修复1 场景；R1 硬约束纪律="
        " answer_E 判定的 12 分制假设下期望增量最大方向）"),
    "brand_model_renamed": (
        {"E3"},
        "品牌型号改写（官方「品牌型号原样沿用」纪律）"),
    "broken_table_row_parse": (
        {"E10", "E8"},
        "表格坏行（P001 行多一列→文档不可解析；平台结构检查轴）"),
    "citation_out_of_bounds": (
        {"E8"},
        "引用越界（推荐理由引全集外产品/不存在字段；answer_E「证据纪律扣分」关切面）"),
    "card_notes_empty": (
        {"E7"},
        "推荐卡注意事项留空（官方要求风险/核验字段不得留空）"),
    "scene_priority_enum_violation": (
        {"OBJ"},
        "优先级判定依据枚举违规（scene_priority 契约轴的枚举面；v0.5.1 修复的契约"
        " field_catalog scenes.value_rule 同轴）"),
    "exclusion_gap": (
        {"E6", "X3"},
        "未选原因漏覆盖（E6 逐款覆盖 + X3 3+其余全集口径）"),
    "missing_output_file": (
        {"E1"},
        "产物文件数量不符（submission_log 行6 平台 failed 实录：v0.4.0-probe"
        "「产物文件数量与任务要求不符」）"),
    "bom_encoding_e9": (
        {"E9", "E10"},
        "UTF-8 BOM（平台要求无 BOM/\\n；windev→GPU 迁移实测编码风险面）"),
    # —— 3 条真实 badcase（昨日出分日志/answer_E 提炼，出处到行）——
    "badcase_degrade_comment_e10": (
        {"E10", "E8"},
        "badcase1：v0.4.11/12 降级诊断 HTML 注释入 user_profile.md 顶部→E10/E8 崩"
        "（submission_log 行21 平台 scored=2「注释bug实锤」、行22 删特性恢复；注释"
        "格式/落点=src/pipeline.py degrade 分支原样）"),
    "badcase_scene_priority_empty_draft": (
        {"E10", "E5", "X2", "X3"},
        "badcase2：v0.5.0 scene_priority/priority_basis 校验矛盾→修复环用尽→phoenix"
        " 空草稿（submission_log 行30 平台 2 分疑似根因；progress.md:47；v0.5.1 修复后"
        "结构 3/6→6/6；产物形态=agent/agent.py _fallback_templates 原样）"),
    "badcase_officialsite_fields_dropped": (
        {"E4"},
        "badcase3：官网五字段开关（GROK_ASK5/answer_E 主议题；submission_log 行25"
        " v0.4.16 直写官网字段 11→1、行30 v0.5.1 停采=「列在格空，非删列」）。本地锁定"
        "同一开关轴不变量：契约行只可空（未提供）不可删——删行=E4 表结构不一致"),
}

EVAL_SET_CASES = {**LEGACY_CASES, **EXPANSION_CASES}


@pytest.fixture(scope="module")
def evaluate_mod():
    """按文件路径装载 tools/evaluate.py（tools/ 非包，不经 import tools.evaluate）。"""
    spec = importlib.util.spec_from_file_location("evaluate_eval_set_under_test", EVAL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# ---------------------------------------------------------------------------
# valid 夹具
# ---------------------------------------------------------------------------

class TestEvalSetValid:
    def test_valid_bundle_exit_zero_all_pass(self, evaluate_mod, capsys):
        exit_code = evaluate_mod.evaluate(VALID_INPUT, VALID_OUTPUT)
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["passed"] is True
        assert report["failed_checks"] == []
        assert [c["id"] for c in report["checks"]] == EXPECTED_PASS_IDS


# ---------------------------------------------------------------------------
# invalid 全量：逐案 exit 1 + 期望检查项被点名（含 high）
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("case,declared", sorted(EVAL_SET_CASES.items()), ids=list(EVAL_SET_CASES))
class TestEvalSetInvalid:
    def test_exit_one_and_declared_checks_named(self, evaluate_mod, case, declared, capsys):
        expected_ids, _provenance = declared
        case_root = EVAL_FIXTURES / "invalid" / case
        exit_code = evaluate_mod.evaluate(case_root / "input", case_root / "output")
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 1, f"{case} 应判失败"
        assert report["passed"] is False
        assert set(expected_ids) <= set(report["failed_checks"]), (
            f"{case} 应点名 {sorted(expected_ids)}，实报 {report['failed_checks']}"
        )
        for check in report["checks"]:
            if check["id"] in expected_ids:
                assert check["status"] == "fail"
                assert check["issues"], f"{case}/{check['id']} 应附问题明细"
                assert any(i["severity"] == "high" for i in check["issues"]), (
                    f"{case}/{check['id']} 应含 high 级问题")


# ---------------------------------------------------------------------------
# 集合盘点 / 确定性 / CLI 全量
# ---------------------------------------------------------------------------

class TestEvalSetInventory:
    def test_invalid_dirs_match_declaration_bidirectionally(self):
        on_disk = {p.name for p in (EVAL_FIXTURES / "invalid").iterdir() if p.is_dir()}
        assert on_disk == set(EVAL_SET_CASES), (
            f"夹具与清单不一致；盘多：{sorted(on_disk - set(EVAL_SET_CASES))}；"
            f"清单多：{sorted(set(EVAL_SET_CASES) - on_disk)}")

    def test_expansion_batch_net_increase_and_badcases(self):
        """扩充批验收口径：净增 ≥10 且其中含 3 条真实 badcase。"""
        assert len(EXPANSION_CASES) == 13
        badcases = {name for name in EXPANSION_CASES if name.startswith("badcase_")}
        assert badcases == {
            "badcase_degrade_comment_e10",
            "badcase_scene_priority_empty_draft",
            "badcase_officialsite_fields_dropped",
        }
        # 全集检查项覆盖面：14 项检查（E1..E10/OBJ/X1..X3）中，X1 只由输入侧触发
        # （输出侧坏法不涉及），其余 13 项均有对应用例点名。
        covered = set().union(*(ids for ids, _ in EVAL_SET_CASES.values()))
        assert covered == {"E1", "E2", "E3", "E4", "E5", "E6", "E7", "E8", "E9",
                           "E10", "OBJ", "X2", "X3"}


class TestEvalSetDeterminism:
    def test_repeat_run_identical_failed_checks(self, evaluate_mod, capsys):
        """同一 case 连跑两次结果一致（eval 集与评估器均为确定性判定）。"""
        case_root = EVAL_FIXTURES / "invalid" / "badcase_scene_priority_empty_draft"
        first = evaluate_mod.run_checks(case_root / "input", case_root / "output")
        second = evaluate_mod.run_checks(case_root / "input", case_root / "output")
        assert first["failed_checks"] == second["failed_checks"]
        assert first["exit_code"] == second["exit_code"] == 1

    def test_fixtures_are_utf8_lf_except_bom_case(self):
        """夹具编码纪律：除 bom_encoding_e9（存坏考据）外全部 UTF-8 无 BOM、LF。"""
        for case in EVAL_SET_CASES:
            for md in (EVAL_FIXTURES / "invalid" / case / "output").glob("*.md"):
                raw = md.read_bytes()
                if case == "bom_encoding_e9":
                    assert raw.startswith(b"\xef\xbb\xbf"), f"{md} 应带 BOM（坏法本体）"
                else:
                    assert not raw.startswith(b"\xef\xbb\xbf"), f"{md} 意外带 BOM"
                    assert b"\r\n" not in raw, f"{md} 含 CRLF"


@pytest.fixture(scope="module")
def cli_exit_codes(tmp_path_factory):
    """CLI 子进程全量实跑一遍：{case or 'valid': returncode}（退出码为证）。"""
    cwd = tmp_path_factory.mktemp("cli_cwd")
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    codes: dict[str, int] = {}
    for name in EVAL_SET_CASES:
        case_root = EVAL_FIXTURES / "invalid" / name
        proc = subprocess.run(
            [sys.executable, str(EVAL_PATH),
             str(case_root / "input"), str(case_root / "output")],
            capture_output=True, text=True, encoding="utf-8", cwd=cwd, env=env,
        )
        assert proc.stderr == "", f"{name} CLI stderr 非空：{proc.stderr[-300:]}"
        report = json.loads(proc.stdout)
        assert report["exit_code"] == proc.returncode
        codes[name] = proc.returncode
    proc = subprocess.run(
        [sys.executable, str(EVAL_PATH), str(VALID_INPUT), str(VALID_OUTPUT)],
        capture_output=True, text=True, encoding="utf-8", cwd=cwd, env=env,
    )
    assert proc.stderr == ""
    codes["valid"] = proc.returncode
    return codes


class TestEvalSetCli:
    def test_cli_invalid_cases_exit_one(self, cli_exit_codes):
        failed = {n for n, c in cli_exit_codes.items() if n != "valid" and c != 1}
        assert not failed, f"invalid 案 CLI 退出码应为 1：{failed}"

    def test_cli_valid_case_exit_zero(self, cli_exit_codes):
        assert cli_exit_codes["valid"] == 0


class TestEvalSetDiscipline:
    def test_runner_has_no_model_or_network_dependency(self):
        """eval 集跑法纪律：不 import 模型网关/网络库（与 test_evaluate.py 同口径的
        import 行正则），全文件无网关模块名与密钥环境变量名（检索词拼装防自指）。"""
        import re
        for source in (Path(__file__).read_text(encoding="utf-8"),
                       EVAL_PATH.read_text(encoding="utf-8")):
            assert not re.search(r"^\s*(import|from)\s+requests\b", source, re.M)
            assert not re.search(r"^\s*(import|from)\s+(urllib\.request|socket)\b", source, re.M)
            for needle in ("model_" + "gateway", "dash" + "scope", "DASH" + "SCOPE_API_KEY"):
                assert needle not in source, f"出现禁用依赖字面：{needle}"
