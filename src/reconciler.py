# -*- coding: utf-8 -*-
"""src/reconciler.py — 五源归并与冲突处理（确定性规则，不用模型）。

契约（contracts/interfaces.md §9）：
- merge_fact_cells：同字段多来源 FactCell → 单 FactCell，按 contracts/rules.json
  field_keep_limits 保留限定（渠道/快照时间/模式/版本/样本范围等，随 evidence_refs
  与 conditions 并列保留）；
- 冲突三步法（rules.json conflict_three_steps）：
  ①判真冲突（不同模式/时间/版本/条件 ≠ 冲突，按 applicable_variant/条件分组并列）
  → ②可解释则并列保留条件（禁止取平均、禁止直觉择一）
  → ③无法解释保留冲突并标「待核验」（status=存在冲突，取值文本并列两组表述）；
- FactCell.status 区分四种：有支持|附条件|存在冲突|输入缺失；
- 时效以输入资料内时间基准（as_of）判断，禁止用运行当天日期；
- find_coverage_gaps：反向覆盖检查，产出字段缺口清单供上游定点复核。

实现约定（供管线/渲染器对齐）：
- merge_fact_cells 接受 FactCell 实例或其 to_dict() 形态，返回 FactCell 实例；
  空列表 → status=输入缺失；
- 宣传表述（fact_kind=宣传表述）永不覆盖官网声明/实测观察/用户反馈：宣传与可靠
  取值不一致 → ③保留冲突标待核验；仅宣传 → 附条件+「待核验」条件说明；
- reconcile_product 输出为扁平产品对象终稿：key 集合与 field_catalog.product
  完全一致；普通字段 = FactCell.to_dict()；battery_by_mode = list[dict]
  （item key：mode/duration/conditions，附 status/evidence_refs 供溯源，渲染器
  按 item_fields 取前三个键）；列表型字段 = list[str]（营销与反馈条目自带
  出处/层级措辞，由抽取 Prompt 保证）；
- 待核验/未提供的表达：raw_value=None + status=输入缺失 → 渲染「未提供」；
  raw_value=None + status=附条件（conditions 携带原因）→ 渲染「待核验」。
"""
from __future__ import annotations

import csv
import json
import logging
import re
from pathlib import Path

from src.model_gateway import GatewayError
from src.schemas import EvidenceRef, FactCell, FactKind, FactStatus

logger = logging.getLogger("qw.reconciler")

REPO_ROOT = Path(__file__).resolve().parent.parent
FIELD_CATALOG_PATH = REPO_ROOT / "contracts" / "field_catalog.json"

# 宣传表述的权威度最低：永不覆盖可靠来源；可靠来源内部按官网优先（kind 与 status
# 分开：一致取值取官网声明的 kind，不升级为实测）
_KIND_AUTHORITY = {
    FactKind.MARKETING: 0,
    FactKind.INFERRED: 1,
    FactKind.USER_FEEDBACK: 2,
    FactKind.OFFICIAL_CLAIM: 3,
    FactKind.MEASURED: 3,
}
_KIND_FOR_VALUE = (FactKind.OFFICIAL_CLAIM, FactKind.MEASURED,
                   FactKind.USER_FEEDBACK, FactKind.INFERRED)
_CONFLICT_NOTE = "存在未决冲突，待核验（宣传表述与可靠来源表述并列保留，未取平均、未择一）"
_PARALLEL_NOTE = "多来源取值并列保留，未取平均、未择一（取值存在差异，待核验）"
_MARKETING_NOTE = "仅宣传性表述，未经可靠来源证实（待核验）"
_AS_OF_RE = re.compile(r"(\d{4})[-/年](\d{1,2})[-/月](\d{1,2})")

