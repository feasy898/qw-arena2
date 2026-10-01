# -*- coding: utf-8 -*-
"""src/validators.py — 确定性校验（全部程序判定，不用模型）。

契约（contracts/interfaces.md §11）：validate_outputs 实现 SPEC §10 确定性
校验清单十项：
1. 恰好 3 份文档，命名精确（E1）；
2. 产品全集 = 输入全集，无遗漏/重复/拆并错误（E2）；
3. 品牌型号原样（E3）；
4. 所有产品表结构一致（E4）；
5. 推荐 = 3 个不同产品且 ∈ E（E5；E 为接口扩展参数 valid_ids，
   未提供时校验 ∈ product_list 全集——E 的语义判定属 constraint_engine）；
6. 未选表覆盖其余全集（可合并同因行）（E6）；
7. 推荐卡风险/核验字段非空（E7，文件层查「注意事项」；对象层查
   costs_risks/pre_purchase_checks）；
8. 引用存在且指向前两份文档内字段（不越界引原文）（E8）；
9. 退出码/运行时间/文件编码（E9：编码在文件层判 UTF-8 无 BOM；
   运行时间经接口扩展参数 run_seconds 判硬截止 1800s；退出码由调用方
   依据「存在 high 级 issue」映射，platform_contract.exit_codes）；
10. 表格可被 Markdown 解析器解析（列数一致、无坏行）（E10，经 renderer.parse_*）。

返回空列表 = 通过；任一 severity="high" = 失败（medium/low 不判失败）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any, Optional, Union

from src import renderer
from src.product_registry import build_registry, normalize_product_id
from src.renderer import (
    LEVELS,
    MISSING_NOT_PROVIDED,
    MISSING_VERIFY,
    NO_VALID_BACKUP,
    parse_products,
    parse_profile,
    parse_recommendation,
    product_table_structures,
)
from src.schemas import FactCell

PathLike = Union[str, os.PathLike]

EXCLUSION_CATEGORIES = (
    "硬约束不符",
    "硬约束证据不足",
    "软需求匹配弱",
    "同条件下被压过",
    "无足够新增价值",
)
TIME_HARD_SECONDS = 1800
_MISSING_PREFIXES = (MISSING_NOT_PROVIDED, MISSING_VERIFY, "优先级未明确")
_BATTERY_NAME_RE = re.compile(r"续航\d+·")


@dataclass
class ValidationIssue:
    """一条校验问题。severity="high" 视为失败。"""

    code: str
    severity: str  # "high" | "medium" | "low"
    message: str


def _issue(code: str, severity: str, message: str) -> ValidationIssue:
    return ValidationIssue(code=code, severity=severity, message=message)


def _is_missing_text(value: Any) -> bool:
    return isinstance(value, str) and (value.strip() in {MISSING_NOT_PROVIDED, MISSING_VERIFY, "优先级未明确"}
                                       or value.strip().startswith(_MISSING_PREFIXES))


def _identity_form(text: str) -> str:
    """身份比较形：去空白 + casefold（与 src/product_registry._identity_form 同口径）。"""
    return re.sub(r"\s+", "", text).casefold()


def _catalog_fields(section: str) -> list[tuple[str, dict]]:
    return renderer.catalog_fields(section)


# ---------------------------------------------------------------------------
# 对象级校验（抽取网关侧 / 决策侧）
# ---------------------------------------------------------------------------

def _check_object(
    obj: dict,
    template: list[tuple[str, dict]],
    code_prefix: str,
    issues: list[ValidationIssue],
) -> None:
    """字段目录通用校验：未知键/必填键/枚举/类型形状/结构化列表项。"""
    known = {f["key"]: (g, f) for g, f in template}
    for key, value in obj.items():
        if key not in known:
            issues.append(_issue(f"{code_prefix}_UNKNOWN_KEY", "medium", f"未知字段 key：{key}"))
    for key, (_group, field_def) in known.items():
        if key not in obj:
            if field_def.get("required"):
                issues.append(
                    _issue(f"{code_prefix}_REQUIRED_ABSENT", "high",
                           f"必填字段缺失（无该 key）：{key}（{field_def['name']}）")
                )
            continue
        value = obj[key]
        enum = field_def.get("enum")
        if enum and value is not None and not _is_missing_text(str(value)) and str(value) not in enum:
            issues.append(
                _issue(f"{code_prefix}_ENUM", "high",
                       f"字段 {key} 取值「{value}」不在枚举 {enum} 内")
            )
        ftype = field_def.get("type")
        is_fact = isinstance(value, FactCell) or (
            isinstance(value, dict) and ("raw_value" in value or "normalized_value" in value)
        )
        if value is not None and not is_fact:
            if ftype == "列表" and not isinstance(value, list):
                severity = "medium" if isinstance(value, str) else "high"
                issues.append(_issue(f"{code_prefix}_TYPE", severity,
                                     f"列表字段 {key} 应为 list，实为 {type(value).__name__}"))
            elif ftype != "列表" and isinstance(value, list):
                issues.append(_issue(f"{code_prefix}_TYPE", "high",
                                     f"标量字段 {key} 不应为 list"))
            elif isinstance(value, dict):
                issues.append(_issue(f"{code_prefix}_TYPE", "high",
                                     f"字段 {key} 取值不可为未知 dict 形态"))
        if isinstance(value, str) and value.strip() == MISSING_VERIFY:
            issues.append(
                _issue(f"{code_prefix}_VERIFY_NOTE", "medium",
                       f"字段 {key} 仅写「待核验」未附条件说明（field_catalog missing_selection_rule）")
            )


def _check_structured_items(
    obj: dict,
    list_key: str,
    item_fields: list[dict],
    code_prefix: str,
    issues: list[ValidationIssue],
) -> None:
    value = obj.get(list_key)
    if value is None:
        return
    if not isinstance(value, list) or any(not isinstance(item, dict) for item in value):
        issues.append(_issue(f"{code_prefix}_TYPE", "high",
                             f"结构化列表 {list_key} 应为 list[dict]，实为 {type(value).__name__}"))
        return
    required_items = {item["key"]: item for item in item_fields if item.get("required")}
    known_items = {item["key"] for item in item_fields}
    # 契约 missing="优先级未明确" 的两键：无法排序时写「优先级未明确」是官方
    # value_rule 的合法表达（field_catalog scenes.item_fields），渲染回读还会把
    # 该表达解析回 None。按缺失报 high 会让修复环无法收敛（原文无优先级依据
    # 时不可伪造整数），v0.5.1 实测 6 用户 3 户因此修复环用尽。对齐契约：豁免
    # ITEM_REQUIRED，枚举/类型检查保留。
    soft_missing_items = {key for key, item_def in required_items.items()
                          if item_def.get("missing") == "优先级未明确"}
    for index, item in enumerate(value, start=1):
        for key in item:
            if key not in known_items:
                issues.append(_issue(f"{code_prefix}_UNKNOWN_KEY", "medium",
                                     f"{list_key}[{index}] 未知项字段：{key}"))
        for key, item_def in required_items.items():
            if key in soft_missing_items:
                continue
            sub = item.get(key)
            if sub is None or (isinstance(sub, str) and _is_missing_text(sub)):
                issues.append(
                    _issue(f"{code_prefix}_ITEM_REQUIRED", "high",
                           f"{list_key}[{index}].{key}（{item_def['name'].format(n=index)}）缺失")
                )
        priority = item.get("scene_priority") if "scene_priority" in item else None
        if (priority is not None and str(priority) != "优先级未明确"
                and (isinstance(priority, bool) or not isinstance(priority, int))):
            issues.append(_issue(f"{code_prefix}_TYPE", "high",
                                 f"{list_key}[{index}].scene_priority 应为整数，实为 {priority!r}"))
        basis = item.get("priority_basis")
        if "priority_basis" in item and basis is not None and not _is_missing_text(str(basis)):
            enum = next(i["enum"] for i in item_fields if i["key"] == "priority_basis")
            if str(basis) not in enum:
                issues.append(_issue(f"{code_prefix}_ENUM", "high",
                                     f"{list_key}[{index}].priority_basis「{basis}」不在枚举 {enum}"))


def validate_profile_object(obj: dict) -> list[ValidationIssue]:
    """校验画像对象（抽取网关侧）：key ∈ field_catalog.profile，枚举/类型/缺失表达合法。"""
    issues: list[ValidationIssue] = []
    template = _catalog_fields("profile")
    _check_object(obj, template, "OBJ_PROFILE", issues)
    scenes_def = next(f for _g, f in template if f["key"] == "scenes")
    _check_structured_items(obj, "scenes", scenes_def["item_fields"], "OBJ_PROFILE", issues)
    return issues


def validate_product_object(obj: dict) -> list[ValidationIssue]:
    """校验单个产品对象（抽取网关侧）：key ∈ field_catalog.product，结构/单位/条件合法。"""
    issues: list[ValidationIssue] = []
    template = _catalog_fields("product")
    _check_object(obj, template, "OBJ_PRODUCT", issues)
    cid = obj.get("canonical_id")
    if cid is not None and not _is_missing_text(str(cid)) and normalize_product_id(str(cid)) != str(cid):
        issues.append(_issue("OBJ_PRODUCT_ID_FORMAT", "high",
                             f"canonical_id 须为三位规范形 P###，实为「{cid}」"))
    battery_def = next(f for _g, f in template if f.get("item_fields"))
    _check_structured_items(obj, battery_def["key"], battery_def["item_fields"], "OBJ_PRODUCT", issues)
    return issues


def validate_plan(plan: object, products: dict[str, dict]) -> list[ValidationIssue]:
    """校验 DecisionPlan：层级集合、产品存在性、推荐卡必填字段非空、未选覆盖全集。"""
    issues: list[ValidationIssue] = []
    data = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
    product_ids = set(products or {})

    selected = [str(x) for x in (data.get("selected_products") or [])]
    cards = [c.to_dict() if hasattr(c, "to_dict") else dict(c)
             for c in (data.get("recommendation_cards") or [])]

    if not 1 <= len(cards) <= 3:
        issues.append(_issue("PLAN_CARD_COUNT", "high",
                             f"推荐卡数应为 1~3（异常路径允许空位说明行），实为 {len(cards)}"))
    levels = [str(c.get("level") or "") for c in cards]
    if levels and levels != list(LEVELS[: len(cards)]):
        issues.append(_issue("PLAN_LEVELS", "high",
                             f"推荐层级序列 {levels} ≠ {list(LEVELS[: len(cards)])}（行序即优先次序）"))

    real_ids: list[str] = []
    for index, card in enumerate(cards, start=1):
        pid = str(card.get("product_id") or "").strip()
        if not pid:
            # 异常路径空位卡：必须带空位说明
            has_note = bool(card.get("profile_match") or card.get("tradeoffs"))
            if not has_note:
                issues.append(_issue("PLAN_EMPTY_SLOT", "medium",
                                     f"第 {index} 行为空位卡但无「{NO_VALID_BACKUP}」说明"))
            continue
        real_ids.append(pid)
        if normalize_product_id(pid) != pid:
            issues.append(_issue("PLAN_PRODUCT_ID_FORMAT", "high",
                                 f"第 {index} 行产品ID「{pid}」非三位规范形"))
        if pid not in product_ids:
            issues.append(_issue("PLAN_PRODUCT_UNKNOWN", "high",
                                 f"第 {index} 行产品ID「{pid}」不在产品全集"))
        if not card.get("profile_match"):
            issues.append(_issue("PLAN_CARD_MATCH", "medium",
                                 f"{pid} 推荐卡未点明命中画像需求"))
        if not card.get("fact_references"):
            issues.append(_issue("PLAN_CARD_FACTS", "high",
                                 f"{pid} 推荐卡无事实依据引用（fact_references 为空）"))
        if not str(card.get("costs_risks") or "").strip():
            issues.append(_issue("PLAN_CARD_RISKS", "high",
                                 f"{pid} 推荐卡「代价与风险」为空（官方要求不得留空）"))
        if not card.get("pre_purchase_checks"):
            issues.append(_issue("PLAN_CARD_CHECKS", "high",
                                 f"{pid} 推荐卡「购买前核验」为空（官方要求不得留空）"))
        if not card.get("switch_conditions"):
            issues.append(_issue("PLAN_CARD_SWITCH", "medium",
                                 f"{pid} 推荐卡「改选条件」为空"))

    if len(real_ids) != len(set(real_ids)):
        issues.append(_issue("PLAN_PRODUCT_DUP", "high", f"推荐产品重复：{real_ids}"))
    if selected[: len(real_ids)] != real_ids:
        issues.append(_issue("PLAN_SELECTED_MISMATCH", "high",
                             f"selected_products {selected} 与推荐卡行序 {real_ids} 不一致"))
    if not str(data.get("overall_conclusion") or "").strip():
        issues.append(_issue("PLAN_CONCLUSION", "high", "综合结论为空（官方要求表后一句综合结论）"))

    covered: set[str] = set()
    for exclusion_raw in data.get("excluded_products") or []:
        exclusion = exclusion_raw.to_dict() if hasattr(exclusion_raw, "to_dict") else dict(exclusion_raw)
        ids = [str(x) for x in (exclusion.get("product_ids") or [])]
        if not ids:
            issues.append(_issue("PLAN_EXCLUSION_IDS", "high", "未选原因行缺产品ID"))
            continue
        category = str(exclusion.get("reason_category") or "")
        if category not in EXCLUSION_CATEGORIES:
            issues.append(_issue("PLAN_EXCLUSION_CATEGORY", "high",
                                 f"未选原因类别「{category}」不在五类 {list(EXCLUSION_CATEGORIES)}"))
        if not str(exclusion.get("reason_detail") or "").strip():
            issues.append(_issue("PLAN_EXCLUSION_DETAIL", "medium",
                                 f"{ids} 未选原因未落到具体字段"))
        if not exclusion.get("change_conditions"):
            issues.append(_issue("PLAN_EXCLUSION_CONDITION", "medium",
                                 f"{ids} 无建议改变条件句"))
        for pid in ids:
            if pid in covered:
                issues.append(_issue("PLAN_EXCLUSION_DUP", "high", f"产品「{pid}」出现在多行未选原因"))
            covered.add(pid)
            if pid not in product_ids:
                issues.append(_issue("PLAN_EXCLUSION_UNKNOWN", "high",
                                     f"未选产品ID「{pid}」不在产品全集"))
            if pid in real_ids:
                issues.append(_issue("PLAN_EXCLUSION_OVERLAP", "high",
                                     f"产品「{pid}」同时出现在推荐与未选中"))
    expected_excluded = product_ids - set(real_ids)
    missing = expected_excluded - covered
    if missing:
        issues.append(_issue("PLAN_EXCLUSION_COVERAGE", "high",
                             f"未选表未覆盖：{sorted(missing)}"))
    extra = covered - product_ids
    if extra:
        issues.append(_issue("PLAN_EXCLUSION_COVERAGE", "high",
                             f"未选表出现全集外产品：{sorted(extra)}"))
    return issues


# ---------------------------------------------------------------------------
# validate_outputs：SPEC §10 十项（文件层）
# ---------------------------------------------------------------------------

def _citation_targets(profile: Optional[dict], products: Optional[dict[str, dict]]) -> tuple[set, dict]:
    """由 parse 后的前两份文档对象构建合法引用目标：{(组,字段名)} 与 {cid: {字段名}}。"""
    profile_targets: set[tuple[str, str]] = set()
    if profile is not None:
        for group_name, field_def in _catalog_fields("profile"):
            profile_targets.add((group_name, field_def["name"]))
            if field_def.get("item_fields"):
                scene_count = len(profile.get("scenes") or [])
                for n in range(1, scene_count + 1):
                    for item in field_def["item_fields"]:
                        profile_targets.add((group_name, item["name"].format(n=n)))
    product_targets: dict[str, set[str]] = {}
    if products:
        battery_def = next(
            f for _g, f in _catalog_fields("product") if f.get("item_fields")
        )
        for cid, obj in products.items():
            names = {f["name"] for _g, f in _catalog_fields("product")}
            names.add(battery_def["name"])
            for n in range(1, len(obj.get("battery_by_mode") or []) + 1):
                for item in battery_def["item_fields"]:
                    names.add(item["name"].format(n=n))
            product_targets[cid] = names
    return profile_targets, product_targets


def _strip_trailing_note(target: str) -> str:
    """引用目标尾部若带（说明）注记则剥离后重试（如「预算上限（约(单值)）」）。"""
    target = target.strip()
    if target.endswith("）"):
        depth = 0
        for index in range(len(target) - 1, -1, -1):
            ch = target[index]
            if ch == "）":
                depth += 1
            elif ch == "（":
                depth -= 1
                if depth == 0:
                    return target[:index].strip()
    return target


_QUOTE_CHARS = "」』\"'"


def _clean_citation_part(part: str) -> str:
    """剥离引用片段首尾的引号/书名号收尾符（字段名本身不含这些字符）。"""
    return part.strip().strip(_QUOTE_CHARS).strip()


def _check_citations(
    cells: list[str],
    profile_targets: set,
    product_targets: dict,
    issues: list[ValidationIssue],
    context: str,
) -> int:
    """校验单元格内全部引用不越界；返回合法引用命中数。"""
    valid = 0
    for cell in cells:
        for match in renderer.CITATION_RE.finditer(str(cell)):
            kind = match.group(1)
            left = _clean_citation_part(match.group(2))
            right = _clean_citation_part(match.group(3))
            if kind == "画像":
                target = (left, right)
                if target in profile_targets or (left, _strip_trailing_note(right)) in profile_targets:
                    valid += 1
                else:
                    issues.append(_issue("E8_CITATIONS", "high",
                                         f"{context}：画像引用「{kind}｜{left}｜{right}」不指向前两份文档字段"))
            else:
                cid = normalize_product_id(left)
                names = product_targets.get(cid) if cid else None
                if cid and names and (right in names or _strip_trailing_note(right) in names):
                    valid += 1
                else:
                    issues.append(_issue("E8_CITATIONS", "high",
                                         f"{context}：产品引用「{kind}｜{left}｜{right}」不存在或越界"))
    return valid


def validate_outputs(
    output_dir: PathLike,
    input_dir: PathLike,
    *,
    valid_ids: Optional[set[str]] = None,
    run_seconds: Optional[float] = None,
) -> list[ValidationIssue]:
    """对输出目录执行 SPEC §10 十项确定性校验。

    :param output_dir: 输出目录
    :param input_dir: 对应输入目录（校验产品全集/品牌型号原样需要）
    :param valid_ids: 可选，有效集 E（constraint_engine 产出）；提供则校验推荐 ⊆ E
    :param run_seconds: 可选，本次运行耗时（秒）；> 1800 记 high（硬截止）
    :return: 问题列表（空列表 = 通过；high 级 = 失败）
    """
    issues: list[ValidationIssue] = []
    output_dir = os.fspath(output_dir)

    # ---- E1 恰好 3 份文档，命名精确 --------------------------------------
    expected_names = [
        renderer.PROFILE_FILENAME, renderer.PRODUCTS_FILENAME, renderer.RECOMMENDATION_FILENAME
    ]
    entries = set()
    if not os.path.isdir(output_dir):
        issues.append(_issue("E1_OUTPUT_FILES", "high", f"输出目录不存在：{output_dir}"))
    else:
        entries = set(os.listdir(output_dir))
        expected_set = set(expected_names)
        for name in sorted(expected_set - entries):
            issues.append(_issue("E1_OUTPUT_FILES", "high", f"缺少产出文件：{name}"))
        for name in sorted(entries - expected_set):
            issues.append(_issue("E1_OUTPUT_FILES", "high", f"输出目录含多余条目：{name}"))

    # ---- E9(部分) 文件编码：UTF-8 无 BOM、换行 \n；运行时间 ----------------
    texts: dict[str, str] = {}
    for name in expected_names:
        path = os.path.join(output_dir, name)
        if name not in entries:
            continue
        try:
            with open(path, "rb") as handle:
                raw = handle.read()
        except OSError as exc:
            issues.append(_issue("E9_RUNTIME_ENCODING", "high", f"{name} 无法读取：{exc}"))
            continue
        if raw.startswith(b"\xef\xbb\xbf"):
            issues.append(_issue("E9_RUNTIME_ENCODING", "high", f"{name} 含 UTF-8 BOM（要求无 BOM）"))
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError as exc:
            issues.append(_issue("E9_RUNTIME_ENCODING", "high", f"{name} 非 UTF-8 编码：{exc}"))
            continue
        if "\r\n" in text or "\r" in text:
            issues.append(_issue("E9_RUNTIME_ENCODING", "medium", f"{name} 含 CR/LF（平台要求 \\n）"))
        texts[name] = text
    if run_seconds is not None and run_seconds > TIME_HARD_SECONDS:
        issues.append(_issue("E9_RUNTIME_ENCODING", "high",
                             f"运行时间 {run_seconds:.0f}s 超过硬截止 {TIME_HARD_SECONDS}s"))

    # ---- E10/E4 前置：解析三份文档（表格可解析、无坏行） --------------------
    profile_obj: Optional[dict] = None
    products_obj: Optional[dict[str, dict]] = None
    rec_obj: Optional[dict] = None
    if renderer.PROFILE_FILENAME in texts:
        try:
            profile_obj = parse_profile(texts[renderer.PROFILE_FILENAME])
            issues.extend(validate_profile_object(profile_obj))
        except ValueError as exc:
            issues.append(_issue("E10_MARKDOWN_TABLE", "high", f"user_profile.md 解析失败：{exc}"))
    if renderer.PRODUCTS_FILENAME in texts:
        try:
            products_obj = parse_products(texts[renderer.PRODUCTS_FILENAME])
        except ValueError as exc:
            issues.append(_issue("E10_MARKDOWN_TABLE", "high", f"product_list.md 解析失败：{exc}"))
    if renderer.RECOMMENDATION_FILENAME in texts:
        try:
            rec_obj = parse_recommendation(texts[renderer.RECOMMENDATION_FILENAME])
        except ValueError as exc:
            issues.append(_issue("E10_MARKDOWN_TABLE", "high", f"recommendation.md 解析失败：{exc}"))

    # ---- E2 产品全集 = 输入全集 ------------------------------------------
    registry_ids: list[str] = []
    registry_products: dict[str, Any] = {}
    if products_obj is not None:
        try:
            from src.input_adapter import load_input

            registry = build_registry(load_input(input_dir).source_records)
            registry_ids = registry.ids()
            registry_products = {p.canonical_id: p for p in registry.all()}
        except Exception as exc:  # InputError 等：输入目录不合法按 E2 记录
            issues.append(_issue("E2_PRODUCT_COVERAGE", "high", f"输入目录不可解析，无法比对产品全集：{exc}"))
            registry_ids = []
        parsed_ids = sorted(products_obj)
        if registry_ids:
            if sorted(registry_ids) != parsed_ids:
                missing = sorted(set(registry_ids) - set(parsed_ids))
                extra = sorted(set(parsed_ids) - set(registry_ids))
                if missing:
                    issues.append(_issue("E2_PRODUCT_COVERAGE", "high",
                                         f"product_list.md 缺少产品节：{missing}"))
                if extra:
                    issues.append(_issue("E2_PRODUCT_COVERAGE", "high",
                                         f"product_list.md 出现输入全集外产品：{extra}"))
            for cid, obj in products_obj.items():
                issues.extend(validate_product_object(obj))

            # ---- E3 品牌型号原样 ----------------------------------------
            for cid in sorted(set(parsed_ids) & set(registry_products)):
                record = registry_products[cid]
                obj = products_obj[cid]
                brand = obj.get("brand")
                model = obj.get("model")
                if record.brand_raw:
                    if not brand or _is_missing_text(str(brand)):
                        issues.append(_issue("E3_VERBATIM_IDENTITY", "high",
                                             f"{cid} 品牌缺失（输入资料为「{record.brand_raw}」）"))
                    elif _identity_form(str(brand)) != _identity_form(record.brand_raw):
                        issues.append(_issue("E3_VERBATIM_IDENTITY", "high",
                                             f"{cid} 品牌「{brand}」≠ 输入资料原文「{record.brand_raw}」（须原样沿用）"))
                if record.model_raw:
                    if not model or _is_missing_text(str(model)):
                        issues.append(_issue("E3_VERBATIM_IDENTITY", "high",
                                             f"{cid} 型号缺失（输入资料为「{record.model_raw}」）"))
                    elif _identity_form(record.model_raw) not in _identity_form(str(model)):
                        issues.append(_issue("E3_VERBATIM_IDENTITY", "high",
                                             f"{cid} 型号「{model}」不含输入资料原文「{record.model_raw}」（须原样沿用）"))

            # ---- E4 所有产品表结构一致 ----------------------------------
            # 每产品表 = field_catalog 模板（结构化列表按该项数展开）；
            # 跨产品在「续航{n}」归一后必须完全一致（结构一致、列表长度可变）。
            try:
                structures = product_table_structures(texts[renderer.PRODUCTS_FILENAME])
            except ValueError as exc:
                issues.append(_issue("E10_MARKDOWN_TABLE", "high", f"product_list.md 表结构读取失败：{exc}"))
                structures = []
            product_template = _catalog_fields("product")
            battery_template = next(f for _g, f in product_template if f.get("item_fields"))

            def _expected_rows(obj: dict) -> list[tuple[str, str]]:
                n_modes = len(obj.get(battery_template["key"]) or [])
                rows: list[tuple[str, str]] = []
                for group_name, field_def in product_template:
                    if field_def.get("item_fields"):
                        if n_modes:
                            for n in range(1, n_modes + 1):
                                for item in field_def["item_fields"]:
                                    rows.append((group_name, item["name"].format(n=n)))
                        else:
                            rows.append((group_name, field_def["name"]))
                    else:
                        rows.append((group_name, field_def["name"]))
                return rows

            def _normalize(name: str) -> str:
                return _BATTERY_NAME_RE.sub("续航{n}·", name)

            def _project(rows: list[tuple[str, str]]) -> list[tuple[str, str]]:
                """把续航逐模式行（或缺失标记行）折叠为单个结构 token 后的行序列。

                「所有产品表格结构完全一致」指同一模板同一字段序；续航模式数随产品
                而异（缺产品级缺失表达行），折叠后必须完全一致。
                """
                out: list[tuple[str, str]] = []
                for group_name, name in rows:
                    if _BATTERY_NAME_RE.match(name) or name == battery_template["name"]:
                        token = (group_name, battery_template["name"])
                        if not out or out[-1] != token:
                            out.append(token)
                    else:
                        out.append((group_name, name))
                return out

            seen_ids = [cid for cid, _rows in structures]
            if len(seen_ids) != len(set(seen_ids)):
                issues.append(_issue("E2_PRODUCT_COVERAGE", "high",
                                     f"product_list.md 存在重复产品节：{seen_ids}"))
            projected_sequences: list[list[tuple[str, str]]] = []
            for cid, rows in structures:
                obj = products_obj.get(cid) or {}
                expected = _expected_rows(obj)
                if rows != expected:
                    diff_count = sum(1 for a, e in zip(rows, expected) if a != e) + abs(len(rows) - len(expected))
                    issues.append(_issue("E4_TABLE_STRUCTURE", "high",
                                         f"{cid} 表结构与 field_catalog 约定不一致"
                                         f"（差异约 {diff_count} 处；实际 {len(rows)} 行 / 约定 {len(expected)} 行）"))
                projected_sequences.append(_project(rows))
            if projected_sequences and any(seq != projected_sequences[0] for seq in projected_sequences[1:]):
                issues.append(_issue("E4_TABLE_STRUCTURE", "high",
                                     "各产品表结构（字段名序列，续航折叠后）不一致，"
                                     "违反官方「所有产品的表格结构必须完全一致」"))

    # ---- E5/E6/E7/E8：推荐报告 -------------------------------------------
    if rec_obj is not None:
        rec_rows = rec_obj["recommendations"]
        if len(rec_rows) != 3:
            issues.append(_issue("E5_RECOMMENDATION", "high",
                                 f"推荐结果表应恰 3 行（首选/备选1/备选2），实为 {len(rec_rows)} 行"))
        levels = [row["level"] for row in rec_rows]
        if levels != list(LEVELS):
            issues.append(_issue("E5_RECOMMENDATION", "high",
                                 f"推荐层级序列 {levels} ≠ {list(LEVELS)}"))
        real_ids: list[str] = []
        for row in rec_rows:
            pid = row["product_id"]
            if pid is None:
                # 异常路径空位行：须含空位说明
                if NO_VALID_BACKUP not in row["reason"]:
                    issues.append(_issue("E5_RECOMMENDATION", "high",
                                         f"第 {levels.index(row['level']) + 1} 行为空位但未说明「{NO_VALID_BACKUP}」"))
                continue
            real_ids.append(pid)
            if normalize_product_id(pid) != pid:
                issues.append(_issue("E5_RECOMMENDATION", "high",
                                     f"推荐产品ID「{pid}」非三位规范形（与 product_list 编号须完全一致）"))
            elif products_obj is not None and pid not in products_obj:
                issues.append(_issue("E5_RECOMMENDATION", "high",
                                     f"推荐产品ID「{pid}」不在 product_list.md 产品全集"))
            elif valid_ids is not None and pid not in valid_ids:
                issues.append(_issue("E5_RECOMMENDATION", "high",
                                     f"推荐产品ID「{pid}」不在有效集 E"))
            if not row["reason"].strip() or _is_missing_text(row["reason"]):
                issues.append(_issue("E5_RECOMMENDATION", "high",
                                     f"「{pid or row['level']}」画像匹配与推荐理由为空"))
        if len(real_ids) != len(set(real_ids)):
            issues.append(_issue("E5_RECOMMENDATION", "high", f"推荐产品重复：{real_ids}"))
        conclusion = rec_obj.get("overall_conclusion")
        if not conclusion or _is_missing_text(str(conclusion)):
            issues.append(_issue("E5_RECOMMENDATION", "high",
                                 "推荐结果表后缺一句综合结论（不得为空/缺失表达）"))

        # E7 推荐卡注意事项非空
        for row in rec_rows:
            if row["product_id"] is None:
                continue
            notes = str(row["notes"] or "").strip()
            if not notes or _is_missing_text(notes):
                issues.append(_issue("E7_CARD_FIELDS", "high",
                                     f"{row['product_id']} 注意事项为空（使用限制/代价+购买前核验不得留空）"))

        # E6 未选表覆盖其余全集
        excluded_ids: list[str] = []
        for row in rec_obj["exclusions"]:
            for pid in row["product_ids"]:
                excluded_ids.append(pid)
        if products_obj is not None:
            for pid in excluded_ids:
                if normalize_product_id(pid) != pid:
                    issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                         f"未选产品ID「{pid}」非三位规范形"))
                elif pid not in products_obj:
                    issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                         f"未选产品ID「{pid}」不在 product_list.md 产品全集"))
                elif pid in real_ids:
                    issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                         f"产品「{pid}」同时出现在推荐与未选中"))
            if len(excluded_ids) != len(set(excluded_ids)):
                issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                     f"产品在多行未选中重复出现（同因合并须在同一行）：{excluded_ids}"))
            expected_excluded = set(products_obj) - set(real_ids)
            actual_excluded = set(excluded_ids)
            if actual_excluded != expected_excluded:
                missing = sorted(expected_excluded - actual_excluded)
                extra = sorted(actual_excluded - expected_excluded)
                if missing:
                    issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                         f"未选表未覆盖全部未入选产品，缺少：{missing}"))
                if extra:
                    issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                         f"未选表出现全集外/已入选产品：{extra}"))
        for row in rec_obj["exclusions"]:
            if not row["reason"].strip() or _is_missing_text(row["reason"]):
                issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                     f"{row['product_ids']} 未选原因为空"))
            if not row["change_conditions"]:
                issues.append(_issue("E6_EXCLUSION_COVERAGE", "high",
                                     f"{row['product_ids']} 无建议改变条件句"))

        # E8 引用存在且不越界
        profile_targets, product_targets = _citation_targets(profile_obj, products_obj)
        for row in rec_rows:
            if row["product_id"] is None:
                continue
            hits = _check_citations([row["reason"], row["notes"]],
                                    profile_targets, product_targets, issues,
                                    f"推荐结果/{row['product_id']}")
            if hits == 0:
                issues.append(_issue("E8_CITATIONS", "high",
                                     f"{row['product_id']} 推荐理由未引用前两份文档字段"
                                     f"（格式：画像｜字段组｜字段名 / 产品属性｜产品ID｜字段名）"))
        for row in rec_obj["exclusions"]:
            _check_citations([row["reason"]] + row["change_conditions"],
                             profile_targets, product_targets, issues,
                             f"未选原因/{'、'.join(row['product_ids'])}")

    return issues
