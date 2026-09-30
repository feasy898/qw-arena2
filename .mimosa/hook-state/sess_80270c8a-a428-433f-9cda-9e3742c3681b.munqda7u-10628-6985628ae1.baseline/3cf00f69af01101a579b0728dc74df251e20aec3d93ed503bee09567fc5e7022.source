# -*- coding: utf-8 -*-
"""tools/local_eval.py — 本地量化评估器（Schema+Grounding 架构验收；BUILD_TASK）。

对示例用户（``--user all`` 缺省全跑 6 个）各跑一次完整 agent（``src.pipeline.run``，
mock 关闭）：环境/.secrets 里有真实 Key 则走真实模型链（grounded 主路径），否则走
确定性正则链（网关构造失败→管线既有降级链），报告逐用户标注实际使用的链路。

量化指标（JSON 报告 + stdout 摘要）：
- **字段覆盖率**：画像（7 字段组）与产品（5 字段组）非空比例（渲染冻结→parse 回读
  口径，缺失表达「未提供/待核验/优先级未明确」及其带说明形态计为缺失）；
- **grounding 通过率**：写入字段中 exact_quote 能逐字回溯来源文本的比例（目标 100%，
  程序保证）。真实链按 ``src.schema_extractor.LAST_GROUNDING_MANIFEST`` 的模型字段
  quote 清单精确计量；正则链无模型 quote 清单，报告标注「程序构造保证（逐字回溯）」
  并以观察级 evidence 引文复核（deterministic 抽取器逐条携带逐字引文）；
- **幻觉率**：写入内容字段中「值与逐字锚点在原文均找不到依据」的比例（目标 0）。
  判定口径：值逐字命中原文，或其逐字锚点（模型 quote / evidence 引文）逐字命中；
  判定/派生类字段（预算口径、优先级、编号等程序口径枚举）豁免并计数。未命中项
  逐条列入 suspects 供人工复核（渲染合成值可能误报，报告如实标注为启发式）；
- **结构校验**：复用 tools/evaluate.py 的 run_checks 全部确定性检查。

用法：
    python tools/local_eval.py                 # 6 用户全跑，真实链（若可）否则正则链
    python tools/local_eval.py --user 1,3      # 指定用户
    python tools/local_eval.py --output-root reports/local_eval/manual

只依赖标准库 + src/tools 确定性模块；密钥只从环境变量/.secrets 读取，不打印、
不写入报告。
"""
from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

_REPO_ROOT = Path(__file__).resolve().parents[1]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from src import schema_extractor  # noqa: E402
from src.deterministic_extractor import deterministic_product_draft  # noqa: E402
from src.input_adapter import load_input  # noqa: E402
from src.pipeline import resolve_config, run  # noqa: E402
from src.product_registry import build_registry  # noqa: E402
from src.renderer import parse_products, parse_profile  # noqa: E402
from src.source_index import SourceIndex  # noqa: E402

DATA_POOL = _REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
DEFAULT_OUTPUT_ROOT = _REPO_ROOT / "reports" / "local_eval"
SECRETS_DIR = _REPO_ROOT / ".secrets"
KEY_FILE = SECRETS_DIR / "dashscope_api_key.txt"
BASE_URL_FILE = SECRETS_DIR / "base_url.txt"
SOURCE_DIRS = (
    "01_Ecommerce_Listings",
    "02_Media_Coverage_and_Reviews",
    "03_User_Feedback_and_Complaints",
    "04_Brand_Official_Sites",
)
REPORT_VERSION = "1.0"
TOOL_NAME = "tools/local_eval.py"

DEGRADE_MARKER = "<!-- degrade:"
_MISSING_MARKS = ("未提供", "待核验", "优先级未明确")
# 判定/派生类字段：程序口径枚举/编号，无原文逐字形态，幻觉检查豁免（逐组计数）
PROFILE_DERIVED_FIELDS = {"profile_id", "budget_semantics", "currency"}
SCENE_DERIVED_FIELDS = {"scene_priority", "priority_basis"}
PRODUCT_DERIVED_FIELDS = {"canonical_id", "aliases"}
# 程序口径注记（非原文摘抄）的豁免：续航条件注记等
PRODUCT_NOTE_EXEMPT = {"conditions"}