# 列表型字段与续航字段（与 product_extractor.LIST_FIELDS/STRUCTURED_FIELDS 同步）
_LIST_FIELDS = frozenset({
    "core_functions", "audio_formats", "variants", "accessories_fit",
    "special_features", "marketing_claims", "common_praise", "common_complaints",
})
_BATTERY_FIELD = "battery_by_mode"
_IDENTITY_FIELDS = ("canonical_id", "aliases", "product_name", "brand", "model")

# 货架展示价兜底（真实模型联调校准，2026-09-24）：货架快照 CSV 固定列序见 SPEC §5
# （第 8 列=展示价(元)，下标 7）；真实模型可能丢弃该口径（run #3/#4 实测：P004 连续
# 两轮只写渠道报价，丢失「展示价456元」，决策层拿不到展示价口径）。
_LISTING_SOURCE_PREFIX = "01_Ecommerce_Listings"
_LISTING_PRICE_COLUMN = 7
_NUMERIC_RE = re.compile(r"\d[\d,]*(?:\.\d+)?")


def _backfill_listing_price(draft: dict, source_records: list) -> dict:
    """货架展示价缺失时的确定性补录（值与证据逐字取自货架快照行，不引入资料外信息）。

    真实模型联调实测（2026-09-24）：模型可能丢弃货架「展示价(元)」列（如 P004 的
    456 元只留渠道报价），导致预算/价格比较器没有展示价口径。货架快照是确定性
    输入数据（SPEC §5 归一属确定性代码，不用模型），据此直接补一条观察再参与
    归并；模型已给出该价格（任一观察含该数字）时不补，mock 夹具全部自带、不受影响。
    """
    if not isinstance(draft, dict):
        return draft
    existing = draft.get("current_price") or []
    existing_text = json.dumps(existing, ensure_ascii=False, default=str)
    for record in source_records or []:
        source_id = str(getattr(record, "source_id", ""))
        if not source_id.startswith(_LISTING_SOURCE_PREFIX):
            continue
        row_text = str(getattr(record, "original_text", ""))
        rows = list(csv.reader([row_text]))
        if not rows or len(rows[0]) <= _LISTING_PRICE_COLUMN:
            continue
        price_text = rows[0][_LISTING_PRICE_COLUMN].strip()
        if not price_text or not _NUMERIC_RE.fullmatch(price_text):
            continue
        if re.search(rf"(?<!\d){re.escape(price_text)}(?!\d)", existing_text):
            continue  # 模型已给该价格口径，不重复补录
        span_id = record.spans[0][0] if getattr(record, "spans", None) else "L1"
        number = float(price_text.replace(",", ""))
        observation = {
            "raw_value": f"展示价{price_text}元",
            "normalized_value": int(number) if number == int(number) else number,
            "unit": "元",
            "fact_kind": "实测观察",
            "status": "有支持",
            "conditions": ["货架快照「展示价(元)」列直接读取（模型抽取缺失，程序按来源行补录）"],
            "applicable_variant": None,
            "as_of": None,
            "evidence_refs": [{"source_id": source_id, "span_id": span_id,
                               "exact_quote": price_text}],
        }
        draft = dict(draft)
        draft["current_price"] = [*existing, observation]
        logger.warning("产品 %s 的货架展示价缺失（模型抽取遗漏），已从货架快照行补录：%s",
                       draft.get("canonical_id"), observation["raw_value"])
        break
    return draft


# ---------------------------------------------------------------------------
# 契约入口
# ---------------------------------------------------------------------------

