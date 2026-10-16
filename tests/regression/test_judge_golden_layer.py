# -*- coding: utf-8 -*-
"""开发仓评分器金标准回归锁（tools/deterministic_judge.py，不随提交包分发）：

- 金标准层（规则9 画像事实核对 / U2 偏好）只存在于 tools/，随 golden 参数注入
  src.self_check.judge；打包产物（src/ 单走）不含任何样例金标准取值；
- 用户 1 真链产物（tests/fixtures/self_check_baseline/u1/）在完整两层口径下
  total=100.0、mode=full；
- 多阶段链产物快照在事实层命中「规则9设备冲突」（「现有主要设备」含已换掉的品牌）；
- 出厂质检门 src.self_check.run（不注入 golden）对同两份产物：u1 满分通过、
  降级链快照由泛化事实层（自检设备旧品牌禁入）而非金标准层捕获同一缺陷。
"""
from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

JUDGE_PATH = ROOT / "tools" / "deterministic_judge.py"
FIX_BASELINE = ROOT / "tests" / "fixtures" / "self_check_baseline"
INPUT_DIR = ROOT / "raw" / "dataset_sample" / "Data_for_Users"

_REQUIRED = ("user_profile.md", "product_list.md", "recommendation.md")


@pytest.fixture(scope="module")
def judge_mod():
    """按文件路径装载 tools/deterministic_judge.py（tools/ 非包，不经 import）。"""
    spec = importlib.util.spec_from_file_location("deterministic_judge_under_test", JUDGE_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def setup_module(module):
    missing = [n for n in ("u1", "degrade_chain_snapshot")
               if not all((FIX_BASELINE / n / f).is_file() for f in _REQUIRED)]
    if missing or not (INPUT_DIR / "01_Ecommerce_Listings").is_dir():
        pytest.exit(f"基准夹具缺失：{missing} 或输入目录不完整", returncode=0)


def test_golden_layer_lives_only_in_tools(judge_mod):
    """包内 src.self_check 不得含金标准锚点/规则9/u2 金标准；tools 评分器独有。"""
    import src.self_check as sc
    for name in ("U1_GOLD_ANCHORS", "U1_GOLD_ANCHOR_MIN", "fact_layer_active",
                 "rule9_profile_crosscheck", "u2_preference_items"):
        assert not hasattr(sc, name), f"src.self_check 仍含金标准层符号 {name}"
        assert hasattr(judge_mod, name), f"tools 评分器缺少金标准层符号 {name}"


def test_judge_u1_full_mode_perfect_score(judge_mod):
    """用户 1 真链产物 + 金标准层注入：mode=full、total=100.0、无 issues。"""
    res = judge_mod.judge(str(FIX_BASELINE / "u1"), str(INPUT_DIR))
    assert res["mode"] == "full"
    assert res["fact_layer"] is True
    assert res["total"] == 100.0
    assert res["issues"] == []


def test_judge_catches_old_brand_conflict_in_degrade_snapshot(judge_mod):
    """v0.5.2 旧产物：金标准层命中「规则9设备冲突」（Velmora 混入现有设备）。"""
    res = judge_mod.judge(str(FIX_BASELINE / "degrade_chain_snapshot"), str(INPUT_DIR))
    assert res["mode"] == "full"
    assert res["total"] < 100.0
    assert any("规则9设备冲突" in i for i in res["issues"])


def test_judge_structure_mode_for_other_users(judge_mod, tmp_path):
    """非用户 1 的产物：事实层不启用（mode=structure），金标准不外溢。"""
    out = tmp_path / "output"
    out.mkdir()
    (out / "user_profile.md").write_text(
        "# 用户画像\n\n| 字段组 | 字段名 | 取值 |\n| --- | --- | --- |\n"
        "| 标识 | 画像编号 | 3 |\n| 基本情况 | 性别 | 男 |\n"
        "| 基本情况 | 年龄 | 22岁 |\n| 基本情况 | 职业 | 大学生 |\n"
        "| 购买目标 | 商品品类 | 耳机 |\n| 预算 | 预算上限 | 300元 |\n"
        "| 设备 | 现有主要设备 | 未提供 |\n| 场景 | 场景1 | 未提供 |\n"
        "| 偏好 | 品牌倾向 | 未提供 |\n",
        encoding="utf-8")
    (out / "product_list.md").write_text("# 产品属性列表\n", encoding="utf-8")
    (out / "recommendation.md").write_text("# 消费推荐报告\n", encoding="utf-8")
    res = judge_mod.judge(str(out), str(INPUT_DIR))
    assert res["mode"] == "structure"
    assert res["fact_layer"] is False
    assert res["unmeasured_dims"] == ["U1", "U2"]
    assert not any("规则9" in i for i in res["issues"])
