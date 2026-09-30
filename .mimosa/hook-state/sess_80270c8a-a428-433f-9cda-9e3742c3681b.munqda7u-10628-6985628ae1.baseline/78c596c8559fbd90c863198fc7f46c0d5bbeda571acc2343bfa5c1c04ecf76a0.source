# -*- coding: utf-8 -*-
"""src/constraint_engine.py — 硬约束判定（四态）。

契约（contracts/interfaces.md §12）与隔离铁律（SPEC §3）：
- 入参只能是 render→parse 回读后的画像/产品对象（renderer.parse_profile /
  parse_products 产出的普通化对象），禁止接收 SourceRecord 原文，
  禁止复用含原始资料的模型会话，禁止自行从原文补事实；
- 四态语义（contracts/rules.json constraint_states）：
  PASS=证据支持符合（附条件且条件在用户场景成立时写明条件）；FAIL=证据支持不符合；
  UNKNOWN=缺失/冲突/条件未确认（≠PASS，不得进入有效集）；NOT_APPLICABLE=有据证明不适用
  （本模块亦用于「无可执行属性判定的综合诉求」，解释中写明由排序综合处理）；
- 「无证据证明不兼容」≠「兼容」；「可能拿到优惠」≠「预算内」。

确定性判定口径（本项目基线，全部可还原为「属性×需求」）：
- 硬约束准入（rules.json / SPEC §6）：仅当原文有明确「需要/要求/必须」类表述且存在
  产品侧可判定字段时立为硬约束；「约(单值)」预算不立为硬上限（立为歧义需求，不作 E
  门槛，只作排序信号）；无可执行判定的诉求（如「听声辨位」「性价比」）不立为硬约束，
  避免「全 UNKNOWN 空转」，降为偏好信号并在矩阵中标 NOT_APPLICABLE；
- 价格判定只采信「当前售价」的展示价/首口径；区间价跨预算线时 UNKNOWN（具体 SKU 价
  待核验）；渠道报价/可能优惠不当作预算内；
- 宣传性表述不进判定（reconciler 已把宣传原文隔离在 marketing_claims，本模块不读取）；
- 生态兼容：资料未见「兼容安卓」类直接记载时一律 UNKNOWN（不因「支持蓝牙」外推为兼容），
  仅「不支持蓝牙」与「与手机配合」类需求直接相关时判 FAIL。

实现约定：
- UserNeed.evidence_refs 在决策阶段一律为空列表：本模块只拿到渲染冻结后的画像文档
  （原文依据已在画像「场景·原文依据/预算原文表述」等字段内），不编造 span；
- 需求的画像引用来源 key 挂在 UserNeed 实例的私有属性 ``_profile_key`` /
  ``_scene_no`` 上（不进 to_dict），供 constraint_matrix 生成 citation_format 引用；
- ``valid_product_ids`` 依据 build_needs 登记的 need_type 判硬约束；未登记的 need_id
  按「硬约束」保守处理（UNKNOWN/FAIL 一律拦截，不放水）。
"""
from __future__ import annotations

import re
from typing import Any, Optional

from src.renderer import catalog_fields
from src.schemas import ConstraintOutcome, ConstraintResult, NeedType, UserNeed

# ---------------------------------------------------------------------------
# 需要 id（稳定）与算子词汇表（同时供 selector 复用）
# ---------------------------------------------------------------------------

NEED_BUDGET_LIMIT = "need_budget_limit"      # 硬预算上限（口径=上限/区间）
NEED_BUDGET_REFERENCE = "need_budget_reference"  # 预算参考口径（约(单值)/未明确，不作门槛）
NEED_FORM = "need_form_factor"
NEED_UNDERWATER = "need_underwater_audio"
NEED_WATER_RESIST = "need_water_resistant"
NEED_NOISE = "need_noise_cancellation"
NEED_CALL = "need_call_available"
NEED_STORAGE = "need_local_storage"
NEED_BLUETOOTH = "need_bluetooth_available"
NEED_ECOSYSTEM = "need_ecosystem_compatible"
NEED_BRAND_AVOID = "need_brand_avoid"
NEED_WARRANTY = "need_warranty_available"
NEED_SCENE_PREFIX = "need_scene_"
NEED_PREF_PREFIX = "need_pref_"

OP_BUDGET_LE = "<="
OP_UNDERWATER = "underwater_audio"
OP_WATER_RESIST = "water_resistant"
OP_NOISE = "active_noise_cancellation"
OP_CALL = "call_available"
OP_STORAGE = "local_storage"
OP_BLUETOOTH = "bluetooth_available"
OP_ECOSYSTEM = "ecosystem_compatible"
OP_FORM = "form_factor"
OP_BRAND_NOT_IN = "brand_not_in"
OP_WARRANTY = "warranty_available"

#: 硬约束准入：原文明确「需要/要求/必须」类措辞（rules.json 硬约束准入的可执行化）
_HARD_MARKER_RE = re.compile(r"需要|要求|必须|一定要|只能|非要|务必")
#: 水下使用场景触发词（原文明确才立硬约束）
_UNDERWATER_RE = re.compile(r"水下|水中|游泳|潜水|浸水")
#: 防水/防汗（非水下）触发词
_WATER_RESIST_RE = re.compile(r"防水|防汗|防点汗|防溅|防雨")
_NOISE_RE = re.compile(r"降噪|隔绝.{0,6}噪音|隔音")
_CALL_RE = re.compile(r"通话|麦克风|送话")
_STORAGE_RE = re.compile(r"自带存储|本地存储|存储功能|不用带手机|无需带手机|离线听|内存")
_BLUETOOTH_RE = re.compile(r"蓝牙")
_ECOSYSTEM_RE = re.compile(r"安卓|鸿蒙|苹果|iOS|iPhone|配合|兼容|封闭系统|生态")
_WARRANTY_RE = re.compile(r"保修|质保|售后")
#: 形态词族（期望与产品类别按同族匹配，同族=PASS、异族=FAIL）
_FORM_FAMILIES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("头戴", ("头戴", "耳罩")),
    ("入耳", ("入耳",)),
    ("颈挂", ("颈挂", "颈后")),
    ("骨传导", ("骨传导",)),
    ("开放式", ("开放式",)),
    ("耳塞", ("耳塞",)),
)
_HARD_PRIORITY = 10
_SCENE_GOAL_TOP_PRIORITY = 9
_AMBIGUOUS_BUDGET_PRIORITY = 8
_SOFT_EXPLICIT_PRIORITY = 6
_SOFT_DEGREE_PRIORITY = 4
_SOFT_DEFAULT_PRIORITY = 2