def merge_fact_cells(cells: list) -> FactCell:
    """合并同字段的多个 FactCell（契约签名；冲突三步法的落点）。

    :param cells: 同一 field_key 的 FactCell/dict 列表（可为空）
    :return: 合并后的单个 FactCell；多组可并存条件时 status=附条件，
             conditions 携带各条件；真冲突不可解释时 status=存在冲突
             且取值文本并列两组表述 + 条件注明「待核验」
    """
    normalized = [_as_fact_cell(c) for c in cells or []]
    normalized = [c for c in normalized if c is not None]
    normalized = _dedupe(normalized)
    if not normalized:
        return FactCell(field_key=normalized_field_key(cells), raw_value=None,
                        status=FactStatus.MISSING)
    if len(normalized) == 1:
        cell = normalized[0]
        if cell.fact_kind == FactKind.MARKETING and not isinstance(cell.raw_value, list):
            # 单条宣传观察（非列表型字段）：不当事实，落成待核验型（raw=None+条件说明）
            return _marketing_only([cell], [cell])
        return _copy_cell(cell)

    groups: dict[str, list[FactCell]] = {}
    order: list[str] = []
    for cell in normalized:
        variant = cell.applicable_variant or ""
        if variant not in groups:
            groups[variant] = []
            order.append(variant)
        groups[variant].append(cell)

    if len(groups) == 1:
        return _merge_same_variant(next(iter(groups.values())))

    # 三步法①：不同模式/变体（applicable_variant 不同）≠ 冲突 → 并列保留
    merged_parts: list[FactCell] = [
        _merge_same_variant(groups[variant]) for variant in order]
    has_value = [c for c in merged_parts if c.raw_value is not None]
    valueless = [c for c in merged_parts if c.raw_value is None]
    raw_segments = []
    conditions: list[str] = []
    for cell in merged_parts:
        variant_label = cell.applicable_variant or "未区分模式"
        if cell.raw_value is not None:
            segment = f"{variant_label}：{cell.raw_value}"
            if cell.conditions:
                segment += f"（{'；'.join(cell.conditions)}）"
            raw_segments.append(segment)
        conditions.extend(f"{variant_label}：{c}" for c in cell.conditions)
    base = has_value[0] if has_value else valueless[0]
    kind = _top_kind(merged_parts)
    statuses = {c.status for c in merged_parts}
    status = (FactStatus.CONDITIONAL if statuses <= {FactStatus.SUPPORTED, FactStatus.CONDITIONAL}
              else max(statuses, key=lambda s: [k for k in FactStatus].index(s)))
    return FactCell(
        field_key=base.field_key,
        raw_value="；".join(raw_segments) if raw_segments else None,
        normalized_value=None,
        unit=base.unit,
        fact_kind=kind,
        status=status,
        conditions=conditions,
        applicable_variant=None,
        as_of=_latest_as_of(merged_parts),
        evidence_refs=_union_evidence(merged_parts),
    )


def reconcile_product(product: dict, source_records: list, config: dict) -> dict:
    """产品对象草稿 → 终稿（字段级归并 + 身份落位，契约签名）。

    :param product: extract_products 产出的产品对象草稿
    :param source_records: 该产品的全部来源记录（仅用于缺口登记语境；归并本身
        只依赖草稿内的 FactCell，不向终稿泄露原文）
    :param config: resolve_config 产出的配置
    :return: 产品对象终稿（key 集合与 field_catalog.product 完全一致）
    """
    catalog = _product_catalog()
    draft = _backfill_listing_price(product or {}, source_records)
    final: dict = {}

    final["canonical_id"] = draft.get("canonical_id")
    aliases = [str(a) for a in (draft.get("aliases") or [])]
    final["aliases"] = sorted({a.strip() for a in aliases if a.strip()})
    for key in ("product_name", "brand", "model"):
        final[key] = draft.get(key)

    identity_and_list = set(_IDENTITY_FIELDS)
    for field_key, _required in catalog:
        if field_key in identity_and_list:
            continue  # 身份字段已在上方落位（registry 权威，非观察列表）
        observations = draft.get(field_key, []) or []
        if field_key == _BATTERY_FIELD:
            final[field_key] = _merge_battery(observations)
        elif field_key in _LIST_FIELDS:
            final[field_key] = _merge_list_items(observations)
        else:
            merged = merge_fact_cells(_as_cells(observations))
            merged.field_key = field_key
            final[field_key] = merged.to_dict()
    return final


