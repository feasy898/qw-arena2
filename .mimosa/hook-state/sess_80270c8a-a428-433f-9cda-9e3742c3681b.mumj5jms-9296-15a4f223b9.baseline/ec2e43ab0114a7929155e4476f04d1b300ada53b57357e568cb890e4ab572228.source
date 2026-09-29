# -*- coding: utf-8 -*-
"""tools/evaluate.py — 本地确定性评估工具（SPEC §12 第一类验收；契约 interfaces.md §16）。

用法：
    python tools/evaluate.py <input_dir> <output_dir> [--run-seconds N]

职责（任务书 + SPEC §10/§12 第一类）：
- 调用 src.validators.validate_outputs 全部确定性校验（E1..E10 十项 + 对象级附属 OBJ_*）；
- 输入侧交叉核对（X1/X2/X3）：从 input_dir 独立重建产品全集，与
  product_list.md 的产品节集合（X2）、recommendation.md 的「3 行推荐实际产品 ∪
  全部未选产品」（即 3+其余 全集）做差集比对；
- 向 stdout 输出恰好一份 JSON 报告（每项检查 pass/fail/skipped + 说明）；
  任何结构检查 fail → 退出码 1，全部通过 → 0；
- 不依赖模型、不依赖网关、不发任何网络请求（仅标准库 + src 确定性模块）。

「独立重建」口径：重建只依据 input_dir（不信任输出文档反推全集）；重建本身复用
src/input_adapter.load_input + src/product_registry.build_registry（SPEC §5 编号归一
规则的唯一确定性实现，contracts/interfaces.md §4），本工具不另造第二套归一逻辑。
X2 的节集合用独立正则直接从 product_list.md 文本提取（不经 renderer/parse_products），
与校验器内部比对路径互为印证；X3 经 renderer.parse_recommendation（确定性）取
推荐行产品ID 与未选行产品ID。有效集 E 的独立重建（审计修复，2026-09）：E 的语义
判定属 constraint_engine（validators 头注），管线决策阶段消费的输入就是渲染冻结后
回读的画像/产品对象；本工具对落盘的前两份文档做同样的真实回读
（renderer.parse_profile/parse_products），用 constraint_engine.constraint_matrix +
valid_product_ids 重建 E 并作为 valid_ids 传入校验——不信任管线内存态、不经任何
模型调用，使「推荐∈E」在评估器侧独立生效（原仅在 pipeline 内部生效）。
E1..E10 的判定与消息全部来自 validators，本工具
只做分组、汇总与退出码映射（high 级 = 失败，medium/low 仅列为提示，不判失败，
与 validators「返回空列表=通过；high=失败」的契约一致）。

检查项清单（报告 checks[].id）：
  E1  产出文件清单（恰好 3 份、命名精确）
  E2  产品全集覆盖（product_list 节集合 = 输入全集，无遗漏/重复/拆并）
  E3  品牌型号原样沿用
  E4  所有产品表结构一致
  E5  推荐结果表（恰 3 行、层级有序、产品∈全集、空位说明）；评估器以
      constraint_engine 从落盘前两份文档独立重建有效集 E 并启用「推荐∈E」校验
      （重建结果同时写入报告顶层 valid_set 字段）
  E6  未选原因覆盖（其余全集逐款覆盖、不重叠、原因/条件句）
  E7  推荐卡风险/核验字段非空
  E8  引用存在且指向前两份文档字段（不越界）
  E9  编码/换行/运行时长（UTF-8 无 BOM、\\n、≤1800s；时长需 --run-seconds 提供）
  E10 Markdown 表格可解析（列数一致、无坏行）
  OBJ 对象级字段口径（validate_outputs 内对象校验的 OBJ_* 码）
  X1  输入侧交叉核对：独立重建产品全集
  X2  输入侧交叉核对：product_list.md 节集合 与 全集差集比对
  X3  输入侧交叉核对：recommendation.md 推荐+未选并集 与 全集差集比对
  OTHER / INTERNAL：未归类问题码兜底 / 评估器自身异常（出现即 fail，不静默）

状态语义：pass=通过；fail=存在 high 级问题（结构失败）；skipped=该项未能执行
（如 product_list.md 缺失导致 X2 无法比对、未提供 --run-seconds 时 E9 的时长子项），
skipped 不判失败但会在报告中如实注明。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Any, Optional, Union

_REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _REPO_ROOT not in sys.path:
    sys.path.insert(0, _REPO_ROOT)

from src import constraint_engine, renderer, validators  # noqa: E402  （确定性模块，无模型依赖）
from src.input_adapter import load_input  # noqa: E402
from src.product_registry import build_registry  # noqa: E402

PathLike = Union[str, os.PathLike]

REPORT_VERSION = "1.0"
TOOL_NAME = "tools/evaluate.py"

# product_list.md 产品节标题（与渲染器模板一致：## P### 产品名称）；独立于 parse 路径提取
_SECTION_HEADING_RE = re.compile(r"^## (P\d{3})\s", re.M)

CHECK_ITEMS: list[tuple[str, str]] = [
    ("E1", "产出文件清单（恰好 3 份、命名精确）"),
    ("E2", "产品全集覆盖（product_list 节集合 = 输入全集，无遗漏/重复/拆并）"),
    ("E3", "品牌型号原样沿用"),
    ("E4", "所有产品表结构一致"),
    ("E5", "推荐结果表（恰 3 行、层级有序、产品∈全集、空位说明）"),
    ("E6", "未选原因覆盖（其余全集逐款覆盖、不重叠、原因与条件句）"),
    ("E7", "推荐卡风险/核验字段非空"),
    ("E8", "引用存在且指向前两份文档字段（不越界）"),
    ("E9", "编码/换行/运行时长（UTF-8 无 BOM、\\n、≤1800s）"),
    ("E10", "Markdown 表格可解析（列数一致、无坏行）"),
    ("OBJ", "对象级字段口径（画像/产品对象 key、枚举、类型；OBJ_* 码）"),
    ("X1", "输入侧交叉核对：独立重建产品全集"),
    ("X2", "输入侧交叉核对：product_list.md 节集合 与 全集差集比对"),
    ("X3", "输入侧交叉核对：recommendation.md 推荐+未选并集 与 全集差集比对"),
]


def _group_of(code: str) -> str:
    """问题码 → 检查项 id（E1..E10 / OBJ / PLAN→OBJ 同组 / 其余 OTHER）。"""
    head = code.split("_", 1)[0]
    if re.fullmatch(r"E\d+", head):
        return head
    if head in {"OBJ", "PLAN"}:
        return "OBJ"
    return "OTHER"


def _new_check(check_id: str, name: str) -> dict[str, Any]:
    return {"id": check_id, "name": name, "status": "pass", "issues": [], "summary": ""}


def _read_text_or_none(path: str) -> Optional[str]:
    """读文本（容忍 BOM）；不存在/不可读/非 UTF-8 → None（编码问题由 E9 报告）。"""
    try:
        with open(path, "rb") as handle:
            raw = handle.read()
    except OSError:
        return None
    try:
        return raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return None


def _set_issue_summary(check: dict[str, Any]) -> None:
    highs = [i for i in check["issues"] if i["severity"] == "high"]
    minors = [i for i in check["issues"] if i["severity"] != "high"]
    if highs:
        check["status"] = "fail"
        check["summary"] = f"失败：{len(highs)} 条 high 级问题；首条：{highs[0]['message']}"
    elif minors:
        check["summary"] = f"通过（附 {len(minors)} 条 medium/low 提示；详见 issues）"
    else:
        check["summary"] = "通过"


def _rebuild_valid_set(output_fs: str) -> tuple[Optional[set[str]], str]:
    """从落盘前两份文档独立重建有效集 E（constraint_engine，无模型调用）。

    口径（审计修复，2026-09）：管线决策阶段消费的输入是渲染冻结后回读的
    画像/产品对象；本工具对落盘的 user_profile.md / product_list.md 做同样的
    真实回读（renderer.parse_profile/parse_products），用
    constraint_engine.constraint_matrix + valid_product_ids 重建 E——与管线
    「相同输入」同口径、但不信任管线内存态，使「推荐∈E」（E5）在评估器侧
    独立生效。

    :return: (E 或 None, 说明文字)。文档缺失/不可读/解析失败 → (None, 原因)
        （结构问题由 E1/E9/E10 记录，此处不重复判失败、不伪造 E）。
    """
    profile_md = _read_text_or_none(os.path.join(output_fs, renderer.PROFILE_FILENAME))
    products_md = _read_text_or_none(os.path.join(output_fs, renderer.PRODUCTS_FILENAME))
    if profile_md is None or products_md is None:
        missing = [name for name, text in (
            (renderer.PROFILE_FILENAME, profile_md),
            (renderer.PRODUCTS_FILENAME, products_md)) if text is None]
        return None, f"前两份文档缺失或不可读（{'、'.join(missing)}），无法重建 E"
    try:
        profile_obj = renderer.parse_profile(profile_md)
        products_obj = renderer.parse_products(products_md)
    except ValueError as exc:
        return None, f"前两份文档解析失败（见 E10）：{exc}"
    matrix = constraint_engine.constraint_matrix(profile_obj, products_obj)
    valid = constraint_engine.valid_product_ids(matrix)
    note = (f"以 constraint_engine 独立重建 E={sorted(valid)}"
            f"（输入=落盘前两份文档回读；需求×产品判定数={len(matrix)}）")
    return valid, note


def run_checks(
    input_dir: PathLike,
    output_dir: PathLike,
    *,
    run_seconds: Optional[float] = None,
) -> dict[str, Any]:
    """执行全部检查，返回 JSON 报告 dict（不打印、不写盘）。

    :param input_dir: 输入目录（重建产品全集 / validators E2、E3 需要）
    :param output_dir: 待评估输出目录
    :param run_seconds: 可选，被评估运行的实际耗时（秒）；>1800 由 validators 记 high
    :return: 报告 dict（含 checks / failed_checks / passed / exit_code）
    """
    checks: dict[str, dict[str, Any]] = {cid: _new_check(cid, name) for cid, name in CHECK_ITEMS}

    # ---- 有效集 E 独立重建（constraint_engine，供 E5「推荐∈E」校验） --------
    valid_set: Optional[set[str]] = None
    valid_set_note: str
    try:
        valid_set, valid_set_note = _rebuild_valid_set(os.fspath(output_dir))
    except Exception as exc:  # 重建自身异常不静默：E5 校验退化为「∈全集」，但如实注明
        valid_set, valid_set_note = None, f"有效集重建异常（{type(exc).__name__}: {exc}）"

    # ---- validators 全部检查（E1..E10 + 对象级附属） ----------------------
    issues = validators.validate_outputs(
        output_dir, input_dir, valid_ids=valid_set, run_seconds=run_seconds
    )
    for issue in issues:
        target = checks.get(_group_of(issue.code))
        if target is None:  # 未预期码：归入 OTHER 兜底，绝不静默丢弃
            target = checks.setdefault("OTHER", _new_check("OTHER", "未归类问题码兜底"))
        target["issues"].append(
            {"code": issue.code, "severity": issue.severity, "message": issue.message}
        )
    for check in checks.values():
        _set_issue_summary(check)

    # E5 汇总补记 E 重建口径（不改变 pass/fail 判定本身，判定来自 validators E5）
    checks["E5"]["summary"] = f"{checks['E5']['summary']}；有效集校验：{valid_set_note}"

    # ---- X1 输入侧独立重建产品全集 ----------------------------------------
    universe: Optional[list[str]] = None
    x1 = checks["X1"]
    try:
        bundle = load_input(input_dir)
        registry = build_registry(bundle.source_records)
        universe = registry.ids()
        if universe:
            x1["summary"] = f"从 input_dir 重建产品全集 {len(universe)} 款：{'、'.join(universe)}"
        else:
            x1["status"] = "fail"
            message = "输入目录可装载但重建产品全集为空（官网/货架/报道/实测均未提供合法产品编号）"
            x1["issues"].append({"code": "X1_REBUILD", "severity": "high", "message": message})
            x1["summary"] = f"失败：{message}"
    except Exception as exc:  # InputError 等：评估器必须产出结论而非崩溃
        x1["status"] = "fail"
        message = f"输入目录不可装载，无法重建产品全集：{type(exc).__name__}: {exc}"
        x1["issues"].append({"code": "X1_REBUILD", "severity": "high", "message": message})
        x1["summary"] = f"失败：{message}"

    output_fs = os.fspath(output_dir)
    universe_set = set(universe) if universe is not None else None

    # ---- X2 product_list.md 节集合 与 全集差集比对 -------------------------
    x2 = checks["X2"]
    if universe_set is None:
        x2["status"] = "skipped"
        x2["summary"] = "前置 X1 失败，无法比对"
    else:
        products_md = _read_text_or_none(os.path.join(output_fs, renderer.PRODUCTS_FILENAME))
        if products_md is None:
            x2["status"] = "skipped"
            x2["summary"] = "product_list.md 缺失或不可读（缺失/编码问题见 E1/E9），无法提取节集合"
        else:
            section_set = set(_SECTION_HEADING_RE.findall(products_md))
            missing = sorted(universe_set - section_set)
            extra = sorted(section_set - universe_set)
            if missing or extra:
                x2["status"] = "fail"
                parts = []
                if missing:
                    parts.append(f"product_list.md 缺少产品节：{missing}")
                if extra:
                    parts.append(f"product_list.md 出现输入全集外产品节：{extra}")
                summary = "差集非空；".join(parts)
                x2["issues"].append({
                    "code": "X2_SET_DIFF", "severity": "high",
                    "message": f"product_list.md 节集合 {sorted(section_set)} ≠ 输入全集 {sorted(universe_set)}"
                               f"（缺少：{missing or '无'}；多余：{extra or '无'}）",
                })
                x2["summary"] = f"失败：{summary}"
            else:
                x2["summary"] = f"product_list.md 节集合 = 输入全集（{len(section_set)} 款，无差集）"

    # ---- X3 recommendation.md（3 行推荐实际产品 ∪ 全部未选） 与 全集差集比对 --
    x3 = checks["X3"]
    if universe_set is None:
        x3["status"] = "skipped"
        x3["summary"] = "前置 X1 失败，无法比对"
    else:
        rec_md = _read_text_or_none(os.path.join(output_fs, renderer.RECOMMENDATION_FILENAME))
        if rec_md is None:
            x3["status"] = "skipped"
            x3["summary"] = "recommendation.md 缺失或不可读（缺失/编码问题见 E1/E9），无法提取全集"
        else:
            try:
                rec = renderer.parse_recommendation(rec_md)
            except ValueError as exc:
                x3["status"] = "skipped"
                x3["summary"] = f"recommendation.md 无法解析（结构问题见 E10）：{exc}"
            else:
                recommended = [row["product_id"] for row in rec["recommendations"] if row["product_id"]]
                excluded = [pid for row in rec["exclusions"] for pid in row["product_ids"]]
                union = set(recommended) | set(excluded)
                missing = sorted(universe_set - union)
                extra = sorted(union - universe_set)
                if missing or extra:
                    x3["status"] = "fail"
                    parts = [f"推荐 {len(recommended)} 款 + 未选 {len(excluded)} 款："]
                    if missing:
                        parts.append(f"推荐+未选未覆盖全集，缺少：{missing}")
                    if extra:
                        parts.append(f"推荐+未选出现全集外产品：{extra}")
                    x3["issues"].append({
                        "code": "X3_SET_DIFF", "severity": "high",
                        "message": f"recommendation.md 推荐+未选并集 {sorted(union)} ≠ 输入全集 "
                                   f"{sorted(universe_set)}（缺少：{missing or '无'}；多余：{extra or '无'}）",
                    })
                    x3["summary"] = f"失败：{' '.join(parts)}"
                else:
                    x3["summary"] = (
                        f"推荐 {len(recommended)} 款 + 未选 {len(excluded)} 款 = 输入全集 "
                        f"{len(universe_set)} 款（3+其余 全集一致，无差集）"
                    )

    # ---- 汇总报告 ----------------------------------------------------------
    ordered = [checks[cid] for cid, _name in CHECK_ITEMS]
    if "OTHER" in checks:
        ordered.append(checks["OTHER"])
    failed = [c["id"] for c in ordered if c["status"] == "fail"]
    skipped = [c["id"] for c in ordered if c["status"] == "skipped"]
    severity_count = {"high": 0, "medium": 0, "low": 0}
    for check in ordered:
        for issue in check["issues"]:
            severity_count[issue["severity"]] = severity_count.get(issue["severity"], 0) + 1
    return {
        "tool": TOOL_NAME,
        "report_version": REPORT_VERSION,
        "input_dir": os.fspath(input_dir),
        "output_dir": output_fs,
        "run_seconds": run_seconds,
        "product_universe": universe,
        "valid_set": sorted(valid_set) if valid_set is not None else None,
        "issue_count": severity_count,
        "checks": ordered,
        "failed_checks": failed,
        "skipped_checks": skipped,
        "passed": not failed,
        "exit_code": 1 if failed else 0,
    }


def _print_json(payload: dict[str, Any]) -> None:
    """stdout 恰好输出一份 JSON；当前流编码无法承载中文时降级 ensure_ascii。"""
    try:
        print(json.dumps(payload, ensure_ascii=False, indent=2))
    except UnicodeEncodeError:
        print(json.dumps(payload, ensure_ascii=True, indent=2))


def evaluate(input_dir: PathLike, output_dir: PathLike, *, run_seconds: Optional[float] = None) -> int:
    """契约入口（contracts/interfaces.md §16）：评估输出目录，返回退出码。

    stdout 输出 JSON 报告（每项检查 pass/fail/skipped + 说明）；
    任何结构检查失败 → 1；全部通过 → 0。不依赖模型、不依赖网关。
    评估器自身异常同样产出 fail 报告并以 1 退出（不静默、不伪造通过）。
    """
    try:
        report = run_checks(input_dir, output_dir, run_seconds=run_seconds)
    except Exception as exc:  # 评估器自身故障必须显式暴露
        report = {
            "tool": TOOL_NAME,
            "report_version": REPORT_VERSION,
            "input_dir": os.fspath(input_dir),
            "output_dir": os.fspath(output_dir),
            "run_seconds": run_seconds,
            "product_universe": None,
            "valid_set": None,
            "issue_count": {"high": 1, "medium": 0, "low": 0},
            "checks": [{
                "id": "INTERNAL", "name": "评估器内部错误", "status": "fail",
                "issues": [{"code": "INTERNAL_ERROR", "severity": "high",
                            "message": f"{type(exc).__name__}: {exc}"}],
                "summary": "评估器未正常完成，结果不可信",
            }],
            "failed_checks": ["INTERNAL"],
            "skipped_checks": [],
            "passed": False,
            "exit_code": 1,
        }
    _print_json(report)
    return report["exit_code"]


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/evaluate.py",
        description="本地确定性评估工具（SPEC §12 第一类：validators 十项 + 输入侧交叉核对）",
    )
    parser.add_argument("input_dir", help="输入目录（与被评估运行一致，用于重建产品全集）")
    parser.add_argument("output_dir", help="待评估的输出目录（恰含 3 份 Markdown）")
    parser.add_argument(
        "--run-seconds", type=float, default=None,
        help="被评估运行的实际耗时（秒）；>1800 记 high（硬截止）。缺省则跳过时长子项",
    )
    args = parser.parse_args(argv)
    return evaluate(args.input_dir, args.output_dir, run_seconds=args.run_seconds)


if __name__ == "__main__":
    sys.exit(main())