_PRICE_ITEM_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)(?:\s*[-—~至到]\s*(\d[\d,]*(?:\.\d+)?))?\s*元")
_PRICE_LISTED_RE = re.compile(
    r"(?:展示价|售价|价格)\s*(?:主要落在)?\s*(\d[\d,]*(?:\.\d+)?)(?:\s*[-—~至到]\s*(\d[\d,]*(?:\.\d+)?))?\s*元")
# 真实模型联调补充（2026-09-24 run #3 实测）：真实模型常写「[699, 999]（测评快照
# 报价区间）」「2948（电商平台甲货架快照价格）」这类不带「元」的形态，
# _PRICE_ITEM_RE 全部漏配 → 决策层误报「展示价未提供」。补两类模式：
# 方括号/圆括号区间、后随全角括号注记的裸数字（货架展示价单值）。
_PRICE_BRACKET_RE = re.compile(
    r"[\[(]\s*(\d[\d,]*(?:\.\d+)?)\s*[,，]\s*(\d[\d,]*(?:\.\d+)?)\s*[\])]")
_PRICE_BARE_RE = re.compile(r"(\d[\d,]*(?:\.\d+)?)\s*(?=（|$|；)")
_LISTED_ANNOTATION_KEYWORDS = ("货架快照", "货架现价", "电商平台甲")
_NEG_BT_UNDERWATER_RE = re.compile(r"水下[^；。]{0,8}(?:不可用|不能用|无法|不支持)")
_LOCAL_UNDERWATER_RE = re.compile(r"水下[^；。]{0,10}(?:MP3|本地|内存|存储|离线)")
_NC_NEG_RE = re.compile(r"无主动降噪|不支持主动降噪|没有主动降噪|非主动降噪")
_CALL_NEG_RE = re.compile(r"不支持.{0,6}(?:通话|麦克风)|无法.{0,6}(?:通话|麦克风)")
_BT_NEG_RE = re.compile(r"不支持蓝牙|无蓝牙|没有蓝牙")
_BT_POS_RE = re.compile(r"支持蓝牙|Bluetooth|蓝牙\s*\d")
_PENDING_PREFIX = "待核验"
_MISSING_EXACT = ("未提供", "待核验", "优先级未明确")


# ---------------------------------------------------------------------------
# parse 后取值读取小工具（导出供 selector 复用；输入=parse 后对象）
# ---------------------------------------------------------------------------

def text_of(value: Any) -> Optional[str]:
    """parse 后取值 → 可判定文本；None/裸缺失表达 → None；列表以「；」连接。"""
    if value is None:
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(value)
    if isinstance(value, (list, tuple)):
        parts = [text_of(item) for item in value]
        joined = "；".join(p for p in parts if p)
        return joined or None
    text = str(value).strip()
    if not text or text in _MISSING_EXACT:
        return None
    return text


def is_pending_text(value: Any) -> bool:
    """带说明的「待核验（…）」口径 → True（parse 对带说明的缺失表达原样保留字符串）。"""
    return isinstance(value, str) and value.strip().startswith(_PENDING_PREFIX)


def to_number(value: Any) -> Optional[float]:
    """数值或纯数字文本 → float；带限定词（约27）等返回 None。"""
    if isinstance(value, bool) or value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).strip()
    if re.fullmatch(r"\d[\d,]*(?:\.\d+)?", text):
        return float(text.replace(",", ""))
    return None


def _annotation_listed(text: str, position: int) -> bool:
    """价格口径后随的全角括号注记是否指向货架/平台展示价（listed 口径）。

    真实模型形态举例：「2948（电商平台甲货架快照价格）」→ True；
    「（测评快照报价区间）」「（存量渠道测评快照）」→ False。
    """
    if position >= len(text) or text[position] != "（":
        return False
    end = text.find("）", position)
    annotation = text[position + 1:end if end != -1 else len(text)]
    return any(keyword in annotation for keyword in _LISTED_ANNOTATION_KEYWORDS)