def find_coverage_gaps(products: dict[str, dict], registry, *,
                       include_optional: bool = False) -> list[dict]:
    """反向覆盖检查：字段缺口清单（契约签名）。

    :param products: {canonical_id: 产品对象终稿}
    :param registry: ProductRegistry（产品全集基准）
    :param include_optional: 兼容扩展（审计修复 2026-09，不影响缺省行为）：
        True 时把非必填字段缺口一并计入返回，且每个缺口 dict 附 ``required``
        布尔键；缺省 False 与契约一致（仅必须字段缺口，dict 不含 ``required`` 键）。
    :return: [{"canonical_id": ..., "field_key": ..., "gap": "缺失|冲突|条件未确认"}, ...]
        产品整体缺失时 field_key=None、gap="缺失"
    """
    catalog = _product_catalog()
    gaps: list[dict] = []
    for record in registry.all():
        cid = record.canonical_id
        obj = (products or {}).get(cid)
        if obj is None:
            gap = {"canonical_id": cid, "field_key": None, "gap": "缺失"}
            if include_optional:
                gap["required"] = True  # 产品整体缺失按必须处理（必须重查）
            gaps.append(gap)
            continue
        for field_key, required in catalog:
            if not required and not include_optional:
                continue
            gap = _field_gap(obj.get(field_key), field_key)
            if gap:
                entry = {"canonical_id": cid, "field_key": field_key, "gap": gap}
                if include_optional:
                    entry["required"] = bool(required)
                gaps.append(entry)
    return gaps


# ---------------------------------------------------------------------------
# 归并内核
# ---------------------------------------------------------------------------

def _merge_same_variant(cells: list[FactCell]) -> FactCell:
    """同字段同变体（同 applicable_variant）的多来源归并（三步法②③）。

    判定分工（确定性，不用模型直觉）：
    - 宣传表述 vs 可靠来源取值矛盾 → ③保留冲突标待核验（status=存在冲突）；
    - 可靠来源之间取值不同 → ②并列保留（含各自条件），禁止取平均/择一；
    - 可靠来源之间取值一致 → 有支持/附条件（kind 取官网声明优先，不升级实测）。
    """
    reliable = [c for c in cells if c.fact_kind != FactKind.MARKETING]
    marketing = [c for c in cells if c.fact_kind == FactKind.MARKETING]
    conditions = _union_conditions(cells)
    evidence = _union_evidence(cells)
    if not reliable:
        return _marketing_only(cells, marketing)

    valued: dict[str, list[FactCell]] = {}
    for cell in reliable:
        if cell.raw_value is not None:
            valued.setdefault(str(cell.raw_value), []).append(cell)

    if not valued:
        # 可靠来源均为「待核验型」观察（无统一口径）
        claims = [str(m.raw_value) for m in marketing if m.raw_value is not None]
        conditions = conditions + [f"「{c}」——{_MARKETING_NOTE}" for c in claims]
        if not conditions:
            conditions = ["资料无统一口径（待核验）"]
        return FactCell(field_key=cells[0].field_key, raw_value=None,
                        normalized_value=None, unit=None,
                        fact_kind=_kind_for_value(reliable),
                        status=FactStatus.CONFLICTED if _any_conflicted(cells)
                        else FactStatus.CONDITIONAL,
                        conditions=conditions,
                        applicable_variant=cells[0].applicable_variant,
                        as_of=_latest_as_of(cells), evidence_refs=evidence)

    if len(valued) == 1:
        base_value = next(iter(valued))
        base_cells = valued[base_value]
        status = (FactStatus.CONDITIONAL
                  if any(c.status == FactStatus.CONDITIONAL for c in cells)
                  else FactStatus.SUPPORTED)
        raw_value = base_value
        conflicts = _marketing_conflicts(marketing, base_value)
        if conflicts:
            # 三步法③：渠道/厂商宣称与可靠取值不一致 → 并列 + 待核验，不择一
            status = FactStatus.CONFLICTED
            raw_value = f"{base_value}；渠道宣称：{'；'.join(conflicts)}"
            conditions = conditions + [_CONFLICT_NOTE]
        return FactCell(field_key=cells[0].field_key, raw_value=raw_value,
                        normalized_value=_first_norm(base_cells),
                        unit=_first_non_null((c.unit for c in cells)),
                        fact_kind=_kind_for_value(base_cells), status=status,
                        conditions=conditions,
                        applicable_variant=cells[0].applicable_variant,
                        as_of=_latest_as_of(cells), evidence_refs=evidence)

    # 可靠来源取值不同 → 三步法②：并列保留（各值条件内联），不取平均、不择一
    ordered_groups = sorted(valued.values(), key=lambda g: -_top_authority(g))
    segments = []
    for group in ordered_groups:
        value = str(group[0].raw_value)
        group_conds = _union_conditions(group)
        segments.append(value + (f"（{'；'.join(group_conds)}）" if group_conds else ""))
    return FactCell(field_key=cells[0].field_key, raw_value="；".join(segments),
                    normalized_value=None,
                    unit=_first_non_null((c.unit for c in cells)),
                    fact_kind=_kind_for_value([c for g in valued.values() for c in g]),
                    status=FactStatus.CONDITIONAL,
                    conditions=conditions + [_PARALLEL_NOTE],
                    applicable_variant=cells[0].applicable_variant,
                    as_of=_latest_as_of(cells), evidence_refs=evidence)


