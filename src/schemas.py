# -*- coding: utf-8 -*-
"""src/schemas.py — SPEC §4 数据契约的 dataclass 完整定义。

唯一权威为 SPEC.md §4；字段含义见 contracts/interfaces.md §1，
业务规则见 contracts/rules.json，字段目录见 contracts/field_catalog.json。

所有类型只依赖标准库；枚举均为 str 子类，可直接 JSON 序列化。
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any, Optional


class _StrEnum(str, Enum):
    """str 枚举基类：json.dumps 直接输出其字符串值。"""

    def __str__(self) -> str:  # noqa: D105
        return str(self.value)


class SourceType(_StrEnum):
    """信息源类型。SPEC §4 约定五源为产品信息源；用户描述为画像唯一来源，单独列出。"""

    USER_DESCRIPTION = "00_User_Descriptions"
    ECOMMERCE_LISTING = "01_Ecommerce_Listings"
    MEDIA_COVERAGE = "02_Media_Coverage_and_Reviews/Product_Coverage"
    THIRD_PARTY_REVIEW = "02_Media_Coverage_and_Reviews/Third_Party_Review"
    USER_FEEDBACK = "03_User_Feedback_and_Complaints"
    OFFICIAL_SITE = "04_Brand_Official_Sites"


class FactKind(_StrEnum):
    """事实类型（SPEC §4：kind 与 status 必须分开）。"""

    OFFICIAL_CLAIM = "官网声明"
    MEASURED = "实测观察"
    USER_FEEDBACK = "用户反馈"
    MARKETING = "宣传表述"
    INFERRED = "有依据归纳"


class FactStatus(_StrEnum):
    """事实状态。"""

    SUPPORTED = "有支持"
    CONDITIONAL = "附条件"
    CONFLICTED = "存在冲突"
    MISSING = "输入缺失"


class NeedType(_StrEnum):
    """用户需求类型。"""

    HARD = "硬约束"
    SOFT = "软偏好"
    GOAL = "目标"
    AMBIGUOUS = "歧义"


class ConstraintOutcome(_StrEnum):
    """约束判定四态（contracts/rules.json constraint_states：UNKNOWN≠PASS）。"""

    PASS = "PASS"
    FAIL = "FAIL"
    UNKNOWN = "UNKNOWN"
    NOT_APPLICABLE = "NOT_APPLICABLE"


@dataclass
class EvidenceRef:
    """证据引用：指向 SourceRecord 的稳定片段。"""

    source_id: str
    span_id: str
    exact_quote: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class SourceRecord:
    """一条输入资料记录（一个文件/一条评价）。

    spans 为稳定片段编号列表（[(span_id, 起始偏移, 结束偏移), ...]），供 EvidenceRef 定位。
    """

    source_id: str
    source_type: SourceType
    original_text: str
    spans: list[tuple[str, int, int]] = field(default_factory=list)
    path: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["source_type"] = str(self.source_type)
        d["spans"] = [list(s) for s in self.spans]
        return d


@dataclass
class FactCell:
    """产品属性表的一个事实单元（field_catalog 中一个子字段的取值载体）。

    kind 与 status 必须分开：「官网写续航10h」是有支持的官网声明，
    不得自动升级为实测值（SPEC §4）。
    """

    field_key: str
    raw_value: str
    normalized_value: Any = None
    unit: Optional[str] = None
    fact_kind: FactKind = FactKind.OFFICIAL_CLAIM
    status: FactStatus = FactStatus.SUPPORTED
    conditions: list[str] = field(default_factory=list)
    applicable_variant: Optional[str] = None
    as_of: Optional[str] = None  # 输入资料内的时间基准，禁止用运行当天日期
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["fact_kind"] = str(self.fact_kind)
        d["status"] = str(self.status)
        d["evidence_refs"] = [e.to_dict() for e in self.evidence_refs]
        return d


@dataclass
class UserNeed:
    """原子化用户需求。

    operator 为可执行判定的算子（如 "<=", ">=", "==", "contains", "in"），
    expected_value 为判定目标值；硬约束准入纪律见 contracts/rules.json。
    """

    need_id: str
    original_expression: str
    need_type: NeedType
    operator: Optional[str] = None
    expected_value: Any = None
    priority: int = 0  # 数值越大优先级越高；0=未明确
    evidence_refs: list[EvidenceRef] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["need_type"] = str(self.need_type)
        d["evidence_refs"] = [e.to_dict() for e in self.evidence_refs]
        return d


@dataclass
class ProductRecord:
    """一款产品的登记与归并结果。

    canonical_id 为三位规范形（P001）；original_ids 保留全部原始写法（P01 等别名）。
    brand_raw/model_raw 为脱敏名原样；fields 的 key 取 contracts/field_catalog.json
    产品子字段 key（如 "battery_by_mode"），值为 FactCell。
    """

    canonical_id: str
    original_ids: list[str] = field(default_factory=list)
    brand_raw: Optional[str] = None
    model_raw: Optional[str] = None
    fields: dict[str, FactCell] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "canonical_id": self.canonical_id,
            "original_ids": list(self.original_ids),
            "brand_raw": self.brand_raw,
            "model_raw": self.model_raw,
            "fields": {k: v.to_dict() for k, v in self.fields.items()},
        }


@dataclass
class ConstraintResult:
    """一个（产品×需求）的约束判定。

    profile_reference / attribute_reference 使用 citation_format：
    「画像｜字段组｜字段名」与「产品属性｜P001｜字段名」，指向前两份文档内字段。
    """

    product_id: str
    need_id: str
    result: ConstraintOutcome
    profile_reference: Optional[str] = None
    attribute_reference: Optional[str] = None
    explanation: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["result"] = str(self.result)
        return d


@dataclass
class RecommendationCard:
    """推荐卡（SPEC §9 固定字段）。"""

    level: str  # 首选 | 备选1 | 备选2 | （异常路径空位说明行）
    product_id: str
    product_name: str
    brand: str
    profile_match: list[str] = field(default_factory=list)   # 命中的画像需求（含画像引用）
    fact_references: list[str] = field(default_factory=list)  # 产品属性引用（citation_format）
    tradeoffs: str = ""                                       # 相对其他有效候选的得/失
    costs_risks: str = ""                                     # 代价与风险；资料无据时写证据边界
    pre_purchase_checks: list[str] = field(default_factory=list)  # 购买前核验
    switch_conditions: list[str] = field(default_factory=list)    # 改选条件

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ExclusionRecord:
    """未选原因行（五类原因见 contracts/rules.json；同因产品可合并为一行）。"""

    product_ids: list[str] = field(default_factory=list)
    reason_category: str = ""  # 硬约束不符|硬约束证据不足|软需求匹配弱|同条件下被压过|无足够新增价值
    reason_detail: str = ""    # 落到产品属性具体字段的冲突点
    change_conditions: list[str] = field(default_factory=list)  # 条件句，须与排除原因对应

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class DecisionPlan:
    """推荐决策计划。selected_products 与 recommendation_cards 行序一致（行序即优先次序）。"""

    selected_products: list[str] = field(default_factory=list)  # canonical_id，≤3，行序即优先次序
    recommendation_cards: list[RecommendationCard] = field(default_factory=list)
    excluded_products: list[ExclusionRecord] = field(default_factory=list)
    overall_conclusion: str = ""  # 表后一句综合结论

    def to_dict(self) -> dict[str, Any]:
        return {
            "selected_products": list(self.selected_products),
            "recommendation_cards": [c.to_dict() for c in self.recommendation_cards],
            "excluded_products": [e.to_dict() for e in self.excluded_products],
            "overall_conclusion": self.overall_conclusion,
        }


@dataclass
class OutputBundle:
    """恰好三份 Markdown 产出物（SPEC §1/§13）。"""

    user_profile_md: str = ""
    product_list_md: str = ""
    recommendation_md: str = ""

    def to_dict(self) -> dict[str, str]:
        return asdict(self)