def parse_price_scopes(text: str) -> list[dict]:
    """当前售价文本 → 价格口径列表 [{lo, hi, listed}]（listed=展示价/货架价口径）。

    兼容形态（真实模型联调校准，2026-09-24）：
    - 「展示价1098元」/「价格主要落在699-999元」（标签口径，listed=True）；
    - 「1099-1499元」「10分钟3小时」等带「元」数字（listed 按注记判断）；
    - 「[699, 999]」「（1099，1499）」括号区间；
    - 「2948（电商平台甲货架快照价格）」后随全角注记的裸数字（注记含
      货架快照/货架现价/电商平台甲 → listed=True，即货架展示价口径）。
    同一口径去重；提取顺序即口径顺序（首口径=文本第一个出现的口径）。
    """
    text = text or ""
    scopes: list[dict] = []
    consumed: list[tuple[int, int]] = []

    def _overlapping(start: int, end: int) -> bool:
        return any(start < e and end > s for s, e in consumed)

    def _push(lo: float, hi: float, listed: bool, start: int, end: int) -> None:
        if any(abs(s["lo"] - lo) < 1e-9 and abs(s["hi"] - hi) < 1e-9 for s in scopes):
            consumed.append((start, end))
            return
        scopes.append({"lo": lo, "hi": hi, "listed": listed})
        consumed.append((start, end))

    listed = _PRICE_LISTED_RE.search(text)
    if listed:
        lo = float(listed.group(1).replace(",", ""))
        hi = float(listed.group(2).replace(",", "")) if listed.group(2) else lo
        _push(lo, hi, True, listed.start(), listed.end())
    for match in _PRICE_BRACKET_RE.finditer(text):
        if _overlapping(match.start(), match.end()):
            continue
        lo = float(match.group(1).replace(",", ""))
        hi = float(match.group(2).replace(",", ""))
        _push(lo, hi, _annotation_listed(text, match.end()), match.start(), match.end())
    for match in _PRICE_ITEM_RE.finditer(text):
        if _overlapping(match.start(), match.end()):
            continue
        lo = float(match.group(1).replace(",", ""))
        hi = float(match.group(2).replace(",", "")) if match.group(2) else lo
        _push(lo, hi, _annotation_listed(text, match.end()), match.start(), match.end())
    for match in _PRICE_BARE_RE.finditer(text):
        if _overlapping(match.start(), match.end()):
            continue
        lo = float(match.group(1).replace(",", ""))
        _push(lo, lo, _annotation_listed(text, match.end()), match.start(), match.end())
    return scopes


# ---------------------------------------------------------------------------
# 引用（citation_format：contracts/field_catalog.json common.citation_format）
# ---------------------------------------------------------------------------

_PROFILE_NAME_INDEX: Optional[dict[str, tuple[str, str]]] = None
_PRODUCT_NAME_INDEX: Optional[dict[str, str]] = None


def _profile_citation(key: str, scene_no: Optional[int] = None) -> str:
    """画像字段 key → 「画像｜字段组｜字段名」（场景子字段带序号）。"""
    global _PROFILE_NAME_INDEX
    if _PROFILE_NAME_INDEX is None:
        index: dict[str, tuple[str, str]] = {}
        for group_name, field_def in catalog_fields("profile"):
            if field_def.get("item_fields"):
                for item in field_def["item_fields"]:
                    index[item["key"]] = (group_name, item["name"])
            else:
                index[field_def["key"]] = (group_name, field_def["name"])
        _PROFILE_NAME_INDEX = index
    group_name, name = _PROFILE_NAME_INDEX[key]
    if scene_no is not None and "{n}" in name:
        name = name.format(n=scene_no)
    return f"画像｜{group_name}｜{name}"


def product_citation(product_id: str, field_key: str) -> str:
    """产品字段 key → 「产品属性｜{编号}｜字段名」（公开，供 selector 复用）。"""
    global _PRODUCT_NAME_INDEX
    if _PRODUCT_NAME_INDEX is None:
        _PRODUCT_NAME_INDEX = {
            field_def["key"]: field_def["name"]
            for _group, field_def in catalog_fields("product")
        }
    return f"产品属性｜{product_id}｜{_PRODUCT_NAME_INDEX[field_key]}"


def _scene_no_of(need: UserNeed) -> Optional[int]:
    match = re.fullmatch(rf"{NEED_SCENE_PREFIX}(\d+)", need.need_id)
    return int(match.group(1)) if match else None


def _profile_key_of(need: UserNeed) -> str:
    """需求 → 画像引用字段 key（build_needs 挂载的 _profile_key 优先）。"""
    attached = getattr(need, "_profile_key", None)
    if attached:
        return str(attached)
    scene_no = _scene_no_of(need)
    if scene_no is not None:
        return "scene_name"
    if need.need_id.startswith(NEED_PREF_PREFIX):
        return "functional_preferences"
    return {
        NEED_BUDGET_LIMIT: "budget_max",
        NEED_BUDGET_REFERENCE: "budget_max",
        NEED_FORM: "desired_product_type",
        NEED_UNDERWATER: "scenes",
        NEED_WATER_RESIST: "functional_preferences",
        NEED_NOISE: "functional_preferences",
        NEED_CALL: "functional_preferences",
        NEED_STORAGE: "functional_preferences",
        NEED_BLUETOOTH: "functional_preferences",
        NEED_ECOSYSTEM: "ecosystem",
        NEED_BRAND_AVOID: "brand_avoidances",
        NEED_WARRANTY: "service_preferences",
    }.get(need.need_id, "other_preferences")


# ---------------------------------------------------------------------------
# build_needs：画像对象 → UserNeed 列表
# ---------------------------------------------------------------------------