def _marketing_only(cells: list[FactCell], marketing: list[FactCell]) -> FactCell:
    """仅宣传表述（无可靠来源）的归并：raw_value=None + 附条件（渲染为「待核验」）。

    宣传原文保留进 conditions 与 marketing_claims 字段，不当产品事实（SPEC §7/§8）。
    """
    claims = [str(m.raw_value) for m in marketing if m.raw_value is not None]
    conditions = _union_conditions(cells)
    conditions = conditions + [f"「{c}」——{_MARKETING_NOTE}" for c in claims]
    if not conditions:
        conditions = ["资料无统一口径（待核验）"]
    return FactCell(field_key=cells[0].field_key, raw_value=None,
                    normalized_value=None, unit=None,
                    fact_kind=FactKind.MARKETING if marketing else cells[0].fact_kind,
                    status=FactStatus.CONFLICTED if _any_conflicted(cells)
                    else FactStatus.CONDITIONAL,
                    conditions=conditions,
                    applicable_variant=cells[0].applicable_variant,
                    as_of=_latest_as_of(cells), evidence_refs=_union_evidence(cells))


def _merge_battery(observations: list) -> list[dict]:
    """续航观察 → 分模式并列（三步法①：不同模式≠冲突），每模式一条 item。"""
    cells = _as_cells(observations)
    groups: dict[str, list[FactCell]] = {}
    order: list[str] = []
    for cell in cells:
        variant = cell.applicable_variant or "未区分模式"
        if variant not in groups:
            groups[variant] = []
            order.append(variant)
        groups[variant].append(cell)
    items: list[dict] = []
    for variant in order:
        merged = _merge_same_variant(groups[variant])
        merged.field_key = _BATTERY_FIELD
        items.append({
            "mode": variant,
            "duration": merged.raw_value,
            "conditions": "；".join(merged.conditions) if merged.conditions else None,
            "status": str(merged.status),
            "evidence_refs": [e.to_dict() for e in merged.evidence_refs],
        })
    return items


def _merge_list_items(observations: list) -> list[str]:
    """列表型字段观察 → 去重保序的字符串列表（条目自带出处/层级措辞）。"""
    items: list[str] = []
    for cell in _as_cells(observations):
        for item in (cell.raw_value if isinstance(cell.raw_value, list) else []):
            text = str(item).strip()
            if text and text not in items:
                items.append(text)
    return items


