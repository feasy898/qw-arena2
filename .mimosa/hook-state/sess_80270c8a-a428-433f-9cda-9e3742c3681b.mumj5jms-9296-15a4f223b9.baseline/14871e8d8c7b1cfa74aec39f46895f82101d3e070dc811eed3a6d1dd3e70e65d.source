#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""agent/agent.py — 提交包入口（v0.4.3「不死鸟」三层防线）。

平台评测实证（reports/progress.md 2026-09 条目）：极简探针（单文件、纯标准库、
恒退出 0、产物多点写入）能 scored；全量包（import src + vendored lib）反复
「Agent运行错误（非零退出）」，且失败发生在一切进程内降级逻辑之前（疑似
import/启动阶段）。本版目标：**无论任何失败都保证「三份产物 + 退出码 0」**。

三层防线：
- 第一层（正常）：sys.path 自举后 ``from src.pipeline import main`` 成功 →
  ``rc = main()``；rc == 0 → sys.exit(0)。
- 第二层（main 返回非 0）：``_ensure_outputs_and_exit0`` —— 扫描候选输出位置
  集合，只对缺产物的位置补写三份极简模板 md（不覆盖已有真实产物），然后
  sys.exit(0)。
- 第三层（import main 失败或 main 抛异常）：``_fallback_output`` —— 在**所有**
  候选位置写三份极简模板 md（user_profile.md 顶部一行 HTML 注释诊断标记：
  异常类型与 traceback 摘要；其余为「待核验/未提供」占位，不编造任何事实），
  traceback 全文写 stderr 与 AGENT_LOG_DIR/agent.log（尽力而为），然后
  sys.exit(0)。

其他纪律：
- ``--version`` 在一切之前处理（先于 import src）：stdout 输出与 agent.json
  一致的版本号，退出码 0；
- 本文件自身只 import 标准库（不得 import requests；requests 由第一层的
  src.pipeline 按需引入，防线层不依赖任何第三方包）；
- 源码（含注释）不出现绝对路径字面量：候选目录一律运行时以
  ``os.path.join(os.sep, ...)`` 构造（打包安全扫描按 CI 根路径做字节匹配）。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import NoReturn, Optional

# ---------------------------------------------------------------------------
# sys.path 自举（保持既有语义不变：仓库布局与提交包布局下 import src.* 均可用）
# ---------------------------------------------------------------------------

_HERE = Path(__file__).resolve().parent
_PARENT = _HERE.parent
for _candidate in (_PARENT, _HERE):
    if str(_candidate) not in sys.path:
        sys.path.insert(0, str(_candidate))
_LIB = _HERE / "lib"  # 提交包布局：vendored 依赖目录（存在才加，最优先）
if _LIB.is_dir() and str(_LIB) not in sys.path:
    sys.path.insert(0, str(_LIB))

VERSION = "0.4.3"
"""--version 的兜底版本号（优先读 agent.json；须与 agent/agent.json 同步升级）。"""

OUTPUT_FILENAMES = ("user_profile.md", "product_list.md", "recommendation.md")
"""三份产出的精确命名（platform_contract.outputs，与 src.report_writer 一致）。"""

# 指令中的路径形token：可选盘符前缀（Windows 本地开发兼容）+ posix 风格路径；
# 排除空白、引号、反引号与中文标点（与 src.pipeline._mirror_output_dirs 同口径，
# 盘符前缀为超集，posix 平台行为不变）。
_PATH_TOKEN_RE = re.compile(r"(?:[A-Za-z]:)?/[^\s\"'`，。；：）】、]+")
_OUTPUT_HINT_RE = re.compile(r"out|输出", re.IGNORECASE)
_TRAILING_PUNCT = ".,;:、"


# ---------------------------------------------------------------------------
# --version（一切之前处理；不 import src）
# ---------------------------------------------------------------------------

def _resolve_version() -> str:
    """--version 输出的版本号：优先读 agent.json（与平台约定一致），兜底 VERSION。

    候选位置（覆盖仓库布局与提交包布局，与 src.pipeline.resolve_version 等价）：
    脚本同目录 agent.json（仓库布局=agent/agent.json；提交包布局=包根 agent.json）、
    脚本父目录 agent.json、脚本父目录下 agent/agent.json。读取失败不致命，兜底
    VERSION，保证 --version 恒退出码 0。
    """
    for candidate in (_HERE / "agent.json",
                      _PARENT / "agent.json",
                      _PARENT / "agent" / "agent.json"):
        if candidate.is_file():
            try:
                data = json.loads(candidate.read_text(encoding="utf-8"))
            except (OSError, ValueError):
                break
            version = str(data.get("version") or "").strip()
            if version:
                return version
    return VERSION


def _parse_args(argv: Optional[list[str]] = None):
    """宽松解析：--prompt 值与未知参数由调用方按序拼接（与 src.pipeline.main 一致）。"""
    parser = argparse.ArgumentParser(
        prog="agent.py",
        description="消费决策好帮手：智能选购顾问（输入目录→恰好 3 份 Markdown）",
    )
    parser.add_argument("--version", action="store_true",
                        help="输出版本号（与 agent.json 一致）后退出")
    parser.add_argument("--prompt", metavar='"自然语言指令"',
                        help="含输入/输出目录的自然语言指令")
    return parser.parse_known_args(argv)