def build_needs(profile: dict) -> list[UserNeed]:
    """从画像对象派生原子化 UserNeed 列表（确定性，契约签名）。

    硬约束准入（rules.json / SPEC §6）：
    - 预算：budget_semantics=「上限/区间」→ 硬约束 <=budget_max；「约(单值)」/未明确
      → 歧义需求（不立硬上限，不作 E 门槛，只作排序信号）；
    - 场景/购买目的/期望类型原文明确表达水下使用（游泳/水下/潜水）→ 硬约束水下听歌；
    - 偏好条目按措辞分级：「需要/要求/必须」且存在产品侧可判定字段 → 硬约束；
      度量型措辞（希望/最好/要好）→ 软偏好；无可执行判定的诉求（听感/外观/性价比等）
      → 软偏好且矩阵中 NOT_APPLICABLE（由排序综合处理，不立硬约束避免全 UNKNOWN 空转）；
    - 品牌排斥原文明确列出 → 硬约束（品牌不在排斥清单）。

    需求优先级（数值越大越高，供排序）：硬约束 10 > 高优场景目标 9 起 > 歧义预算 8
    > 明确措辞软偏好 6 > 度量型软偏好 4 > 其他 2。

    :param profile: 画像对象（parse 后）
    :return: UserNeed 列表（need_id 稳定；多次调用结果一致）
    """
    profile = profile or {}
    needs: list[UserNeed] = []
    seen: set[str] = set()

    def _add(need_id: str, expression: str, need_type: NeedType, operator: Optional[str],
             expected: Any, priority: int, *, profile_key: Optional[str] = None,
             scene_no: Optional[int] = None) -> None:
        if need_id in seen:
            return
        seen.add(need_id)
        need = UserNeed(
            need_id=need_id,
            original_expression=expression,
            need_type=need_type,
            operator=operator,
            expected_value=expected,
            priority=priority,
            evidence_refs=[],  # 决策层只有渲染冻结画像，不编造 span（见模块 docstring）
        )
        if profile_key:
            setattr(need, "_profile_key", profile_key)
        if scene_no is not None:
            setattr(need, "_scene_no", scene_no)
        needs.append(need)

    # 1) 预算（口径决定硬约束准入）
    budget_max = to_number(profile.get("budget_max"))
    semantics = text_of(profile.get("budget_semantics"))
    if budget_max is not None:
        raw = text_of(profile.get("budget_raw")) or f"{budget_max:g}元"
        shown = f"{budget_max:g}"
        if semantics in ("上限", "区间"):
            _add(NEED_BUDGET_LIMIT, f"预算不超过{shown}元（原文：{raw}）",
                 NeedType.HARD, OP_BUDGET_LE, budget_max, _HARD_PRIORITY,
                 profile_key="budget_max")
        else:
            label = semantics or "未明确"
            _add(NEED_BUDGET_REFERENCE, f"预算约{shown}元（口径：{label}，不直接当硬上限）",
                 NeedType.AMBIGUOUS, OP_BUDGET_LE, budget_max, _AMBIGUOUS_BUDGET_PRIORITY,
                 profile_key="budget_max")

    # 2) 期望产品类型/形态（原文明确形态词族才立可判定约束）
    desired = text_of(profile.get("desired_product_type"))
    if desired and _form_families_of(desired):
        _add(NEED_FORM, f"期望类型：{desired}", NeedType.HARD, OP_FORM, desired,
             _HARD_PRIORITY, profile_key="desired_product_type")

    # 3) 场景目标 + 场景原文触发的可判定需求
    scenes = [s for s in (profile.get("scenes") or []) if isinstance(s, dict)]
    for index, scene in enumerate(scenes, start=1):
        name = str(scene.get("scene_name") or "").strip()
        if not name:
            continue
        rank = scene.get("scene_priority") if isinstance(scene.get("scene_priority"), int) else None
        priority = (_SCENE_GOAL_TOP_PRIORITY - rank + 1) if rank is not None else 5
        parts = [name]
        if scene.get("usage_frequency"):
            parts.append(f"频率：{scene['usage_frequency']}")
        if scene.get("usage_duration"):
            parts.append(f"时长：{scene['usage_duration']}")
        _add(f"{NEED_SCENE_PREFIX}{index}", "，".join(parts), NeedType.GOAL, None, None,
             priority, profile_key="scene_name", scene_no=index)
        scene_text = "，".join(filter(None, [name, str(scene.get("scene_evidence") or "")]))
        if _UNDERWATER_RE.search(scene_text):
            _add(NEED_UNDERWATER, f"在「{name}」场景下可在水下听歌（依据：{scene_text}）",
                 NeedType.HARD, OP_UNDERWATER, None, _HARD_PRIORITY,
                 profile_key="scene_name", scene_no=index)
        elif _WATER_RESIST_RE.search(scene_text):
            _add(NEED_WATER_RESIST, f"「{name}」场景要求防水防汗（依据：{scene_text}）",
                 NeedType.HARD, OP_WATER_RESIST, None, _HARD_PRIORITY,
                 profile_key="scene_name", scene_no=index)

    # 4) 期望类型 / 购买目的触发的可判定需求（与偏好条目同一套触发词表）
    purpose_text = "；".join(filter(None, (text_of(x) for x in (profile.get("purchase_purposes") or []))))
    for domain, profile_key in ((desired, "desired_product_type"),
                                (purpose_text, "purchase_purposes")):
        if domain:
            _emit_predicate_needs(_add, [(domain, profile_key, None)], hard_marker=True)

    # 5) 偏好条目逐条派生（每条独立编号，措辞分级：需要/要求→硬；希望/最好→软；
    #    无可执行谓词的条目 → 软偏好且矩阵中 NOT_APPLICABLE，由排序综合处理）
    pref_items: list[tuple[str, str, str]] = []
    pref_no = 0
    for key in ("functional_preferences", "sound_preferences", "service_preferences",
                "appearance_preferences", "other_preferences"):
        for item in (profile.get(key) or []):
            item_text = text_of(item)
            if item_text:
                pref_no += 1
                pref_items.append((item_text, key, f"{NEED_PREF_PREFIX}{pref_no}"))
    _emit_predicate_needs(_add, pref_items, hard_marker=False)

    # 6) 设备/生态（原文明确「只能/必须/不兼容」才立硬约束，默认软偏好）
    eco_text = "；".join(filter(None, [
        text_of(profile.get("ecosystem")),
        text_of(profile.get("ecosystem_notes")),
        text_of(profile.get("devices")),
    ]))
    keyword = _ecosystem_keyword(eco_text)
    if eco_text and keyword:
        hard_eco = bool(re.search(r"只能|仅支持|必须|不兼容|无法兼容", eco_text))
        _add(NEED_ECOSYSTEM, f"与用户设备生态（{keyword}）配合（原文：{eco_text}）",
             NeedType.HARD if hard_eco else NeedType.SOFT,
             OP_ECOSYSTEM, keyword, _HARD_PRIORITY if hard_eco else _SOFT_EXPLICIT_PRIORITY,
             profile_key="ecosystem")

    # 7) 品牌排斥（原文明确列出 → 硬约束）
    avoid = [a for a in (text_of(x) for x in (profile.get("brand_avoidances") or [])) if a]
    if avoid:
        _add(NEED_BRAND_AVOID, f"品牌不在排斥清单（{'、'.join(avoid)}）",
             NeedType.HARD, OP_BRAND_NOT_IN, avoid, _HARD_PRIORITY,
             profile_key="brand_avoidances")

    _register_need_types(needs)
    return needs