def _conditions_explain(groups: list[list[FactCell]]) -> bool:
    """三步法②判据：各取值组带有互不相同的非空条件（可按条件并列成立）。"""
    condition_sets = []
    for group in groups:
        conds = frozenset(_union_conditions(group))
        condition_sets.append(conds)
    non_empty = [s for s in condition_sets if s]
    if len(non_empty) < len(groups):
        return False
    return len(set(condition_sets)) == len(groups)


def _marketing_conflicts(marketing: list[FactCell], agreed_value) -> list[str]:
    """宣传表述与可靠取值不一致的宣称原文（三步法③素材）。"""
    if agreed_value is None:
        return []
    seen = []
    for cell in marketing:
        raw = cell.raw_value
        if raw is not None and str(raw) != str(agreed_value) and str(raw) not in seen:
            seen.append(str(raw))
    return seen


def _any_conflicted(cells: list[FactCell]) -> bool:
    return any(c.status == FactStatus.CONFLICTED for c in cells)


def _kind_for_value(cells: list[FactCell]) -> FactKind:
    """一致取值的 fact_kind：官网声明优先（不得自动升级为实测，SPEC §4）。"""
    kinds = {c.fact_kind for c in cells}
    for kind in _KIND_FOR_VALUE:
        if kind in kinds:
            return kind
    return cells[0].fact_kind


def _top_kind(cells: list[FactCell]) -> FactKind:
    return max((c.fact_kind for c in cells), key=lambda k: _KIND_AUTHORITY.get(k, -1))


def _top_authority(cells: list[FactCell]) -> int:
    return max(_KIND_AUTHORITY.get(c.fact_kind, -1) for c in cells)


def _dedupe(cells: list[FactCell]) -> list[FactCell]:
    seen: set = set()
    result: list[FactCell] = []
    for cell in cells:
        key = (_hashable(cell.raw_value), _hashable(cell.normalized_value),
               cell.fact_kind, cell.status, _hashable(cell.applicable_variant),
               tuple(sorted((e.source_id, e.span_id, e.exact_quote)
                            for e in cell.evidence_refs)))
        if key not in seen:
            seen.add(key)
            result.append(cell)
    return result


def _hashable(value):
    """dedupe 键的 hashable 化（raw_value 可为 list——列表型观察的 items）。"""
    if isinstance(value, list):
        return ("__list__", tuple(value))
    return value


def _union_conditions(cells: list[FactCell]) -> list[str]:
    result: list[str] = []
    for cell in cells:
        for cond in cell.conditions or []:
            text = str(cond).strip()
            if text and text not in result:
                result.append(text)
    return result


def _union_evidence(cells: list[FactCell]) -> list:
    result = []
    seen = set()
    for cell in cells:
        for ref in cell.evidence_refs or []:
            key = (ref.source_id, ref.span_id, ref.exact_quote)
            if key not in seen:
                seen.add(key)
                result.append(ref)
    return result


def _latest_as_of(cells: list[FactCell]):
    """取资料内最新时间基准（只用 as_of，禁止运行当天日期）。"""
    best, best_key = None, None
    for cell in cells:
        if not cell.as_of:
            continue
        key = _as_of_key(str(cell.as_of))
        if key is None:
            continue
        if best_key is None or key > best_key:
            best, best_key = cell.as_of, key
    if best is not None:
        return best
    return next((c.as_of for c in cells if c.as_of), None)


def _as_of_key(text: str):
    m = _AS_OF_RE.search(text)
    if not m:
        return None
    return (int(m.group(1)), int(m.group(2)), int(m.group(3)))


def _first_non_null(values):
    return next((v for v in values if v is not None), None)


def _first_norm(cells: list[FactCell]):
    return next((c.normalized_value for c in cells if c.normalized_value is not None), None)


