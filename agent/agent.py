#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/agent.py — 提交包入口（contracts/platform_contract.json runtime.entry）。

平台协议：
- ``python agent.py --version``：stdout 输出与 agent.json 一致的版本号，退出码 0；
- ``python agent.py --prompt "自然语言指令"``：从指令解析输入/输出目录并完成
  端到端主链，成功退出码 0；解析失败退出码 2 并写日志（日志目录 AGENT_LOG_DIR，
  本地开发缺省 reports/logs/；不硬编码任何 Key，密钥只从环境变量读）。

包布局兼容（sys.path 自举）：本文件把「脚本所在目录」与「其父目录」都加入
sys.path——仓库布局（src/ 在 agent/ 的父目录）与提交包布局（src/ 打进包内、
与 agent.py 同级）下 ``import src.*`` 均可用；提交包布局再把包内 ``lib/``
（pip install --target lib 的产物，raw/Agent本地验证指南.md「agent.py 示例
片段」同款处理）插到最前，保证线上无网络环境下 ``import requests`` 用包内
依赖；仓库布局无 lib/ 目录时该步自动跳过。业务逻辑一律在 src/pipeline.py，
本文件不承载业务。
"""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PARENT = _HERE.parent
for _candidate in (_PARENT, _HERE):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
_LIB = _HERE / "lib"  # 提交包布局：vendored 依赖目录（存在才加，最优先）
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

from src.pipeline import main  # noqa: E402  （自举完成后引用统一 CLI 入口）

if __name__ == "__main__":
    sys.exit(main())