_NEED_TYPE_REGISTRY: dict[str, NeedType] = {}
"""need_id → need_type 登记（build_needs 时更新；valid_product_ids 据此判硬约束，
未登记的 need_id 按硬约束保守处理）。"""


def _register_need_types(needs: list[UserNeed]) -> None:
    for need in needs:
        _NEED_TYPE_REGISTRY[need.need_id] = need.need_type


def _ecosystem_keyword(text: str) -> Optional[str]:
    for candidate in ("安卓", "鸿蒙", "苹果", "iOS", "iPhone", "Velmora"):
        if candidate in text:
            return candidate
    return None


def _emit_predicate_needs(_add, items: list[tuple[str, str, Optional[str]]], *,
                          hard_marker: bool) -> None:
    """条目文本 → 可判定需求（每条至多一个谓词，按固定词表顺序命中；词表见模块头）。

    hard_marker=True（期望类型/购买目的域）：命中即按「原文明确表达」立硬约束候选；
    hard_marker=False（偏好条目域）：按条目措辞分级。无可执行谓词的条目立为软偏好
    （operator=None → 矩阵中 NOT_APPLICABLE），保持「全需求×全产品」覆盖。
    """
    for text, profile_key, fallback_id in items:
        if not text:
            continue
        hard = hard_marker or bool(_HARD_MARKER_RE.search(text))

        def _emit(need_id, expression, operator, priority_hard, priority_soft, expected=None):
            _add(need_id, expression,
                 NeedType.HARD if hard else NeedType.SOFT, operator, expected,
                 priority_hard if hard else priority_soft, profile_key=profile_key)

        if _UNDERWATER_RE.search(text):
            _emit(NEED_UNDERWATER, f"可在水下听歌（原文：{_trim(text)}）",
                  OP_UNDERWATER, _HARD_PRIORITY, _SOFT_EXPLICIT_PRIORITY)
            continue
        if _WATER_RESIST_RE.search(text):
            _emit(NEED_WATER_RESIST, f"防水防汗（原文：{_trim(text)}）",
                  OP_WATER_RESIST, _HARD_PRIORITY, _SOFT_DEGREE_PRIORITY)
            continue
        if _NOISE_RE.search(text):
            _emit(NEED_NOISE, f"降噪（原文：{_trim(text)}）",
                  OP_NOISE, _HARD_PRIORITY, _SOFT_EXPLICIT_PRIORITY)
            continue
        if _CALL_RE.search(text):
            _emit(NEED_CALL, f"通话能力（原文：{_trim(text)}）",
                  OP_CALL, _HARD_PRIORITY, _SOFT_EXPLICIT_PRIORITY)
            continue
        if _STORAGE_RE.search(text):
            _emit(NEED_STORAGE, f"自带本地存储（原文：{_trim(text)}）",
                  OP_STORAGE, _HARD_PRIORITY, _SOFT_DEGREE_PRIORITY)
            continue
        if _BLUETOOTH_RE.search(text):
            _emit(NEED_BLUETOOTH, f"蓝牙（原文：{_trim(text)}）",
                  OP_BLUETOOTH, _HARD_PRIORITY, _SOFT_EXPLICIT_PRIORITY)
            continue
        if _WARRANTY_RE.search(text):
            _emit(NEED_WARRANTY, f"保修售后（原文：{_trim(text)}）",
                  OP_WARRANTY, _HARD_PRIORITY, _SOFT_DEGREE_PRIORITY)
            continue
        if fallback_id:
            _add(fallback_id, f"综合诉求：{_trim(text)}", NeedType.SOFT, None, None,
                 _SOFT_DEFAULT_PRIORITY, profile_key=profile_key)


def _trim(text: str, limit: int = 60) -> str:
    cleaned = re.sub(r"\s+", "", text_of(text) or "")
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _form_families_of(text: str) -> list[str]:
    families = []
    if text:
        for family, words in _FORM_FAMILIES:
            if any(w in text for w in words):
                families.append(family)
    return families


# ---------------------------------------------------------------------------
# 谓词判定（产品对象 → (四态, 解释, 产品字段 key)）
# ---------------------------------------------------------------------------