def _copy_cell(cell: FactCell) -> FactCell:
    return FactCell(
        field_key=cell.field_key, raw_value=cell.raw_value,
        normalized_value=cell.normalized_value, unit=cell.unit,
        fact_kind=cell.fact_kind, status=cell.status,
        conditions=list(cell.conditions), applicable_variant=cell.applicable_variant,
        as_of=cell.as_of, evidence_refs=list(cell.evidence_refs))


def _as_fact_cell(cell) -> FactCell | None:
    if cell is None:
        return None
    if isinstance(cell, FactCell):
        return cell
    if isinstance(cell, dict):
        try:
            evidence = []
            for ref in cell.get("evidence_refs") or []:
                if isinstance(ref, dict):
                    evidence.append(EvidenceRef(
                        source_id=str(ref.get("source_id") or ""),
                        span_id=str(ref.get("span_id") or ""),
                        exact_quote=str(ref.get("exact_quote") or "")))
                else:
                    evidence.append(ref)
            raw_value = cell.get("raw_value")
            items = cell.get("items")
            if items is not None and raw_value is None:
                raw_value = items  # 列表型观察：items 暂存于 raw_value（list）
            return FactCell(
                field_key=str(cell.get("field_key") or ""),
                raw_value=raw_value,
                normalized_value=cell.get("normalized_value"),
                unit=cell.get("unit"),
                fact_kind=FactKind(cell.get("fact_kind") or FactKind.OFFICIAL_CLAIM.value),
                status=FactStatus(cell.get("status") or FactStatus.SUPPORTED.value),
                conditions=list(cell.get("conditions") or []),
                applicable_variant=cell.get("applicable_variant"),
                as_of=cell.get("as_of"),
                evidence_refs=evidence)
        except ValueError as exc:
            logger.warning("非法 FactCell 观察被丢弃: %s", exc)
            return None
    logger.warning("无法识别的观察类型被丢弃: %s", type(cell).__name__)
    return None


def _as_cells(observations) -> list[FactCell]:
    return [c for c in (_as_fact_cell(o) for o in observations or []) if c is not None]


def normalized_field_key(cells) -> str:
    for cell in cells or []:
        if isinstance(cell, FactCell):
            return cell.field_key
        if isinstance(cell, dict) and cell.get("field_key"):
            return str(cell["field_key"])
    return ""


def _field_gap(value, field_key: str):
    """单个必填字段的缺口判定（缺失|冲突|条件未确认；无缺口返回 None）。"""
    if field_key == _BATTERY_FIELD:
        items = value if isinstance(value, list) else []
        if not items:
            return "缺失"
        if all(not isinstance(i, dict) or i.get("duration") is None for i in items):
            return "条件未确认"
        return None
    if field_key in _LIST_FIELDS:
        return "缺失" if not (isinstance(value, list) and value) else None
    if value is None:
        return "缺失"
    if isinstance(value, dict):
        status = value.get("status")
        if status == FactStatus.CONFLICTED.value:
            return "冲突"
        if status == FactStatus.CONDITIONAL.value and value.get("raw_value") is None:
            return "条件未确认"  # 资料有表述但无统一口径（待核验）
        if status == FactStatus.MISSING.value or (
                value.get("raw_value") is None and value.get("items") is None):
            return "缺失"
    return None


_CATALOG_CACHE: list[tuple[str, bool]] | None = None


def _product_catalog() -> list[tuple[str, bool]]:
    """field_catalog.product 的 (字段键, required) 列表（缓存）。"""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        try:
            catalog = json.loads(Path(FIELD_CATALOG_PATH).read_text(encoding="utf-8"))
            pairs = [(f["key"], bool(f.get("required")))
                     for group in catalog["product"]["groups"] for f in group["fields"]]
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise GatewayError(f"field_catalog.json 读取失败: {exc}") from exc
        _CATALOG_CACHE = pairs
    return list(_CATALOG_CACHE)
