# -*- coding: utf-8 -*-
"""随包源码零样例取值回归锁。

背景：随包源码不得包含官方样例池任何用户的**具体取值**（称谓/品牌/场景
细节/年龄/预算数字/职业），防止「按样例调出来的产物」观感。此类取值只许存在于：

- tools/（开发侧金标准回归锁，不随包分发）；
- tests/fixtures/（README §7 已声明的离线 mock 网关夹具）；
- raw/、reports/（样例原始资料与开发期记录，不入包）。

本测试扫描随包源码目录（src/ contracts/ prompts/ agent/），命中即失败。
通用词（游泳/办公/教师/图书馆等日常生活词）不在禁表——它们是领域词汇；
禁表只收**可定位到具体样例用户的指纹词**。
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
SCAN_DIRS = ("src", "contracts", "prompts", "agent")
SCAN_SUFFIXES = (".py", ".txt", ".json", ".md")

# 样例用户指纹词（6 位官方样例用户的可定位取值；与
# tests/unit/test_advisor_pipeline.py 中性化清单同源，扩至全部随包源码）
BANNED = (
    "小王", "小李", "小张", "陈女士", "赵先生",
    "地铁", "轰头", "禾岚", "Velmora", "大二", "FPS",
    "40分钟", "28岁", "2000元", "互联网公司", "销售总监", "产品经理",
)


def test_no_sample_values_in_package_sources():
    hits: list[str] = []
    for dirname in SCAN_DIRS:
        root = REPO_ROOT / dirname
        if not root.is_dir():
            continue
        for path in sorted(root.rglob("*")):
            if path.suffix not in SCAN_SUFFIXES or "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8", errors="ignore")
            for word in BANNED:
                if word in text:
                    hits.append(f"{path.relative_to(REPO_ROOT)}: {word}")
    assert not hits, "随包源码命中样例用户指纹词：\n" + "\n".join(hits)
