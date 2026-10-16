# -*- coding: utf-8 -*-
"""src/product_registry.py — 产品清单与编号归一（确定性代码，不用模型）。

规则（contracts/rules.json id_normalization）：
- 仅对明确的产品编号字段/文件名编号归一：P01 ≡ P001，尾号对齐；
- canonical_form = P + 3 位数字；原始写法全部保留为 original_ids 别名；
- 品牌与型号脱敏名原样沿用，不做模糊匹配、不删后缀、不还原真实品牌；
- 产品全集 = 官网 ∪ 货架 ∪ 报道 ∪ 实测；「同编号不同型号」= 身份冲突，登记不合并。

实现约定（确定性，均在本模块内固化）：
- 型号来源：官网文件名 P###_<型号>_Official_Site.txt 的 <型号>、货架 CSV「型号」列、
  反馈 CSV「型号」列；品牌来源：货架 CSV「品牌」列。报道/实测文件名仅提供编号。
- model_raw 取值优先级：官网文件名 > 货架 CSV > 反馈 CSV（同优先级取装载序首个）；
  全部候选原文保留进身份冲突判定的 detail，任何文本都不改写。
- 身份冲突判定（仅用于决定「是否登记冲突」，不合并、不改写文本）：
    型号：去除全部空白并 casefold 后（下称「身份形」），两候选互不为子串 → 冲突。
      依据：货架「型号」列常含 SKU 前后缀（如「SR-210 / Ralun Pro」vs 官网文件名
      「RalunPro」），严格相等会把一致的样例数据误报为冲突；子串判定不修改任何文本。
    品牌：身份形严格相等，不等即冲突（品牌短，不做子串放宽）。
- 反馈 CSV 不参与产品全集（registry_scope），仅为其已入册产品补充别名与型号候选。
- 编号写法不合法（非 P+1..3 位数字）的记录不参与登记（无警告通道，下游覆盖检查兜底）。
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from typing import Optional

from src.input_adapter import (
    COL_BRAND,
    COL_MODEL,
    COL_PRODUCT_ID,
    COVERAGE_FILENAME_RE,
    OFFICIAL_FILENAME_RE,
    REVIEW_FILENAME_RE,
    csv_cells,
    read_csv_rows,
)
from src.schemas import ProductRecord, SourceRecord, SourceType

_IDENTITY_RE = re.compile(r"^[Pp](\d{1,3})$")
_WHITESPACE_RE = re.compile(r"\s+")

# 型号取值优先级（数值小者优先）
_MODEL_PRIORITY: dict[SourceType, int] = {
    SourceType.OFFICIAL_SITE: 0,
    SourceType.ECOMMERCE_LISTING: 1,
    SourceType.USER_FEEDBACK: 2,
}


def normalize_product_id(raw: str) -> Optional[str]:
    """将明确的产品编号写法归一为三位规范形。

    :param raw: 如 "P01"、"P001"、"P12"（容忍首尾空白与大小写 p/P）
    :return: 规范形（"P001"）；非编号写法返回 None（不猜）
    """
    if not isinstance(raw, str):
        return None
    m = _IDENTITY_RE.match(raw.strip())
    if not m:
        return None
    return f"P{int(m.group(1)):03d}"


def _identity_form(text: str) -> str:
    """身份比较形：去除全部空白 + casefold。仅用于冲突判定，不改写任何原文。"""
    return _WHITESPACE_RE.sub("", text).casefold()


def _basename(record: SourceRecord) -> str:
    if record.path:
        return os.path.basename(os.fspath(record.path))
    return record.source_id.rsplit("/", 1)[-1]


@dataclass
class _Candidate:
    """一个型号/品牌候选原文及其出处。"""

    value: str
    source_type: SourceType
    source_id: str


@dataclass
class _ProductAcc:
    """构建期按 canonical_id 聚合的标识信息。"""

    aliases: set[str] = field(default_factory=set)
    brands: list[_Candidate] = field(default_factory=list)
    models: list[_Candidate] = field(default_factory=list)


class ProductRegistry:
    """产品全集登记表（canonical_id 升序为稳定顺序）。"""

    def __init__(self, products: list[ProductRecord], identity_conflicts: list[dict]) -> None:
        """不直接构造：请经 build_registry()。

        :param products: 仅填充基础标识的 ProductRecord 列表
        :param identity_conflicts: 身份冲突清单（如 {"canonical_id": "P001",
            "conflict": "同编号不同型号", "detail": [...]}），不静默合并
        """
        index: dict[str, ProductRecord] = {}
        for product in products:
            if product.canonical_id in index:
                raise ValueError(
                    f"canonical_id 重复：{product.canonical_id}（应经 build_registry 构建）"
                )
            index[product.canonical_id] = product
        self._index = index
        self._products = sorted(products, key=lambda p: p.canonical_id)
        self.identity_conflicts = list(identity_conflicts)

    def get(self, canonical_id: str) -> ProductRecord:
        """按规范编号取产品；缺失 raise KeyError。"""
        return self._index[canonical_id]

    def all(self) -> list[ProductRecord]:
        """全部产品（canonical_id 升序）。"""
        return list(self._products)

    def ids(self) -> list[str]:
        """全部规范编号（升序）。"""
        return [product.canonical_id for product in self._products]


def _filename_ref(record: SourceRecord) -> Optional[tuple[str, Optional[str]]]:
    """从报道/实测/官网文件名解析 (编号原始写法, 型号或 None)。"""
    name = _basename(record)
    if record.source_type is SourceType.OFFICIAL_SITE:
        m = OFFICIAL_FILENAME_RE.match(name)
        if m:
            return (f"P{m.group(1)}", m.group(2))
        return None
    if record.source_type is SourceType.MEDIA_COVERAGE:
        m = COVERAGE_FILENAME_RE.match(name)
        if m:
            return (f"P{m.group(1)}", None)
        return None
    if record.source_type is SourceType.THIRD_PARTY_REVIEW:
        m = REVIEW_FILENAME_RE.match(name)
        if m:
            return (f"P{m.group(1)}", None)
        return None
    return None


def _csv_row_values(
    record: SourceRecord, header_cache: dict[str, list[str]]
) -> Optional[tuple[str, str, str]]:
    """CSV 行记录 → (编号原始写法, 品牌, 型号)；缺列/无 path 时返回 None。"""
    if not record.path:
        return None
    key = os.fspath(record.path)
    header = header_cache.get(key)
    if header is None:
        header, _rows = read_csv_rows(record.path)
        header_cache[key] = header
    cells = csv_cells(record.original_text)
    if COL_PRODUCT_ID not in header:
        return None

    def _cell(column: str) -> str:
        if column not in header:
            return ""
        idx = header.index(column)
        return cells[idx] if idx < len(cells) else ""

    return (_cell(COL_PRODUCT_ID), _cell(COL_BRAND), _cell(COL_MODEL))


def _add_candidate(
    store: list[_Candidate], value: str, record: SourceRecord
) -> None:
    if value and value.strip():
        store.append(_Candidate(value, record.source_type, record.source_id))


def _distinct(candidates: list[_Candidate]) -> list[_Candidate]:
    """按身份形去重（保留首个出处）。"""
    seen: dict[str, _Candidate] = {}
    for cand in candidates:
        key = _identity_form(cand.value)
        if key not in seen:
            seen[key] = cand
    return list(seen.values())


def _conflict_detail(candidates: list[_Candidate]) -> list[dict[str, str]]:
    return [
        {"source_type": str(c.source_type), "source_id": c.source_id, "value": c.value}
        for c in candidates
    ]


def _detect_conflicts(
    canonical_id: str, acc: _ProductAcc
) -> list[dict]:
    """返回该产品的身份冲突登记项（同编号不同型号 / 同编号不同品牌）。"""
    conflicts: list[dict] = []
    models = _distinct(acc.models)
    if len(models) >= 2:
        forms = [_identity_form(c.value) for c in models]
        incompatible = any(
            not (a in b or b in a)
            for i, a in enumerate(forms)
            for j, b in enumerate(forms)
            if i < j
        )
        if incompatible:
            conflicts.append(
                {
                    "canonical_id": canonical_id,
                    "conflict": "同编号不同型号",
                    "detail": _conflict_detail(models),
                }
            )
    brands = _distinct(acc.brands)
    if len(brands) >= 2:
        conflicts.append(
            {
                "canonical_id": canonical_id,
                "conflict": "同编号不同品牌",
                "detail": _conflict_detail(brands),
            }
        )
    return conflicts


def _model_raw(acc: _ProductAcc) -> Optional[str]:
    """按 官网 > 货架 > 反馈 优先级取型号原文（不改写；同优先级取装载序首个）。"""
    if not acc.models:
        return None
    best = min(
        enumerate(acc.models),
        key=lambda pair: (_MODEL_PRIORITY.get(pair[1].source_type, 99), pair[0]),
    )
    return best[1].value


def record_product_ids(record: SourceRecord) -> list[str]:
    """单条来源记录直接关联的产品规范编号（确定性；无关联返回 []）。

    - 报道/实测/官网：文件名编号（P##/P###）；
    - 货架/反馈 CSV 行：「测试产品ID」列（须能经 record.path 读到表头定位列序）；
    - 用户描述：不关联产品。
    编号写法不合法或无法定位编号列 → []（不猜）。
    """
    if record.source_type is SourceType.USER_DESCRIPTION:
        return []
    if record.source_type in (
        SourceType.MEDIA_COVERAGE,
        SourceType.THIRD_PARTY_REVIEW,
        SourceType.OFFICIAL_SITE,
    ):
        ref = _filename_ref(record)
        if ref is None:
            return []
        cid = normalize_product_id(ref[0])
        return [cid] if cid else []
    # CSV 行记录
    if not record.path:
        return []
    header, _rows = read_csv_rows(record.path)
    if COL_PRODUCT_ID not in header:
        return []
    idx = header.index(COL_PRODUCT_ID)
    cells = csv_cells(record.original_text)
    if idx >= len(cells):
        return []
    cid = normalize_product_id(cells[idx])
    return [cid] if cid else []


def build_registry(records: list[SourceRecord]) -> ProductRegistry:
    """从来源记录构建产品全集（确定性，不用模型）。

    编号来源：货架 CSV 测试产品ID 列、反馈 CSV 测试产品ID 列、官网文件名 P###、
    报道/实测文件名 P##。同编号不同型号 → 身份冲突登记（detail 含各来源型号写法）。

    :param records: 全部来源记录
    :return: ProductRegistry（产品仅填充 canonical_id/original_ids/brand_raw/model_raw，
        fields 留空；产品全集 = 官网 ∪ 货架 ∪ 报道 ∪ 实测，反馈仅补充别名/型号候选）
    """
    # 稳定处理顺序：与装载顺序无关，按 (source_type, 文件, 行号) 排序
    def _sort_key(rec: SourceRecord) -> tuple[str, str, int]:
        file_part, _, row_part = rec.source_id.partition("#row")
        row_num = int(row_part) if row_part.isdigit() else -1
        return (str(rec.source_type), file_part, row_num)

    ordered = sorted(records, key=_sort_key)
    accs: dict[str, _ProductAcc] = {}
    header_cache: dict[str, list[str]] = {}
    feedback_records: list[SourceRecord] = []

    def _acc_for(cid: str) -> _ProductAcc:
        return accs.setdefault(cid, _ProductAcc())

    # 第一遍：产品全集四源（官网 ∪ 货架 ∪ 报道 ∪ 实测）
    for record in ordered:
        if record.source_type in (SourceType.MEDIA_COVERAGE, SourceType.THIRD_PARTY_REVIEW, SourceType.OFFICIAL_SITE):
            ref = _filename_ref(record)
            if ref is None:
                continue
            raw_id, model = ref
            cid = normalize_product_id(raw_id)
            if cid is None:
                continue
            acc = _acc_for(cid)
            acc.aliases.add(raw_id)
            if model:
                _add_candidate(acc.models, model, record)
        elif record.source_type is SourceType.ECOMMERCE_LISTING:
            values = _csv_row_values(record, header_cache)
            if values is None:
                continue
            raw_id, brand, model = values
            cid = normalize_product_id(raw_id)
            if cid is None:
                continue
            acc = _acc_for(cid)
            acc.aliases.add(raw_id)
            _add_candidate(acc.brands, brand, record)
            _add_candidate(acc.models, model, record)
        elif record.source_type is SourceType.USER_FEEDBACK:
            feedback_records.append(record)

    # 第二遍：反馈 CSV 仅补充别名与型号候选（不创建产品；编号不合法或未入册 → 忽略）
    for record in feedback_records:
        values = _csv_row_values(record, header_cache)
        if values is None:
            continue
        raw_id, _brand, model = values
        cid = normalize_product_id(raw_id)
        if cid is None or cid not in accs:
            continue
        acc = accs[cid]
        acc.aliases.add(raw_id)
        _add_candidate(acc.models, model, record)

    products: list[ProductRecord] = []
    conflicts: list[dict] = []
    for cid in sorted(accs):
        acc = accs[cid]
        conflicts.extend(_detect_conflicts(cid, acc))
        brand_raw = acc.brands[0].value if acc.brands else None
        products.append(
            ProductRecord(
                canonical_id=cid,
                original_ids=sorted(acc.aliases),
                brand_raw=brand_raw,
                model_raw=_model_raw(acc),
                fields={},
            )
        )
    return ProductRegistry(products, conflicts)