def _judge_budget(product: dict, budget_max: float) -> tuple[ConstraintOutcome, str, str]:
    """预算判定：只采信「当前售价」的展示价/首口径；渠道报价不当作预算内。"""
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "current_price")
    raw = product.get("current_price")
    text = text_of(raw)
    if text is None:
        reason = "待核验口径" if is_pending_text(raw) else "未提供"
        return (ConstraintOutcome.UNKNOWN, f"当前售价{reason}（{cite}），无法确认是否在预算内",
                "current_price")
    scopes = parse_price_scopes(text)
    if not scopes:
        return (ConstraintOutcome.UNKNOWN,
                f"当前售价无可比对数值（{cite}＝{_trim(text, 46)}）", "current_price")
    primary = scopes[0] if scopes[0]["listed"] else next(
        (s for s in scopes if s["listed"]), scopes[0])
    shown = f"{primary['lo']:g}-{primary['hi']:g}元" if primary["hi"] > primary["lo"] \
        else f"{primary['lo']:g}元"
    exceeds = primary["lo"] > budget_max
    within = primary["hi"] <= budget_max
    if within:
        return (ConstraintOutcome.PASS,
                f"展示价口径{shown}不超预算上限{budget_max:g}元"
                f"（{cite}＝{_trim(text, 46)}）", "current_price")
    if exceeds:
        note = ""
        if any(s["lo"] <= budget_max for s in scopes if s is not primary):
            note = "；并列保留的渠道报价口径不当作预算内（可能拿到优惠≠预算内）"
        return (ConstraintOutcome.FAIL,
                f"展示价口径{shown}超出预算上限{budget_max:g}元"
                f"（{cite}＝{_trim(text, 46)}）{note}", "current_price")
    return (ConstraintOutcome.UNKNOWN,
            f"售价区间{shown}跨越预算上限{budget_max:g}元，具体成交SKU价待核验"
            f"（{cite}＝{_trim(text, 46)}）", "current_price")


def _judge_underwater(product: dict) -> tuple[ConstraintOutcome, str, str]:
    """水下听歌判定：官方类别/防水条件/水下蓝牙字段的可靠证据；宣传不进判定。

    - 官网类别含游泳/水下（官方对用途的分类声明）或防水条件含水深/时长口径 → 有支持；
    - 「水下只能使用MP3模式」类实测 → 有支持（条件：须经本地模式）；
    - 「水下蓝牙不可用」只是否定蓝牙路径，与本地模式水下可用不矛盾 → 转为条件；
    - 无任何可靠证据 → UNKNOWN（不因缺失外推为可用）。
    """
    pid = str(product.get("canonical_id"))
    positives: list[str] = []
    conditions: list[str] = []

    uw_raw = product.get("bluetooth_underwater")
    uw_text = text_of(uw_raw)
    if uw_text and not is_pending_text(uw_raw):
        if _LOCAL_UNDERWATER_RE.search(uw_text):
            positives.append(f"水下经本地模式可用（{product_citation(pid, 'bluetooth_underwater')}）")
            conditions.append("水下使用须经本地模式；蓝牙水下串流宣称与实测不一致，待核验"
                              if "待核验" in uw_text else "水下使用须经本地模式")
        elif _NEG_BT_UNDERWATER_RE.search(uw_text):
            conditions.append(f"水下蓝牙不可用（{product_citation(pid, 'bluetooth_underwater')}）"
                              "，水下使用不走蓝牙")

    wp_raw = product.get("waterproof_conditions")
    wp_text = text_of(wp_raw)
    if wp_text and not is_pending_text(wp_raw):
        has_water_word = re.search(r"水下|水中|浸水|淡水|海水|游泳|水深", wp_text)
        has_measure = re.search(r"\d(?:\.\d+)?\s*米|小时|分钟", wp_text)
        if has_water_word and has_measure:
            positives.append(f"防水条件载明水下口径（{product_citation(pid, 'waterproof_conditions')}）")
            conditions.append(_trim(wp_text, 60))

    cat_text = text_of(product.get("category"))
    if cat_text and _UNDERWATER_RE.search(cat_text):
        positives.append(f"官方类别为「{_trim(cat_text, 24)}」（{product_citation(pid, 'category')}）")
        conditions.append("具体水深/时长以防水适用条件字段为准")

    if positives:
        detail = "；".join(positives)
        if conditions:
            detail += f"；条件：{'；'.join(dict.fromkeys(conditions))}"
        return (ConstraintOutcome.PASS, detail, "bluetooth_underwater")
    if conditions:  # 仅「水下蓝牙不可用」类否定 → 无正向水下证据，但不构成「不能水下」的充分证据
        return (ConstraintOutcome.UNKNOWN,
                f"仅有否定性记载（{'；'.join(conditions)}），未见水下可用的可靠证据（UNKNOWN≠PASS）",
                "bluetooth_underwater")
    return (ConstraintOutcome.UNKNOWN,
            "产品属性中未见水下使用/防水的可靠证据（UNKNOWN≠PASS，不因缺失外推为可用）",
            "waterproof_conditions")


def _judge_water_resist(product: dict) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    rating_raw = product.get("protection_rating")
    rating = text_of(rating_raw)
    wp_raw = product.get("waterproof_conditions")
    wp = text_of(wp_raw)
    if rating and not is_pending_text(rating_raw):
        note = f"防护等级{_trim(rating, 20)}（{product_citation(pid, 'protection_rating')}）"
        if wp:
            note += f"；防水条件：{_trim(wp, 50)}（{product_citation(pid, 'waterproof_conditions')}）"
        return (ConstraintOutcome.PASS, note, "protection_rating")
    if wp and not is_pending_text(wp_raw):
        return (ConstraintOutcome.PASS,
                f"防水适用条件有记载（{product_citation(pid, 'waterproof_conditions')}＝{_trim(wp, 50)}）",
                "waterproof_conditions")
    reason = "为待核验口径" if (is_pending_text(rating_raw) or is_pending_text(wp_raw)) else "未提供"
    return (ConstraintOutcome.UNKNOWN,
            f"防护等级与防水条件{reason}（{product_citation(pid, 'protection_rating')}）",
            "protection_rating")