# ---------------------------------------------------------------------------
# 链路判定与凭据（值不打印、不落报告）
# ---------------------------------------------------------------------------

def _read_secret(path: Path, env_value: str) -> str:
    value = (env_value or "").strip()
    if value:
        return value
    if path.is_file():
        return path.read_text(encoding="utf-8").strip()
    return ""


# 正则链强制构造（与 tests/unit/test_degrade_decision.py 同口径）：真实模式 +
# 不可达网关 → 管线既有确定性降级链接管。注意：仅清空 Key 会让网关按
# is_mock_mode 判为 mock（缺 Key 即 mock），不会走正则链。
REGEX_CHAIN_ENV = {
    "DASHSCOPE_API_KEY": "sk-local-eval-invalid",
    "OPENAI_BASE_URL": "https://10.255.255.1.invalid/v1",
}


def resolve_chain(force: str = "auto") -> tuple[str, str]:
    """(链路名, 说明)。real=真实模型链；regex=确定性正则链；mock=强制夹具。

    :param force: auto=按环境/.secrets 判定；real/regex=强制指定（regex 用于
        无网关环境的确定性验证，注入不可达网关构造）。
    """
    if force == "regex":
        os.environ.update(REGEX_CHAIN_ENV)
        return "regex", ("--chain regex 强制：不可达网关构造（正则链）→ 管线既有"
                         "确定性降级链接管")
    if (os.environ.get("QW_FORCE_MOCK") or "").strip():
        return "mock", "QW_FORCE_MOCK=1：mock 夹具链"
    key = _read_secret(KEY_FILE, os.environ.get("DASHSCOPE_API_KEY", ""))
    base = _read_secret(BASE_URL_FILE, os.environ.get("OPENAI_BASE_URL", ""))
    if key and key != "mock":
        # 真实链凭据经环境变量注入网关（值不打印、不写入任何报告）
        os.environ["DASHSCOPE_API_KEY"] = key
        if base:
            os.environ["OPENAI_BASE_URL"] = base
        note = f"真实模型链（Key 长度 {len(key)}，base_url {'已设' if base else '缺失→网关回退默认地址'}）"
        return "real", note
    # 无任何凭据：与 regex 同口径注入不可达构造（缺 Key 会被网关判为 mock，
    # 那不是正则链），保证「网关不可达→正则链」语义成立
    os.environ.update(REGEX_CHAIN_ENV)
    return "regex", "DASHSCOPE_API_KEY 缺失：按不可达网关构造→管线既有确定性正则降级链"


# ---------------------------------------------------------------------------
# 输入构造
# ---------------------------------------------------------------------------

def discover_users(input_pool: Path) -> list[str]:
    desc_dir = input_pool / "00_User_Descriptions"
    files = sorted(p.name for p in desc_dir.glob("User_Description_*.txt"))
    if not files:
        raise SystemExit(f"[local-eval] 未找到用户描述：{desc_dir}")
    return files


def build_single_user_input(staging: Path, user_file: str) -> Path:
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    desc_dir = staging / "00_User_Descriptions"
    desc_dir.mkdir()
    shutil.copy2(DATA_POOL / "00_User_Descriptions" / user_file, desc_dir / user_file)
    for dirname in SOURCE_DIRS:
        shutil.copytree(DATA_POOL / dirname, staging / dirname)
    return staging


# ---------------------------------------------------------------------------
# 覆盖率
# ---------------------------------------------------------------------------

def _is_missing(value: Any) -> bool:
    if value is None:
        return True
    if isinstance(value, (list, dict)):
        return len(value) == 0
    if isinstance(value, str):
        text = value.strip()
        return (not text) or text.startswith(_MISSING_MARKS)
    return False


def _catalog() -> dict:
    path = _REPO_ROOT / "contracts" / "field_catalog.json"
    return json.loads(path.read_text(encoding="utf-8"))


