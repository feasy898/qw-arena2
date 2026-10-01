#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""极简探针 agent（v0.4.0-probe）：诊断平台评测管线用。

- 纯标准库、无模型调用、无第三方依赖、确定性、亚秒级完成、恒退出码 0（能找到输出目录时）；
- --version：输出版本号；
- --prompt "..."：尽力从指令提取输入/输出路径（解析失败则尝试平台标准路径
  /home/user/ws/{input,output}），输出目录恒创建；
- 产出三份最小合法 Markdown（命名与官方要求一致；内容为「待核验」占位的
  诚实空结构，不含任何编造事实）。

本探针唯一目的：判定平台评测管线的「Agent运行错误（未正常启动/非零退出）」
是平台侧问题还是提交包问题。它不用于正式参赛评分。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys

VERSION = "0.4.0"

FILES = ("user_profile.md", "product_list.md", "recommendation.md")

PROFILE_MD = """# 用户画像

| 字段组 | 字段名 | 取值 |
| --- | --- | --- |
| 标识 | 画像编号 | 待核验 |
| 基本情况 | 性别 | 未提供 |
| 基本情况 | 年龄 | 未提供 |
| 购买目标 | 品类 | 待核验 |
| 预算 | 预算上限 | 待核验 |
| 设备 | 系统生态 | 未提供 |
| 场景 | 场景1 | 待核验 |
| 偏好 | 品牌倾向 | 未提供 |
"""

PRODUCTS_MD = """# 产品属性

（探针运行：未做任何模型调用，全部字段以「待核验」诚实占位）

| 字段组 | 字段名 | 取值 |
| --- | --- | --- |
| 基础标识 | 产品编号 | 待核验 |
| 基础标识 | 产品名称 | 待核验 |
| 产品力 | 续航 | 待核验 |
| 市场验证 | 市场热度 | 待核验 |
| 购买与使用成本 | 当前售价 | 待核验 |
"""

RECO_MD = """# 消费推荐报告

## 推荐结果

（探针运行：无法给出有据推荐，如实以「待核验」占位，不做任何编造）

| 推荐层级 | 产品ID | 产品 | 画像匹配与推荐理由 | 注意事项 |
| --- | --- | --- | --- | --- |
| 首选 | 无 | 无 | 待核验（输入资料未能支持有据推荐） | 待核验 |
| 备选1 | 无 | 无 | 当前尚不构成有效备选（探针占位） | 待核验 |
| 备选2 | 无 | 无 | 当前尚不构成有效备选（探针占位） | 待核验 |

综合结论：待核验。

## 未选原因

| 产品ID | 未进入前三的主要原因 | 建议改变条件 |
| --- | --- | --- |
| 待核验 | 待核验 | 待核验 |
"""


def log(msg: str) -> None:
    log_dir = os.environ.get("AGENT_LOG_DIR")
    line = f"[probe] {msg}"
    print(line, file=sys.stderr)
    if log_dir:
        try:
            os.makedirs(log_dir, exist_ok=True)
            with open(os.path.join(log_dir, "agent.log"), "a", encoding="utf-8") as fh:
                fh.write(line + "\n")
        except OSError:
            pass


def find_output_dir(prompt_text: str) -> str:
    """确定性定位输出目录：指令中的绝对路径 → 平台标准路径 → 输入同级 output。"""
    for raw in re.findall(r"/[^\s\"'`，。；：）】、]+", prompt_text or ""):
        cand = raw.rstrip(".,;:、")
        if os.path.isdir(cand) and os.path.isdir(os.path.join(cand, "00_User_Descriptions")):
            parent = os.path.dirname(cand)
            return os.path.join(parent, "output") if parent else "/home/user/ws/output"
    if os.path.isdir("/home/user/ws/input"):
        return "/home/user/ws/output"
    # 都没有：仍要产出（平台可能把资料放在别处/延迟挂载）——用标准路径
    return "/home/user/ws/output"


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="agent.py")
    parser.add_argument("--version", action="store_true")
    parser.add_argument("--prompt", nargs="?", default=None)
    args, unknown = parser.parse_known_args(argv)
    if args.version:
        print(VERSION)
        return 0
    try:
        log(f"start argv={sys.argv!r} cwd={os.getcwd()}")
        prompt_text = " ".join(
            ([args.prompt] if args.prompt else [])
            + [u for u in unknown if not u.startswith("-")]
        ).strip()
        out_dir = find_output_dir(prompt_text)
        os.makedirs(out_dir, exist_ok=True)
        contents = (PROFILE_MD, PRODUCTS_MD, RECO_MD)
        for name, content in zip(FILES, contents):
            with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as fh:
                fh.write(content)
        log(f"done out={out_dir} files={FILES}")
        print("probe finished")
        return 0
    except Exception as exc:  # 探针绝不退出非零（记录后仍产出）
        log(f"error {type(exc).__name__}: {exc}")
        try:
            out_dir = "/home/user/ws/output"
            os.makedirs(out_dir, exist_ok=True)
            for name, content in zip(FILES, (PROFILE_MD, PRODUCTS_MD, RECO_MD)):
                with open(os.path.join(out_dir, name), "w", encoding="utf-8", newline="\n") as fh:
                    fh.write(content)
        except OSError:
            pass
        return 0


if __name__ == "__main__":
    sys.exit(main())
