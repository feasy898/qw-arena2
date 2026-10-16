# -*- coding: utf-8 -*-
"""src/source_index.py — 来源登记与检索（确定性代码，不用模型）。

对 load_input 产出的 SourceRecord 列表建立索引，供产品抽取按
信息源类型或产品编号取数。编号检索接受任意别名（P01/P001，按
contracts/rules.json 归一）。
"""
from __future__ import annotations

from typing import Optional

from src.product_registry import normalize_product_id, record_product_ids
from src.schemas import SourceRecord, SourceType


class SourceIndex:
    """SourceRecord 只读索引。"""

    def __init__(self, records: list[SourceRecord]) -> None:
        """构建索引。

        :param records: input_adapter.load_input 产出的全部来源记录
        """
        self._records: list[SourceRecord] = []
        self._by_type: dict[SourceType, list[SourceRecord]] = {}
        self._by_product: dict[str, list[SourceRecord]] = {}
        for record in records:
            self._records.append(record)
            self._by_type.setdefault(record.source_type, []).append(record)
            for canonical_id in record_product_ids(record):
                self._by_product.setdefault(canonical_id, []).append(record)

    def by_type(self, source_type: SourceType) -> list[SourceRecord]:
        """按信息源类型取记录（保持装载顺序）。"""
        return list(self._by_type.get(source_type, []))

    def by_product(self, product_ref: str) -> list[SourceRecord]:
        """按产品编号（任意别名写法）取记录。

        仅对明确的编号字段/文件名编号归一（P01 ≡ P001，尾号对齐）；
        product_ref 不是合法编号时返回空列表，不抛错。

        :param product_ref: 如 "P01"、"P001"
        """
        canonical_id = normalize_product_id(product_ref)
        if canonical_id is None:
            return []
        return list(self._by_product.get(canonical_id, []))

    def user_description(self) -> Optional[SourceRecord]:
        """返回用户描述记录（多份时返回装载序首个）；无则 None。"""
        records = self._by_type.get(SourceType.USER_DESCRIPTION, [])
        return records[0] if records else None