def profile_coverage(parsed: Optional[dict], catalog: dict) -> dict:
    """画像分组覆盖率：组内（子）字段非缺失比例；scenes 按 item_fields 逐场景展开。"""
    groups: dict[str, dict] = {}
    for group in catalog["profile"]["groups"]:
        covered = total = 0
        for field_def in group["fields"]:
            key = field_def["key"]
            if field_def.get("item_fields"):
                scenes = (parsed or {}).get(key) or []
                scene_list = scenes if isinstance(scenes, list) else []
                if not scene_list:
                    total += len(field_def["item_fields"])
                    continue
                for scene in scene_list:
                    for item_def in field_def["item_fields"]:
                        total += 1
                        if not _is_missing((scene or {}).get(item_def["key"])):
                            covered += 1
                continue
            total += 1
            if not _is_missing((parsed or {}).get(key)):
                covered += 1
        groups[group["name"]] = {
            "covered": covered, "total": total,
            "rate": round(covered / total, 4) if total else None,
        }
    all_covered = sum(g["covered"] for g in groups.values())
    all_total = sum(g["total"] for g in groups.values())
    return {"groups": groups,
            "overall": {"covered": all_covered, "total": all_total,
                        "rate": round(all_covered / all_total, 4) if all_total else None}}


def product_coverage(parsed: Optional[dict], catalog: dict) -> dict:
    """产品分组覆盖率：逐款逐组非缺失比例，组间取平均；overall 为全部字段汇总。"""
    groups: dict[str, dict] = {}
    all_covered = all_total = 0
    products = parsed or {}
    for group in catalog["product"]["groups"]:
        g_covered = g_total = 0
        for field_def in group["fields"]:
            key = field_def["key"]
            if field_def.get("item_fields"):
                for obj in products.values():
                    items = (obj or {}).get(key) or []
                    g_total += len(field_def["item_fields"])
                    if isinstance(items, list):
                        for item in items:
                            for item_def in field_def["item_fields"]:
                                if not _is_missing((item or {}).get(item_def["key"])):
                                    g_covered += 1
                continue
            for obj in products.values():
                g_total += 1
                if not _is_missing((obj or {}).get(key)):
                    g_covered += 1
        groups[group["name"]] = {
            "covered": g_covered, "total": g_total,
            "rate": round(g_covered / g_total, 4) if g_total else None,
        }
        all_covered += g_covered
        all_total += g_total
    return {"groups": groups,
            "overall": {"covered": all_covered, "total": all_total,
                        "rate": round(all_covered / all_total, 4) if all_total else None}}


# ---------------------------------------------------------------------------
# grounding 与幻觉（锚点精确子串判定）
# ---------------------------------------------------------------------------

_PAREN_NOTE_RE = re.compile(r"[（(][^（）()]*[)）]")
# 程序口径占位值（field_catalog value_rule 认可的形态，非原文摘抄）
PRODUCT_PLACEHOLDER_VALUES = {"未区分模式"}


def _segment_verbatim(value: Any, source_text: str) -> Optional[bool]:
    """启发式值级回溯：标量整体逐字；合并值按「；」分段逐字；数字按字符串逐字。

    口径注记剥离：段内「（…）」程序注记（渠道口径/并列说明等）剔除后再比对
    （注记是程序生成的口径说明，非原文摘抄；该剥离仅用于本评估启发式，
    抽取链路的 exact_quote 校验仍是精确子串、不做任何剥离）。

    :return: True/False；None=无可判定内容（空/豁免由调用方处理）
    """
    if value is None or isinstance(value, (dict, list)):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text in PRODUCT_PLACEHOLDER_VALUES:
        return True
    segments = [seg.strip() for seg in text.split("；") if seg.strip()]
    if not segments:
        return None
    for seg in segments:
        if seg in source_text:
            continue
        core = _PAREN_NOTE_RE.sub("", seg).strip()
        if core and core in source_text:
            continue
        return False
    return True