def _judge_noise(product: dict) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "noise_cancellation")
    raw = product.get("noise_cancellation")
    text = text_of(raw)
    if text is None:
        reason = "为待核验口径" if is_pending_text(raw) else "未提供"
        return (ConstraintOutcome.UNKNOWN, f"降噪能力{reason}（{cite}），无证据≠符合", "noise_cancellation")
    if _NC_NEG_RE.search(text):
        return (ConstraintOutcome.FAIL,
                f"{cite}＝{_trim(text, 46)}：资料明确「无主动降噪」", "noise_cancellation")
    if re.search(r"主动降噪|ANC", text):
        if is_pending_text(raw):
            return (ConstraintOutcome.UNKNOWN, f"降噪能力为待核验口径（{cite}＝{_trim(text, 46)}）",
                    "noise_cancellation")
        return (ConstraintOutcome.PASS, f"{cite}＝{_trim(text, 46)}", "noise_cancellation")
    if "环境" in text:
        return (ConstraintOutcome.FAIL,
                f"{cite}＝{_trim(text, 46)}：仅环境声/环境降噪，非主动降噪", "noise_cancellation")
    return (ConstraintOutcome.UNKNOWN, f"降噪能力无明确口径（{cite}＝{_trim(text, 46)}）",
            "noise_cancellation")


def _judge_call(product: dict) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "call_capability")
    raw = product.get("call_capability")
    text = text_of(raw)
    if text is None:
        reason = "为待核验口径" if is_pending_text(raw) else "未提供"
        return (ConstraintOutcome.UNKNOWN, f"通话能力{reason}（{cite}），无证据证明可用≠可用",
                "call_capability")
    if _CALL_NEG_RE.search(text):
        return (ConstraintOutcome.FAIL, f"{cite}＝{_trim(text, 46)}", "call_capability")
    return (ConstraintOutcome.PASS, f"{cite}＝{_trim(text, 46)}", "call_capability")


def _judge_storage(product: dict) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "local_storage_gb")
    number = to_number(product.get("local_storage_gb"))
    if number is not None:
        if number > 0:
            return (ConstraintOutcome.PASS,
                    f"本地存储{number:g}GB（{product_citation(pid, 'local_storage_gb')}），可脱机播放",
                    "local_storage_gb")
        return (ConstraintOutcome.FAIL, f"本地存储为0（{product_citation(pid, 'local_storage_gb')}）",
                "local_storage_gb")
    raw = product.get("local_storage_gb")
    if text_of(raw) is None:
        core = text_of(product.get("core_functions")) or ""
        if is_pending_text(raw) or re.search(r"存储", core):
            return (ConstraintOutcome.UNKNOWN,
                    f"存储容量未载明数值（{product_citation(pid, 'local_storage_gb')}；"
                    f"{product_citation(pid, 'core_functions')}）", "local_storage_gb")
        return (ConstraintOutcome.UNKNOWN,
                f"本地存储未提供（{product_citation(pid, 'local_storage_gb')}）", "local_storage_gb")
    return (ConstraintOutcome.UNKNOWN,
            f"本地存储为待核验口径（{product_citation(pid, 'local_storage_gb')}）", "local_storage_gb")


def _judge_bluetooth(product: dict) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "bluetooth")
    raw = product.get("bluetooth")
    text = text_of(raw)
    if text is None:
        reason = "为待核验口径" if is_pending_text(raw) else "未提供"
        return (ConstraintOutcome.UNKNOWN, f"蓝牙能力{reason}（{cite}）", "bluetooth")
    if _BT_NEG_RE.search(text):
        return (ConstraintOutcome.FAIL, f"{cite}＝{_trim(text, 46)}", "bluetooth")
    if _BT_POS_RE.search(text):
        if is_pending_text(raw):
            return (ConstraintOutcome.UNKNOWN, f"蓝牙能力为待核验口径（{cite}＝{_trim(text, 46)}）",
                    "bluetooth")
        return (ConstraintOutcome.PASS, f"{cite}＝{_trim(text, 46)}", "bluetooth")
    return (ConstraintOutcome.UNKNOWN, f"蓝牙能力无明确口径（{cite}＝{_trim(text, 46)}）", "bluetooth")


def _judge_ecosystem(product: dict, keyword: Optional[str]) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "bluetooth")
    text = text_of(product.get("bluetooth")) or text_of(product.get("core_functions")) or ""
    target = keyword or "用户设备"
    if _BT_NEG_RE.search(text):
        return (ConstraintOutcome.FAIL,
                f"需求为与{target}设备配合，但{cite}＝{_trim(text, 46)}，无无线连接路径", "bluetooth")
    if keyword and re.search(r"兼容|适配", text) and keyword in text:
        return (ConstraintOutcome.PASS, f"{cite}＝{_trim(text, 46)}：资料明确兼容{keyword}", "bluetooth")
    return (ConstraintOutcome.UNKNOWN,
            f"资料未见与{target}适配的直接记载（{cite}＝{_trim(text, 46) or '未提供'}）；"
            "无证据证明兼容≠兼容，不作外推", "bluetooth")


def _judge_form(product: dict, expected: str) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "category")
    expected_families = _form_families_of(expected)
    product_text = "；".join(filter(None, [
        text_of(product.get("category")),
        text_of(product.get("wearing_design")),
    ])) or ""
    product_families = _form_families_of(product_text)
    if not product_families:
        return (ConstraintOutcome.UNKNOWN,
                f"产品类别未见形态词（{cite}＝{_trim(product_text, 40) or '未提供'}），无法与期望形态比对",
                "category")
    joined = "、".join(expected_families)
    if set(expected_families) & set(product_families):
        return (ConstraintOutcome.PASS,
                f"期望形态（{joined}）与产品类别相符（{cite}＝{_trim(product_text, 40)}）", "category")
    return (ConstraintOutcome.FAIL,
            f"期望形态（{joined}）与产品类别不符（{cite}＝{_trim(product_text, 40)}）", "category")


def _judge_brand_avoid(product: dict, avoid: list[str]) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "brand")
    brand = text_of(product.get("brand")) or ""
    hit = next((a for a in avoid if a and a in brand), None)
    if hit:
        return (ConstraintOutcome.FAIL, f"品牌{brand}在用户排斥清单（{cite}＝{brand}）", "brand")
    return (ConstraintOutcome.PASS,
            f"品牌{brand}不在用户排斥清单（{'、'.join(avoid)}）（{cite}＝{brand}）", "brand")


