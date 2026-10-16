# -*- coding: utf-8 -*-
"""tests/regression/test_evaluate_valid_set.py — 评估器有效集 E 独立重建（审计修复1）。

审计原发现：tools/evaluate.py 只调 validate_outputs 而未传 valid_ids，「推荐∈E」
只在 pipeline 内部生效。修复后评估器以 constraint_engine 从落盘前两份文档真实回读
（与管线决策阶段相同输入口径）独立重建 E 并作为 valid_ids 传入校验：
- 合法产出：报告含重建的 valid_set，E5 汇总注明重建口径，退出码 0；
- 推荐含 ∉E 产品（篡改画像预算→P001 出 E 仍被推荐）：E5 high「不在有效集 E」，退出 1；
- 文档不可解析：E 重建如实跳过（不伪造 E），结构问题由 E10 记录。
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
EVAL_PATH = REPO_ROOT / "tools" / "evaluate.py"
VALID_INPUT = REPO_ROOT / "tests" / "fixtures" / "eval" / "valid" / "input"
VALID_OUTPUT = REPO_ROOT / "tests" / "fixtures" / "eval" / "valid" / "output"


@pytest.fixture(scope="module")
def evaluate_mod():
    import importlib.util
    spec = importlib.util.spec_from_file_location("evaluate_valid_set_under_test", EVAL_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _copy_valid_output(tmp_path: Path, mutate=None) -> Path:
    out = tmp_path / "output"
    out.mkdir()
    for name in ("user_profile.md", "product_list.md", "recommendation.md"):
        text = (VALID_OUTPUT / name).read_text(encoding="utf-8")
        if mutate and name == "user_profile.md":
            text = mutate(text)
        (out / name).write_text(text, encoding="utf-8", newline="\n")
    return out


def _hard_budget_600(text: str) -> str:
    """预算改为「上限 600 元」→ P001（799元）硬约束 FAIL，出有效集。"""
    replaced = text
    for old, new in (
        ("| 预算 | 预算上限 | 800元 |", "| 预算 | 预算上限 | 600元 |"),
        ("| 预算 | 预算原文表述 | 预算大概800左右 |", "| 预算 | 预算原文表述 | 预算不超过600元 |"),
        ("| 预算 | 预算口径 | 约(单值) |", "| 预算 | 预算口径 | 上限 |"),
    ):
        assert old in text, f"夹具基准行缺失：{old}"
        replaced = replaced.replace(old, new)
    return replaced


class TestValidSetRebuild:
    def test_valid_fixture_rebuilds_e_and_passes(self, evaluate_mod, capsys):
        exit_code = evaluate_mod.evaluate(VALID_INPUT, VALID_OUTPUT)
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 0
        assert report["valid_set"] == ["P001"]
        e5 = next(c for c in report["checks"] if c["id"] == "E5")
        assert e5["status"] == "pass"
        assert "独立重建" in e5["summary"] and "constraint_engine" in e5["summary"]

    def test_recommendation_outside_e_fails_e5(self, evaluate_mod, tmp_path, capsys):
        """P001 出 E（硬预算 600 不过）却仍被推荐 → E5 high「不在有效集 E」。"""
        output = _copy_valid_output(tmp_path, mutate=_hard_budget_600)
        exit_code = evaluate_mod.evaluate(VALID_INPUT, output)
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        # 预算收紧为硬上限 600 后：P001 展示价 799 元 FAIL，P002 水下证据不足 UNKNOWN
        # → 有效集为空（SPEC §9 异常路径），两款被推荐产品均 ∉ E
        assert report["valid_set"] == []
        assert "E5" in report["failed_checks"]
        e5 = next(c for c in report["checks"] if c["id"] == "E5")
        assert any("不在有效集 E" in i["message"] and i["severity"] == "high"
                   and "P001" in i["message"] for i in e5["issues"])

    def test_unparseable_docs_skip_rebuild_without_faking(self, evaluate_mod, tmp_path,
                                                          capsys):
        """文档不可解析 → E 重建如实跳过（valid_set=None），不伪造、不崩溃。"""
        output = _copy_valid_output(tmp_path)
        (output / "user_profile.md").write_text("这不是合法的画像表格\n", encoding="utf-8")
        exit_code = evaluate_mod.evaluate(VALID_INPUT, output)
        report = json.loads(capsys.readouterr().out)
        assert exit_code == 1
        assert report["valid_set"] is None
        e5 = next(c for c in report["checks"] if c["id"] == "E5")
        assert "解析失败" in e5["summary"] and "E10" in e5["summary"]
        assert "E10" in report["failed_checks"]

    def test_rebuild_uses_no_model_gateway(self):
        """重建只依赖确定性模块（constraint_engine/renderer），不引入模型网关。"""
        source = EVAL_PATH.read_text(encoding="utf-8")
        assert "model_gateway" not in source
        assert "constraint_engine" in source