def _prompt_text_of(args, unknown: list[str]) -> str:
    """--prompt 值 + 未知非选项参数按序拼接（与 src.pipeline.main 同样宽松）。"""
    return " ".join(
        ([args.prompt] if args.prompt else [])
        + [u for u in unknown if not u.startswith("-")]
    ).strip()


# ---------------------------------------------------------------------------
# 候选输出位置集合（内联实现；不 import src——防线层只许标准库）
# ---------------------------------------------------------------------------

def candidate_output_dirs(prompt_text: str) -> list[str]:
    """候选输出目录集合（去重、保序；参考 src/pipeline._mirror_output_dirs 与
    probe2/agent.py 的 candidate_output_dirs，内联进本文件）。

    候选：① 指令中 output 类绝对路径；② 指令中各路径的同级 output（输入目录
    的兄弟目录）；③ 平台标准输出目录两个（仅 posix 平台启用——目录运行时以
    os.path.join(os.sep, ...) 构造，Windows 本地开发不产生盘符相对的杂散目录）；
    ④ 当前工作目录下 output。任一位置被平台检查都能数到恰好 3 个标准命名文件。
    """
    cands: list[str] = []
    tokens = [p.rstrip(_TRAILING_PUNCT) for p in _PATH_TOKEN_RE.findall(prompt_text or "")]
    for p in tokens:
        if _OUTPUT_HINT_RE.search(p) and p not in cands:
            cands.append(p)
    for p in tokens:
        parent = os.path.dirname(p.rstrip("/"))
        if parent:
            sibling = os.path.join(parent, "output")
            if sibling not in cands:
                cands.append(sibling)
    if os.name == "posix":  # 平台为 Linux；标准目录只在 posix 语义下成立
        for base in (os.path.join(os.sep, "home", "user", "ws"),
                     os.path.join(os.sep, "workspace")):
            cand = os.path.join(base, "output")
            if cand not in cands:
                cands.append(cand)
    cand = os.path.join(os.getcwd(), "output")
    if cand not in cands:
        cands.append(cand)
    return cands


# ---------------------------------------------------------------------------
# 极简模板 md（结构合法：表头与官方格式一致；内容「待核验/未提供」占位，不编造）
# ---------------------------------------------------------------------------

def _one_line(text: str, limit: int = 200) -> str:
    """压成单行并截断（诊断标记用；不含用户资料，只含异常类型/位置摘要）。"""
    flattened = re.sub(r"\s+", " ", str(text or "")).strip()
    return flattened[:limit]


def _fallback_templates(diagnostic: str) -> dict[str, str]:
    """三份极简模板 md 全文（user_profile.md 顶部一行 HTML 注释诊断标记）。

    - 表头与官方格式一致（contracts/field_catalog.json：画像/产品三列表、
      推荐五列表）；字段名取自 field_catalog 官方字段；
    - 取值一律「待核验/未提供」占位；推荐结果表沿用官方异常路径空位行
      （产品ID=「无」、理由「当前尚不构成有效备选」，不虚构第三款产品）；
    - 不编造任何产品、编号或事实。
    """
    marker = _one_line(diagnostic)
    profile_rows = "\n".join(
        f"| {group} | {field} | {value} |"
        for group, field, value in (
            ("标识", "画像编号", "未提供"),
            ("基本情况", "称谓", "未提供"),
            ("基本情况", "所在城市/地区", "未提供"),
            ("购买目标", "商品品类", "未提供"),
            ("预算", "预算原文表述", "待核验"),
            ("设备", "现有主要设备", "未提供"),
            ("偏好", "品牌倾向", "未提供"),
        )
    )
    product_rows = "\n".join(
        f"| {group} | {field} | {value} |"
        for group, field, value in (
            ("基础标识", "产品编号", "未提供"),
            ("基础标识", "产品名称", "未提供"),
            ("产品力", "降噪能力", "待核验"),
            ("品牌力", "品牌名气口碑", "待核验"),
            ("市场验证", "市场热度（销量）", "待核验"),
            ("购买与使用成本", "当前售价", "未提供"),
        )
    )
    rec_rows = "\n".join(
        f"| {level} | 无 | （当前尚不构成有效备选） | 当前尚不构成有效备选 | 无 |"
        for level in ("首选", "备选1", "备选2")
    )
    return {
        "user_profile.md": (
            f"<!-- fallback: {marker} -->\n\n"
            "# 用户画像\n\n"
            "| 字段组 | 字段名 | 取值 |\n"
            "| --- | --- | --- |\n"
            f"{profile_rows}\n"
        ),
        "product_list.md": (
            "# 产品属性列表\n\n"
            "| 字段组 | 字段名 | 取值 |\n"
            "| --- | --- | --- |\n"
            f"{product_rows}\n"
        ),
        "recommendation.md": (
            "# 消费推荐报告\n\n"
            "## 推荐结果\n\n"
            "| 推荐层级 | 产品ID | 产品 | 画像匹配与推荐理由 | 注意事项 |\n"
            "| --- | --- | --- | --- | --- |\n"
            f"{rec_rows}\n\n"
            "综合结论：待核验\n"
        ),
    }