def grounding_metrics(manifest: dict, user_text: str, product_texts: dict,
                      draft_anchors: Optional[dict] = None) -> dict:
    """grounding 通过率：写入字段中 exact_quote 能逐字回溯来源的比例。

    真实链：按 schema_extractor.LAST_GROUNDING_MANIFEST 的模型字段 quote 清单
    精确复核；正则链：无模型 quote 清单，用确定性抽取草稿的观察级 evidence
    引文复核产品侧（画像侧无锚点清单，如实计 0 条并在 note 说明）。
    """
    checked = passed = 0
    failures: list[str] = []
    profile_entries = manifest.get("profile") or {}
    for field, entry in profile_entries.items():
        quote = (entry or {}).get("exact_quote") if isinstance(entry, dict) else None
        if not isinstance(quote, str) or not quote:
            continue
        checked += 1
        if quote in user_text:
            passed += 1
        else:
            failures.append(f"画像.{field}")
    for cid, fields in (manifest.get("products") or {}).items():
        source_text = product_texts.get(cid, "")
        for field, quotes in (fields or {}).items():
            if not isinstance(quotes, list):
                continue
            for quote in quotes:
                if not isinstance(quote, str) or not quote:
                    continue
                checked += 1
                if quote in source_text:
                    passed += 1
                else:
                    failures.append(f"{cid}.{field}")
    if not checked and draft_anchors:
        for cid, field_quotes in draft_anchors.items():
            source_text = product_texts.get(cid, "")
            for field, quotes in (field_quotes or {}).items():
                for quote in quotes:
                    checked += 1
                    if quote in source_text:
                        passed += 1
                    else:
                        failures.append(f"{cid}.{field}")
    return {"checked": checked, "passed": passed,
            "rate": round(passed / checked, 4) if checked else None,
            "failures": failures[:20]}


def _draft_anchor_quotes(records_by_cid: dict, index) -> dict[str, dict[str, list[str]]]:
    """正则链锚点：deterministic 抽取草稿各字段的 evidence 逐字引文（逐产品取记录）。"""
    anchors: dict[str, dict[str, list[str]]] = {}
    for cid, record in records_by_cid.items():
        records = sorted(index.by_product(cid),
                         key=lambda r: (str(r.source_type), r.source_id))
        draft = deterministic_product_draft(record, records)
        field_quotes: dict[str, list[str]] = {}
        for field_key, observations in draft.items():
            if not isinstance(observations, list):
                continue
            quotes = []
            for obs in observations:
                if isinstance(obs, dict):
                    for ref in obs.get("evidence_refs") or []:
                        if isinstance(ref, dict) and ref.get("exact_quote"):
                            quotes.append(str(ref["exact_quote"]))
            if quotes:
                field_quotes[field_key] = quotes
        anchors[cid] = field_quotes
    return anchors