def _judge_warranty(product: dict) -> tuple[ConstraintOutcome, str, str]:
    pid = str(product.get("canonical_id"))
    cite = product_citation(pid, "warranty")
    raw = product.get("warranty")
    text = text_of(raw)
    if text is None:
        reason = "为待核验口径" if is_pending_text(raw) else "未提供"
        return (ConstraintOutcome.UNKNOWN, f"保修{reason}（{cite}）", "warranty")
    return (ConstraintOutcome.PASS, f"{cite}＝{_trim(text, 46)}", "warranty")


# ---------------------------------------------------------------------------
# 单条需求 × 单产品判定入口
# ---------------------------------------------------------------------------

def _evaluate(need: UserNeed, product: dict) -> tuple[ConstraintOutcome, str, Optional[str]]:
    """一条需求 × 一款产品 → (四态, 解释, 产品字段 key)。"""
    op = need.operator
    if op == OP_BUDGET_LE:
        budget = to_number(need.expected_value)
        if budget is None:
            return (ConstraintOutcome.UNKNOWN, "预算上限无数值，无法判定", "current_price")
        outcome, detail, field_key = _judge_budget(product, budget)
        if need.need_id == NEED_BUDGET_REFERENCE:
            detail += "；预算口径为约(单值)/未明确，本判定不作硬约束门槛"
        return (outcome, detail, field_key)
    if op == OP_UNDERWATER:
        return _judge_underwater(product)
    if op == OP_WATER_RESIST:
        return _judge_water_resist(product)
    if op == OP_NOISE:
        return _judge_noise(product)
    if op == OP_CALL:
        return _judge_call(product)
    if op == OP_STORAGE:
        return _judge_storage(product)
    if op == OP_BLUETOOTH:
        return _judge_bluetooth(product)
    if op == OP_ECOSYSTEM:
        keyword = need.expected_value if isinstance(need.expected_value, str) else None
        return _judge_ecosystem(product, keyword)
    if op == OP_FORM:
        return _judge_form(product, str(need.expected_value or ""))
    if op == OP_BRAND_NOT_IN:
        avoid = need.expected_value if isinstance(need.expected_value, list) else []
        return _judge_brand_avoid(product, avoid)
    if op == OP_WARRANTY:
        return _judge_warranty(product)

    # 无可执行谓词：
    # - 场景目标：若场景文本命中可判定词表（水下/防水/降噪），沿用对应判定；
    # - 其余（听感/外观/性价比等综合诉求）：NOT_APPLICABLE，由排序综合处理。
    scene_text = need.original_expression
    if _scene_no_of(need) is not None or need.need_id.startswith(NEED_PREF_PREFIX):
        if _UNDERWATER_RE.search(scene_text):
            outcome, detail, field_key = _judge_underwater(product)
            return (outcome, f"诉求「{_trim(scene_text, 30)}」：{detail}", field_key)
        if _WATER_RESIST_RE.search(scene_text):
            outcome, detail, field_key = _judge_water_resist(product)
            return (outcome, f"诉求「{_trim(scene_text, 30)}」：{detail}", field_key)
        if _NOISE_RE.search(scene_text):
            outcome, detail, field_key = _judge_noise(product)
            return (outcome, f"诉求「{_trim(scene_text, 30)}」：{detail}", field_key)
    return (ConstraintOutcome.NOT_APPLICABLE,
            "综合诉求无产品侧可执行判定字段（不立为硬约束，避免无据阈值）；由排序作为偏好信号综合处理",
            None)


def constraint_matrix(profile: dict, products: dict[str, dict]) -> list[ConstraintResult]:
    """全部 UserNeed × 全部产品 → 四态判定矩阵（契约签名）。

    profile_reference / attribute_reference 使用 citation_format
    （「画像｜字段组｜字段名」「产品属性｜P001｜字段名」），指向前两份文档内字段。

    :param profile: 画像对象（parse 后）
    :param products: {canonical_id: 产品对象}（parse 后）
    :return: ConstraintResult 列表（product_id × need_id 全覆盖；空产品集 → 空列表）
    """
    needs = build_needs(profile or {})
    results: list[ConstraintResult] = []
    for cid in sorted(products or {}):
        product = products[cid] or {}
        for need in needs:
            outcome, explanation, field_key = _evaluate(need, product)
            scene_no = getattr(need, "_scene_no", None)
            if scene_no is None:
                scene_no = _scene_no_of(need)
            results.append(ConstraintResult(
                product_id=cid,
                need_id=need.need_id,
                result=outcome,
                profile_reference=_profile_citation(_profile_key_of(need), scene_no),
                attribute_reference=product_citation(cid, field_key) if field_key else None,
                explanation=explanation,
            ))
    return results


def valid_product_ids(constraints: list[ConstraintResult]) -> set[str]:
    """有效集 E：对所有适用硬约束均 PASS（NOT_APPLICABLE=不适用，不阻拦）。

    rules.json：UNKNOWN 的产品不得进入有效集。need_type 登记不在册的 need_id
    按硬约束保守处理（UNKNOWN/FAIL 一律拦截，不放水）。

    :param constraints: constraint_matrix 产出
    :return: 有效产品 canonical_id 集合
    """
    appeared: set[str] = set()
    blocked: set[str] = set()
    for item in constraints or []:
        appeared.add(item.product_id)
        need_type = _NEED_TYPE_REGISTRY.get(item.need_id, NeedType.HARD)
        if need_type is not NeedType.HARD:
            continue
        if item.result in (ConstraintOutcome.FAIL, ConstraintOutcome.UNKNOWN):
            blocked.add(item.product_id)
    return appeared - blocked
