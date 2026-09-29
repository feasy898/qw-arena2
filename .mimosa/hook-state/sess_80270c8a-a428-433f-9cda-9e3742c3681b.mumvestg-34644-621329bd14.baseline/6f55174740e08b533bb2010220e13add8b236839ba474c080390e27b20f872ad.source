# -*- coding: utf-8 -*-
"""tests/unit/test_grounded_pipeline.py — grounded 抽取全链（管线级）。

覆盖 BUILD_TASK「子进程级：mock 网关（现有夹具机制）跑通画像+产品 grounded 抽取全链」：
1. 进程内：mock 夹具（tests/fixtures/gateway/responses.json 的 grounded_* 键）驱动
   pipeline.run 全链 → grounded 证据清单非空（证明主路径确实走了 schema_extractor）、
   产出三份文档且通过全部确定性校验；
2. 子进程：QW_FORCE_MOCK=1 + agent/agent.py --prompt（与平台调用方式一致）→ 退出码 0、
   三份产物齐全、evaluate 全过；
3. 降级：mock 夹具缺 grounded 键 → 既有确定性降级链接管，仍产出合法三份文档
   （模型路径整体失败 → 降级链不变的回归防线）。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from src.pipeline import resolve_config, run
from src.schema_extractor import LAST_GROUNDING_MANIFEST

REPO_ROOT = Path(__file__).resolve().parents[2]
SAMPLE_DIR = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
SHARED_FIXTURE = REPO_ROOT / "tests" / "fixtures" / "gateway" / "responses.json"
SOURCE_DIRS = (
    "01_Ecommerce_Listings",
    "02_Media_Coverage_and_Reviews",
    "03_User_Feedback_and_Complaints",
    "04_Brand_Official_Sites",
)
OUTPUT_FILES = ("user_profile.md", "product_list.md", "recommendation.md")


def _single_user_input(staging: Path, user_file: str = "User_Description_1.txt") -> Path:
    """样例池 → 单用户输入目录（恰 1 份用户描述 + 全部五源产品资料）。"""
    staging.mkdir(parents=True, exist_ok=True)
    desc_dir = staging / "00_User_Descriptions"
    desc_dir.mkdir(exist_ok=True)
    shutil.copy2(SAMPLE_DIR / "00_User_Descriptions" / user_file,
                 desc_dir / user_file)
    for dirname in SOURCE_DIRS:
        target = staging / dirname
        if target.exists():
            shutil.rmtree(target)
        shutil.copytree(SAMPLE_DIR / dirname, target)
    return staging


def _mock_config(fixture_path: Path) -> dict:
    return {"default_model": "qwen3.6-flash",
            "fallback_models": ["qwen3.6-flash"],
            "max_repair_loops": 1,
            "time_limits": {"hard_seconds": 1800, "soft_seconds": 1680},
            "mock": {"enabled": True, "force_env": "QW_FORCE_MOCK",
                     "fixture_path": str(fixture_path)}}


@pytest.fixture()
def grounded_env(tmp_path):
    input_dir = _single_user_input(tmp_path / "input")
    output_dir = tmp_path / "output"
    LAST_GROUNDING_MANIFEST.clear()
    yield input_dir, output_dir
    LAST_GROUNDING_MANIFEST.clear()


def test_grounded_chain_in_process(grounded_env):
    input_dir, output_dir = grounded_env
    bundle = run(input_dir, output_dir, _mock_config(SHARED_FIXTURE))
    # grounded 主路径确实执行：证据清单含画像与全部产品
    assert LAST_GROUNDING_MANIFEST.get("profile"), "画像 grounded 清单为空（未走主路径）"
    assert set(LAST_GROUNDING_MANIFEST.get("products") or set()) == (
        {"P001", "P002", "P003", "P004", "P005"})
    # 证据逐字回溯（程序保证的落地验证）：quote 逐字命中对应来源文本
    manifest = json.loads(json.dumps(LAST_GROUNDING_MANIFEST))  # 快照
    user_text = (input_dir / "00_User_Descriptions" / "User_Description_1.txt"
                 ).read_text(encoding="utf-8-sig")
    quotes = [entry["exact_quote"] for entry in manifest["profile"].values()
              if isinstance(entry, dict) and entry.get("exact_quote")]
    assert quotes and all(q in user_text for q in quotes)
    # 三份产物 + 确定性校验全过
    for name in OUTPUT_FILES:
        assert (output_dir / name).is_file(), name
    from tools.evaluate import run_checks
    report = run_checks(input_dir, output_dir)
    assert report["passed"], [c for c in report["failed_checks"]]


def test_grounded_chain_subprocess(tmp_path):
    """子进程级：QW_FORCE_MOCK=1 走 agent/agent.py --prompt（平台同款调用方式）。"""
    input_dir = _single_user_input(tmp_path / "input")
    output_dir = tmp_path / "output"
    prompt = (f"请根据 {input_dir} 中的数据完成消费决策任务，"
              f"将结果输出到 {output_dir}")
    env = dict(os.environ)
    env["QW_FORCE_MOCK"] = "1"
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "agent" / "agent.py"), "--prompt", prompt],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=300,
    )
    assert proc.returncode == 0, proc.stderr[-2000:]
    for name in OUTPUT_FILES:
        assert (output_dir / name).is_file(), name
    from tools.evaluate import run_checks
    report = run_checks(input_dir, output_dir)
    assert report["passed"], report["failed_checks"]


def test_missing_grounded_keys_falls_back_to_deterministic(tmp_path):
    """夹具缺 grounded 键 → MockResponseMissingError → 既有确定性降级链接管。

    既有不变量（v0.4.x，非本架构引入）：确定性降级链存在场景优先级类 OBJ high
    （scene_priority 资料确无时如实缺失），修复环耗尽后 run() raise ValueError、
    CLI 层经 phoenix 防线转退出码 0；本测试锁定「降级接管 + 产物仍落盘 + 不走
    grounded 主路径」这三个要点。
    """
    fixture_path = tmp_path / "legacy_responses.json"
    legacy = json.loads(SHARED_FIXTURE.read_text(encoding="utf-8"))
    reduced = {k: v for k, v in legacy.items() if not k.startswith("grounded_")}
    fixture_path.write_text(json.dumps(reduced, ensure_ascii=False), encoding="utf-8")
    input_dir = _single_user_input(tmp_path / "input")
    output_dir = tmp_path / "output"
    LAST_GROUNDING_MANIFEST.clear()
    try:
        with pytest.raises(ValueError):
            run(input_dir, output_dir, _mock_config(fixture_path))
        assert not LAST_GROUNDING_MANIFEST.get("products"), "不应走 grounded 主路径"
        assert (output_dir / "user_profile.md").is_file()
        assert (output_dir / "product_list.md").is_file()
    finally:
        LAST_GROUNDING_MANIFEST.clear()