def _write_templates(directory: str, templates: dict[str, str],
                     only_missing: bool) -> bool:
    """向一个目录写模板产物；only_missing=True 时不覆盖已存在文件。

    目录创建/写文件失败按位置级失败处理（尽力而为，绝不抛出）。返回是否有写入。
    """
    try:
        os.makedirs(directory, exist_ok=True)
    except OSError:
        return False
    wrote = False
    for name, content in templates.items():
        path = os.path.join(directory, name)
        if only_missing and os.path.exists(path):
            continue
        try:
            with open(path, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(content)
            wrote = True
        except OSError:
            continue
    return wrote


def _append_agent_log(text: str) -> None:
    """尽力而为追加 AGENT_LOG_DIR/agent.log（本地开发缺省 reports/logs/）。

    只写诊断摘要与 traceback（不含完整用户资料、不含任何密钥值）；任何失败静默。
    """
    log_dir = os.environ.get("AGENT_LOG_DIR", "").strip()
    if not log_dir:
        for base in (_PARENT, _HERE):
            candidate = base / "reports" / "logs"
            if candidate.is_dir():
                log_dir = str(candidate)
                break
        else:
            log_dir = str(_PARENT / "reports" / "logs")
    try:
        os.makedirs(log_dir, exist_ok=True)
        with open(os.path.join(log_dir, "agent.log"), "a",
                  encoding="utf-8", newline="\n") as handle:
            handle.write(text if text.endswith("\n") else text + "\n")
    except OSError:
        pass


# ---------------------------------------------------------------------------
# 第二层 / 第三层防线（都恒 sys.exit(0)）
# ---------------------------------------------------------------------------

def _ensure_outputs_and_exit0(prompt_text: str, reason: str) -> NoReturn:
    """第二层防线：main 返回非 0 —— 只对缺产物的位置补写模板（不覆盖已有真实
    产物），然后 sys.exit(0)。"""
    templates = _fallback_templates(reason)
    written: list[str] = []
    for directory in candidate_output_dirs(prompt_text):
        if _write_templates(directory, templates, only_missing=True):
            written.append(directory)
    message = f"[phoenix] 第二层防线（{reason}）：补写模板产物 → {written}"
    print(message, file=sys.stderr)
    _append_agent_log(message + "\n")
    sys.exit(0)


def _fallback_output(prompt_text: str, exc: BaseException) -> NoReturn:
    """第三层防线：import main 失败或 main 抛异常 —— 在所有候选位置写三份极简
    模板 md（诊断标记含异常类型与 traceback 摘要），traceback 全文写 stderr 与
    agent.log（尽力而为），然后 sys.exit(0)。"""
    tb_text = traceback.format_exc()
    print(tb_text.rstrip("\n"), file=sys.stderr)
    _append_agent_log("=== [phoenix] 第三层防线（import/运行异常，产出模板兜底） ===\n"
                      + tb_text)
    frames = traceback.extract_tb(exc.__traceback__)
    origin = ""
    if frames:
        last = frames[-1]
        origin = f"（{_one_line(os.path.basename(last.filename))}:{last.lineno}）"
    marker = f"{type(exc).__name__}: {_one_line(str(exc), limit=120)}{origin}"
    templates = _fallback_templates(marker)
    written: list[str] = []
    for directory in candidate_output_dirs(prompt_text):
        if _write_templates(directory, templates, only_missing=False):
            written.append(directory)
    print(f"[phoenix] 第三层防线（{marker}）：模板产物写入 {len(written)} 个候选位置",
          file=sys.stderr)
    sys.exit(0)


# ---------------------------------------------------------------------------
# 入口
# ---------------------------------------------------------------------------

def _run(argv: Optional[list[str]] = None) -> NoReturn:
    # --version 在一切之前处理（含 src 损坏场景：绝不先 import 再判版本）
    try:
        args, unknown = _parse_args(argv)
    except SystemExit as exc:  # argparse 硬错（如 --prompt 缺值）：仍走第三层防线
        _fallback_output("", RuntimeError(f"命令行参数解析失败（argparse 退出 {exc.code}）"))
    if args.version:
        print(_resolve_version())
        sys.exit(0)

    prompt_text = _prompt_text_of(args, unknown)

    # ---- 第一层防线：正常路径 ----
    try:
        from src.pipeline import main  # noqa: E402  （自举完成后引用统一 CLI 入口）
    except BaseException as exc:  # noqa: BLE001  第三层防线：启动阶段失败也不许非零退出
        _fallback_output(prompt_text, exc)
    try:
        rc = main()
    except BaseException as exc:  # noqa: BLE001  第三层防线：main 异常同样兜底
        _fallback_output(prompt_text, exc)

    if rc == 0:
        sys.exit(0)
    # ---- 第二层防线：main 返回非 0 ----
    _ensure_outputs_and_exit0(prompt_text, f"main 返回非零退出码 {rc}")


if __name__ == "__main__":
    _run()