def hallucination_metrics(parsed_profile: Optional[dict], parsed_products: Optional[dict],
                          manifest: dict, user_text: str, product_texts: dict,
                          draft_anchors: dict,
                          registered_identity: Optional[dict] = None) -> dict:
    """幻觉疑似项：内容字段「值与锚点均无法逐字回溯原文」的比例（启发式，附清单）。

    registered_identity：{cid: {registry 权威身份值}}（编号归一的 model_raw/brand_raw
    等，如官网文件名提取的「RalunPro」）——程序权威原样值，豁免值级比对。
    """
    suspects: list[dict] = []
    written = exempt = 0
    profile_manifest = manifest.get("profile") or {}

    def _check(label: str, value: Any, source_text: str, anchor_ok: bool) -> None:
        nonlocal written, exempt
        if _is_missing(value):
            return
        if isinstance(value, (dict, list)) and not value:
            return
        written += 1
        if anchor_ok:
            return
        value_ok = _segment_verbatim(value, source_text)
        if value_ok is True:
            return
        if value_ok is None:
            exempt += 1  # 结构化/空内容：无字面可查（豁免计数，不计疑似）
            return
        suspects.append({"field": label, "value": str(value)[:60]})

    for key, value in (parsed_profile or {}).items():
        if key == "scenes":
            scenes = value if isinstance(value, list) else []
            for index, scene in enumerate(scenes, 1):
                for child_key, child_value in (scene or {}).items():
                    if child_key in SCENE_DERIVED_FIELDS:
                        if not _is_missing(child_value):
                            written += 1
                            exempt += 1
                        continue
                    anchor_ok = False
                    _check(f"scenes[{index}].{child_key}", child_value,
                           user_text, anchor_ok)
            continue
        if key in PROFILE_DERIVED_FIELDS:
            if not _is_missing(value):
                written += 1
                exempt += 1
            continue
        entry = profile_manifest.get(key)
        quote = (entry or {}).get("exact_quote") if isinstance(entry, dict) else None
        anchor_ok = bool(isinstance(quote, str) and quote and quote in user_text)
        _check(f"画像.{key}", value, user_text, anchor_ok)

    for cid, obj in (parsed_products or {}).items():
        source_text = product_texts.get(cid, "")
        field_quotes = (manifest.get("products") or {}).get(cid)
        if not field_quotes:
            field_quotes = draft_anchors.get(cid) or {}
        registered = (registered_identity or {}).get(cid) or set()
        for key, value in (obj or {}).items():
            if key in PRODUCT_DERIVED_FIELDS:
                if not _is_missing(value):
                    written += 1
                    exempt += 1
                continue
            if key in ("product_name", "brand", "model") \
                    and str(value) in registered:
                # registry 权威身份值（编号归一/官网文件名提取，程序原样沿用）
                written += 1
                exempt += 1
                continue
            quotes = field_quotes.get(key) if isinstance(field_quotes, dict) else None
            anchor_ok = bool(quotes) and all(
                isinstance(q, str) and q and q in source_text for q in quotes)
            if isinstance(value, list) and value and isinstance(value[0], dict):
                # 结构化条目（续航分模式）：mode/duration 逐字核查，conditions 为程序口径注记豁免
                for index, item in enumerate(value, 1):
                    for item_key, item_value in (item or {}).items():
                        if item_key in PRODUCT_NOTE_EXEMPT:
                            if not _is_missing(item_value):
                                written += 1
                                exempt += 1
                            continue
                        _check(f"{cid}.{key}[{index}].{item_key}", item_value,
                               source_text, anchor_ok)
                continue
            _check(f"{cid}.{key}", value, source_text, anchor_ok)

    rate = round(len(suspects) / written, 4) if written else None
    return {"written": written, "exempt_derived": exempt,
            "suspects": suspects, "suspect_rate": rate,
            "note": "启发式：值或其逐字锚点命中原文即视为有依据；渲染合成值可能误报，"
                    "suspects 供人工复核"}


# ---------------------------------------------------------------------------
# 主流程
# ---------------------------------------------------------------------------

