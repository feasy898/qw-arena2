# -*- coding: utf-8 -*-
"""出厂质检基准回归（不拉真模型）：

对既有产物目录跑 src.self_check.run，断言分数阈值与 atoms 命中：
- 真链满分产物快照（tests/fixtures/self_check_baseline/u1/）：passed=True、
  score_hint=100、无 atoms；
- 多阶段降级链产物快照（tests/fixtures/self_check_baseline/degrade_chain_snapshot/，
  已知含模板残留、旧设备品牌冲突等缺陷）：passed=False、score_hint<100、
  failed_atoms 命中预期规则；
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.self_check import run  # noqa: E402

FIX_BASELINE = ROOT / "tests" / "fixtures" / "self_check_baseline"
INPUT_DIR = ROOT / "raw" / "dataset_sample" / "Data_for_Users"  # 与 /tmp/p061b/ws/input 逐字节一致

_REQUIRED = ("user_profile.md", "product_list.md", "recommendation.md")


def setup_module(module):
    missing = [n for n in ("u1", "degrade_chain_snapshot")
               if not all((FIX_BASELINE / n / f).is_file() for f in _REQUIRED)]
    if missing or not (INPUT_DIR / "01_Ecommerce_Listings").is_dir():
        pytest.exit(f"基准夹具缺失：{missing} 或输入目录不完整", returncode=0)


def test_u1_real_chain_output_passes_gate():
    """u1 真链产物（/tmp/p061b 快照）必须 passed=True（judge 总分 100.0 的回归锁）。"""
    res = run(str(FIX_BASELINE / "u1"), str(INPUT_DIR))
    assert res["passed"] is True, res["failed_atoms"]
    assert res["failed_atoms"] == []
    assert res["score_hint"] == 100.0
    assert set(res) == {"passed", "score_hint", "failed_atoms"}


def test_degrade_chain_snapshot_fails_with_expected_rules():
    """多阶段链产物快照：passed=False、score_hint<100，atoms 命中已知缺陷规则
    （设备旧品牌冲突 / 模板残留 / 表结构不一致）。"""
    res = run(str(FIX_BASELINE / "degrade_chain_snapshot"), str(INPUT_DIR))
    assert res["passed"] is False
    assert res["score_hint"] < 100.0
    assert res["failed_atoms"]
    for a in res["failed_atoms"]:
        assert set(a) == {"doc", "rule", "message"}
        assert a["doc"] in ("user_profile.md", "product_list.md",
                            "recommendation.md", "输出目录")
    rules = {a["rule"] for a in res["failed_atoms"]}
    assert "自检设备旧品牌禁入" in rules       # 「现有主要设备」含已换掉的旧品牌（泛化层捕获）
    assert "规则7模板残留" in rules            # 过程性措辞/截断残留
    assert "规则4结构一致" in rules            # 产品表字段名集合不一致
    assert any("Velmora" in a["message"] for a in res["failed_atoms"])