def evaluate_user(user_file: str, output_root: Path, chain: str) -> dict:
    staging = build_single_user_input(output_root / "_input" / user_file[:-4],
                                      user_file)
    output_dir = output_root / user_file[:-4]
    if output_dir.exists():
        shutil.rmtree(output_dir)
    catalog = _catalog()
    bundle_in = load_input(staging)
    index = SourceIndex(bundle_in.source_records)
    registry_records = {r.canonical_id: r for r in
                        build_registry(bundle_in.source_records).all()}
    registered_identity = {
        cid: {str(getattr(rec, attr)) for attr in ("model_raw", "brand_raw")
              if getattr(rec, attr)}
        for cid, rec in registry_records.items()}
    product_texts = {
        cid: "\n".join(str(r.original_text) for r in
                       sorted(index.by_product(cid),
                              key=lambda r: (str(r.source_type), r.source_id)))
        for cid in registry_records}
    draft_anchors = _draft_anchor_quotes(registry_records, index)

    config = resolve_config()
    mock_cfg = dict(config.get("mock") or {})
    mock_cfg["enabled"] = False  # 评估器自身关闭夹具 mock：真实链（若可）否则正则链
    config["mock"] = mock_cfg
    schema_extractor.LAST_GROUNDING_MANIFEST.clear()
    started = time.monotonic()
    error: Optional[str] = None
    try:
        run(staging, output_dir, config)
    except Exception as exc:  # 降级链修复环耗尽等：产物已落盘，如实记录后继续计量
        error = f"{type(exc).__name__}: {exc}"
    elapsed = time.monotonic() - started
    manifest = json.loads(json.dumps(schema_extractor.LAST_GROUNDING_MANIFEST))
    schema_extractor.LAST_GROUNDING_MANIFEST.clear()
    profile_md_path = output_dir / "user_profile.md"
    profile_md = profile_md_path.read_text(encoding="utf-8-sig") \
        if profile_md_path.is_file() else ""
    degraded = profile_md.startswith(DEGRADE_MARKER)
    used_chain = chain
    if chain == "real" and degraded:
        used_chain = "regex(运行中降级)"

    parsed_profile = parsed_products = None
    parse_error: Optional[str] = None
    try:
        parsed_profile = parse_profile(profile_md) if profile_md else None
        products_md_path = output_dir / "product_list.md"
        products_md = products_md_path.read_text(encoding="utf-8-sig") \
            if products_md_path.is_file() else ""
        parsed_products = parse_products(products_md) if products_md else None
    except ValueError as exc:
        parse_error = str(exc)[:200]

    from tools.evaluate import run_checks
    structure = run_checks(staging, output_dir, run_seconds=elapsed)

    report: dict[str, Any] = {
        "user": user_file,
        "chain": used_chain,
        "elapsed_seconds": round(elapsed, 2),
        "run_error": error,
        "degraded": degraded,
        "parse_error": parse_error,
        "profile_coverage": profile_coverage(parsed_profile, catalog),
        "product_coverage": product_coverage(parsed_products, catalog),
        "grounding": grounding_metrics(manifest, bundle_in.user_text or "",
                                       product_texts, draft_anchors),
        "hallucination": hallucination_metrics(
            parsed_profile, parsed_products, manifest, bundle_in.user_text or "",
            product_texts, draft_anchors, registered_identity),
        "structure": {
            "passed": structure["passed"],
            "failed_checks": structure["failed_checks"],
            "issue_count": structure["issue_count"],
            "valid_set": structure["valid_set"],
        },
    }
    if not (manifest.get("profile") or manifest.get("products")):
        report["grounding"]["note"] = ("正则链/降级链：无模型 quote 清单；产品侧以确定性抽取"
                                       "草稿的观察级 evidence 引文复核（逐字回溯由构造保证），"
                                       "画像侧无锚点清单")
    return report


def summarize(reports: list[dict]) -> dict:
    users = len(reports)

    def _mean(values: list) -> Optional[float]:
        vals = [v for v in values if isinstance(v, (int, float))]
        return round(sum(vals) / len(vals), 4) if vals else None

    g_checked = sum(r["grounding"]["checked"] for r in reports)
    g_passed = sum(r["grounding"]["passed"] for r in reports)
    h_written = sum(r["hallucination"]["written"] for r in reports)
    h_suspects = sum(len(r["hallucination"]["suspects"]) for r in reports)
    return {
        "users": users,
        "chains": sorted({r["chain"] for r in reports}),
        "structure_pass_users": sum(1 for r in reports if r["structure"]["passed"]),
        "profile_coverage_overall_mean": _mean(
            [r["profile_coverage"]["overall"]["rate"] for r in reports]),
        "product_coverage_overall_mean": _mean(
            [r["product_coverage"]["overall"]["rate"] for r in reports]),
        "grounding": {"checked": g_checked, "passed": g_passed,
                      "rate": round(g_passed / g_checked, 4) if g_checked else None},
        "hallucination": {"written": h_written, "suspects": h_suspects,
                          "rate": round(h_suspects / h_written, 4) if h_written else None},
        "profile_group_mean": {
            group: _mean([r["profile_coverage"]["groups"].get(group, {}).get("rate")
                          for r in reports])
            for group in (reports[0]["profile_coverage"]["groups"] if reports else {})},
        "product_group_mean": {
            group: _mean([r["product_coverage"]["groups"].get(group, {}).get("rate")
                          for r in reports])
            for group in (reports[0]["product_coverage"]["groups"] if reports else {})},
    }


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python tools/local_eval.py",
        description="本地量化评估：6 示例用户全链实跑（真实链/正则链）+ 覆盖率/grounding/幻觉/结构报告")
    parser.add_argument("--user", default="all",
                        help="all（缺省，全部用户）或编号/编号列表，如 1、1,3-4")
    parser.add_argument("--input-dir", default=str(DATA_POOL),
                        help="样例资料池（缺省 raw/dataset_sample/Data_for_Users）")
    parser.add_argument("--output-root", default=None,
                        help="报告与产物根目录（缺省 reports/local_eval/run_<时间戳>）")
    parser.add_argument("--chain", default="auto", choices=["auto", "real", "regex"],
                        help="auto=按环境判定（缺省）；regex=强制确定性正则链；real=强制真实链")
    args = parser.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    input_pool = Path(args.input_dir)
    if not input_pool.is_dir():
        print(f"[local-eval] 样例池不存在：{input_pool}")
        return 1

    all_users = discover_users(input_pool)
    if args.user.strip().lower() == "all":
        selected = list(all_users)
    else:
        wanted: set[str] = set()
        for part in re.split(r"[,\s]+", args.user.strip()):
            if not part:
                continue
            if "-" in part:
                lo, hi = part.split("-", 1)
                for n in range(int(lo), int(hi) + 1):
                    wanted.add(f"User_Description_{n}.txt")
            else:
                wanted.add(f"User_Description_{int(part)}.txt")
        selected = [u for u in all_users if u in wanted]
        missing = sorted(wanted - set(all_users))
        if missing:
            print(f"[local-eval] 忽略不存在的用户：{missing}")
    if not selected:
        print("[local-eval] 没有可评估的用户")
        return 1

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    output_root = Path(args.output_root) if args.output_root else (
        DEFAULT_OUTPUT_ROOT / f"run_{timestamp}")
    output_root.mkdir(parents=True, exist_ok=True)

    chain, chain_note = resolve_chain(args.chain)
    print(f"[local-eval] 链路={chain}（{chain_note}）")
    print(f"[local-eval] 用户={len(selected)} 输出根={output_root}")

    reports = []
    for user_file in selected:
        print(f"[local-eval] ---- {user_file} ----")
        report = evaluate_user(user_file, output_root, chain)
        reports.append(report)
        pc = report["profile_coverage"]["overall"]
        prc = report["product_coverage"]["overall"]
        print(f"[local-eval] {report['user']} chain={report['chain']} "
              f"画像覆盖={pc['covered']}/{pc['total']}({pc['rate']}) "
              f"产品覆盖={prc['covered']}/{prc['total']}({prc['rate']}) "
              f"grounding={report['grounding']['rate']} "
              f"幻觉疑似={len(report['hallucination']['suspects'])}"
              f"/{report['hallucination']['written']} "
              f"结构={'pass' if report['structure']['passed'] else 'fail'}"
              f"{' 错误=' + report['run_error'] if report['run_error'] else ''}")

    summary = summarize(reports)
    payload = {
        "tool": TOOL_NAME,
        "report_version": REPORT_VERSION,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "input_pool": str(input_pool),
        "chain": chain,
        "chain_note": chain_note,
        "metric_notes": {
            "coverage": "渲染冻结→parse 回读口径；缺失表达（未提供/待核验/优先级未明确，含带说明形态）计为缺失",
            "grounding": "写入字段中 exact_quote 能逐字回溯来源的比例（精确子串；正则链由确定性抽取器构造保证）",
            "hallucination": "值与逐字锚点均无法回溯原文的疑似项比例（启发式，suspects 供人工复核）",
        },
        "summary": summary,
        "users": reports,
    }
    report_path = output_root / "report.json"
    report_path.write_text(json.dumps(payload, ensure_ascii=False, indent=1),
                           encoding="utf-8")
    print("=" * 72)
    print(f"[local-eval] 汇总：用户={summary['users']} 链路={summary['chains']} "
          f"结构通过={summary['structure_pass_users']}/{summary['users']}")
    print(f"[local-eval] 画像覆盖率均值={summary['profile_coverage_overall_mean']} "
          f"产品覆盖率均值={summary['product_coverage_overall_mean']}")
    print(f"[local-eval] grounding={summary['grounding']} "
          f"幻觉疑似率={summary['hallucination']['rate']}")
    print(f"[local-eval] 画像分组均值={json.dumps(summary['profile_group_mean'], ensure_ascii=False)}")
    print(f"[local-eval] 产品分组均值={json.dumps(summary['product_group_mean'], ensure_ascii=False)}")
    print(f"[local-eval] 报告已写入：{report_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
