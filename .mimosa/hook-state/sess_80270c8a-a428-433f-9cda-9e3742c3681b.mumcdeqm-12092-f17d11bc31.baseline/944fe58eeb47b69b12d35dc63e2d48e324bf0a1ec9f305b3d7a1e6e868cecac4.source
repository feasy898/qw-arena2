# -*- coding: utf-8 -*-
"""确定性基线抽取器（零模型依赖）。

模型网关不可用时的降级抽取：只填写能在原文中逐字回溯的字段，
提取不到的保持空形态（渲染层落「未提供」），不编造数值、单位或品牌型号。

- deterministic_profile：画像键与 profile_extractor.extract_profile_bundle 的画像半边一致。
  场景时长/频率只收指定短语；优先级只在同句有明确主次措辞时填写；
  品类、期望类型与购买目的同样只收原文里已经写出的词。
- deterministic_product_draft：键集与 product_extractor.empty_draft 一致。
  品牌/型号在草稿里是身份标量；货架其余列与官网键值行写入 observation 列表
  （FactCell 字典形态）。货架展示价的 normalized_value 取单元格纯数字，并用同行
  平台原文作口径注记，以便和后写入的渠道报价区分。货架事实种类用「有依据归纳」
  （价格不用「实测观察」，也不用「官网声明」）；官网键值行用「官网声明」。
  媒体报道补渠道报价区间、含主打/采用/搭载/支持的卖点句（宣传表述，只进
  marketing_claims；「不支持」不算）和「在…条件下」；第三方实测只在出现
  「实测/测得/测试中」时记实测观察；
  用户反馈按原文关键词归入好评/槽点，样本不足 2 条时在 feedback_sample_note 注明。
"""
from __future__ import annotations

import json
import re
from pathlib import Path

from src.input_adapter import InputError, csv_cells, line_spans, read_csv_rows
from src.schemas import FactKind, FactStatus, SourceType

_REPO_ROOT = Path(__file__).resolve().parent.parent
_FIELD_CATALOG = _REPO_ROOT / "contracts" / "field_catalog.json"

# 画像键（与 profile_extractor.SCALARS / LISTS / 场景 item 对齐）
_PROFILE_SCALARS = (
    "profile_id", "name", "gender", "age", "occupation", "city",
    "product_category", "desired_product_type", "budget_min", "budget_max",
    "currency", "budget_raw", "budget_semantics", "ecosystem", "ecosystem_notes",
)
_PROFILE_LISTS = (
    "purchase_purposes", "devices", "brand_preferences", "brand_avoidances",
    "appearance_preferences", "sound_preferences", "functional_preferences",
    "service_preferences", "other_preferences",
)
_SCENE_KEYS = (
    "scene_name", "usage_duration", "usage_frequency",
    "scene_priority", "priority_basis", "scene_evidence",
)

_KIND_LISTING = FactKind.INFERRED.value          # 有依据归纳
_KIND_OFFICIAL = FactKind.OFFICIAL_CLAIM.value   # 官网声明
_KIND_MEASURED = FactKind.MEASURED.value         # 实测观察
_KIND_MARKETING = FactKind.MARKETING.value       # 宣传表述
_KIND_FEEDBACK = FactKind.USER_FEEDBACK.value    # 用户反馈
_STATUS_OK = FactStatus.SUPPORTED.value
_STATUS_COND = FactStatus.CONDITIONAL.value
_THIRD_PARTY_COND = "第三方实测"
_SAMPLE_SHORT_NOTE = "样本不足，不构成共性"
_SAMPLE_UNKNOWN_NOTE = "代表性未知，不能推及整体"

_BUDGET_RE = re.compile(
    r"(?:预算)?"
    r"\s*(?P<qual>大概|大约|约|不超过|最多|上限)?"
    r"\s*(?:在)?"
    r"\s*(?P<n1>\d+(?:\.\d+)?)"
    r"(?:\s*[-—~～至到]\s*(?P<n2>\d+(?:\.\d+)?))?"
    r"\s*(?P<unit>元|块)"
    r"\s*(?P<suffix>左右|上下|以内|以下|之内|之间)?"
)
_DEVICE_RE = re.compile(
    r"(?P<brand>[A-Za-z][A-Za-z0-9]{1,24}|[\u4e00-\u9fff]{2,8})"
    r"\s*的?\s*(?P<kind>手机|电脑|笔记本|平板)"
)
_CAMP_BRAND_RE = re.compile(r"阵营的([\u4e00-\u9fff]{2,8})")
_PREFERENCE_BRAND_RE = re.compile(r"偏好\s*([^，。；\n]+)")
_PREFERENCE_VERB_RE = re.compile(
    r"(?:希望|需要|要求|喜欢|不喜欢|不想)([^，。；\n]{2,40})"
)
_PREFERENCE_REQUIRE_RE = re.compile(
    r"对[^，。；\n]{1,24}?要求[^，。；\n]{0,8}"
)
_HEADER_RE = re.compile(r"【([^】]+)】")
_KV_RE = re.compile(r"(.{2,12})[:：]\s*(.+)")
_IP_RE = re.compile(r"IPX?\d+(?:/IPX?\d+)*")
_WEIGHT_RE = re.compile(r"约?\d+(?:\.\d+)?g(?![A-Za-z])")
_STORAGE_RE = re.compile(r"(\d+(?:\.\d+)?)\s*GB", re.IGNORECASE)
_BATTERY_RE = re.compile(
    r"^(?P<mode>.*?)(?P<dur>(?:最长)?约?\d+(?:\.\d+)?\s*小时)$"
)
_CONDITIONAL_RE = re.compile(r"未披露|未查到|待核验|须查|不以外推|无统一口径|未完整")
_PAGE_DATE_RE = re.compile(r"页面日期[:：]\s*(\d{4}-\d{2}-\d{2})")
_SOURCE_DATE_RE = re.compile(
    r"(?:发布日期|测试日期|页面日期)[:：]\s*(\d{4}-\d{2}-\d{2})"
)
_PRODUCT_ID_RE = re.compile(r"P\d{1,3}")
_WS_RE = re.compile(r"\s+")
_PRICE_CONTEXT_RE = re.compile(r"报价|价格|售价|价位")
_PRICE_RANGE_RE = re.compile(
    r"(?P<low>\d+(?:\.\d+)?)\s*[-—~～－至到]\s*(?P<high>\d+(?:\.\d+)?)\s*元"
)
_CLAIM_WORD_RE = re.compile(r"主打|采用|搭载|(?<!不)支持")
_COND_PHRASE_RE = re.compile(r"在[^。！？\n；]{1,40}?条件下")
_MEASURED_WORD_RE = re.compile(r"实测|测得")
_REVIEW_SENSE_RE = re.compile(r"实测|测试中")
_DURATION_RE = re.compile(r"(?:最长\s*)?约?\s*\d+(?:\.\d+)?\s*小时")
_PAREN_RE = re.compile(r"（([^）]{1,30})）")
_NEG_BEFORE_RE = re.compile(r"(?:不会|没有|不太|不怎么|并不|不|没|无|未).{0,2}$")
_PRAISE_HAO_RE = re.compile(r"好(?!像|比|歹)")
_COMPLAINT_RES = (
    re.compile(r"失望"),
    re.compile(r"差(?!别|距|异|不多|点)"),
    re.compile(r"(?<![判果诊])断(?!定|层|句)"),
    re.compile(r"坏"),
    re.compile(r"闷"),
    re.compile(r"疼"),
    re.compile(r"痛"),
)
_PRAISE_WORDS = ("满意", "不错", "舒服", "舒适", "清晰", "喜欢", "推荐")
_BATTERY_MODES = ("未区分模式", "蓝牙模式", "MP3模式", "内存模式", "本地MP3")
_FEEDBACK_HEADER_PREDS = (
    lambda name: "反馈内容" in name,
    lambda name: "反馈摘录" in name,
    lambda name: "评价原文" in name,
    lambda name: "评价内容" in name,
    lambda name: name in {"评价", "评论", "内容", "原文"},
    lambda name: (
        any(token in name for token in ("反馈", "评价", "评论"))
        and not any(token in name for token in ("时间", "日期", "ID", "编号"))
    ),
)

# 场景名只来自这些词。相邻两词拼成更长原文（如「咖啡馆办公」）时只保留长名。
_SCENE_KEYWORDS = (
    "通勤", "办公", "游泳", "游戏", "运动", "跑步", "健身",
    "学习", "出差", "会议", "图书馆", "宿舍", "咖啡馆", "飞机", "高铁",
)
# 时长/频率：只收这些原文短语，不把「每次游1小时」「每周去3-4次」补成规范说法
_SCENE_DURATION_RE = re.compile(r"单程\d+分钟|往返\d+分钟|每次\d+[-~]?\d*分钟|约\d+小时")
_SCENE_FREQUENCY_RE = re.compile(r"每天|每周\d+次|日常|周末|平时|偶尔")
# 「最近/最好/最后」里的「最」不是排序。排序词必须和场景名落在同一分句。
_PRIORITY_CUE_RE = re.compile(r"主要|首先|核心是|是核心|最(?!近|好|后|终|多)")
_CLAUSE_SPLIT_RE = re.compile(r"[，,。；;！？!?\n]+")
_CATEGORY_WORDS = ("耳机", "耳塞", "头戴式", "头戴")
_DESIRED_TYPE_RE = re.compile(
    r"骨传导[^，。；\n]{0,8}?耳机"
    r"|颈挂式[^，。；\n]{0,8}?耳机"
    r"|头戴式[^，。；\n]{0,8}?耳机"
    r"|入耳式[^，。；\n]{0,8}?耳机"
    r"|挂耳式[^，。；\n]{0,8}?耳机"
    r"|能在水下听歌的耳机"
    r"|游泳耳机"
    r"|骨传导|颈挂式|头戴式|入耳式|挂耳式|开放式"
)
_PURPOSE_RES = (
    re.compile(r"听{1,2}播客和音乐"),
    re.compile(r"听{1,2}播客"),
    re.compile(r"听{1,2}音乐"),
    re.compile(r"听{1,2}歌"),
    re.compile(r"听{1,2}网课"),
    re.compile(r"学习视频"),
    re.compile(r"开电话会议"),
    re.compile(r"打电话"),
    re.compile(r"打游戏"),
    re.compile(r"玩游戏"),
    re.compile(r"缓解枯燥"),
    re.compile(r"开会"),
    re.compile(r"办公"),
    re.compile(r"放松一下|放松"),
    re.compile(r"学习"),
)
_ECOSYSTEM_PHRASES = (
    "封闭系统生态", "封闭系统", "安卓", "Android", "iOS", "苹果", "鸿蒙", "Windows",
)
_BRAND_STOP = frozenset({
    "这类", "海外", "一线", "音频", "品牌", "的", "和", "与", "或", "等", "手机", "耳机",
})
_PREF_STOP = frozenset({"一些", "一下", "功能", "产品", "耳机", "可以", "能够"})

# 货架列（表头优先；无表头时仅在列数与编号形态吻合时用样例列序）
_LISTING_ROLES = (
    ("brand", lambda name: name == "品牌", 1),
    ("platform", lambda name: name == "平台", 2),
    ("shop", lambda name: "店铺" in name, 3),
    ("title", lambda name: "标题" in name, 4),
    ("model", lambda name: name == "型号", 5),
    ("variant", lambda name: ("颜色" in name or "套餐" in name), 6),
    ("price", lambda name: "展示价" in name, 7),
    ("original", lambda name: "优惠前" in name, 8),
    ("sales", lambda name: ("已售" in name or "付款" in name), 9),
)

_CATALOG_KEYS: list[str] | None = None


def _product_field_keys() -> list[str]:
    """field_catalog.product 全部子字段键（与 empty_draft 同源）。"""
    global _CATALOG_KEYS
    if _CATALOG_KEYS is None:
        catalog = json.loads(_FIELD_CATALOG.read_text(encoding="utf-8"))
        _CATALOG_KEYS = [
            field["key"]
            for group in catalog["product"]["groups"]
            for field in group["fields"]
        ]
    return list(_CATALOG_KEYS)


def _identity(text: str) -> str:
    return _WS_RE.sub("", text or "").casefold()


def _number(text: str):
    if text is None or not re.fullmatch(r"\d+(?:\.\d+)?", str(text)):
        return None
    raw = str(text)
    return float(raw) if "." in raw else int(raw)


def _status_for(text: str) -> str:
    if text and _CONDITIONAL_RE.search(text):
        return _STATUS_COND
    return _STATUS_OK


# ---------------------------------------------------------------------------
# 画像
# ---------------------------------------------------------------------------

def deterministic_profile(user_text: str) -> dict:
    """从用户描述原文保守提取画像（无模型、无 needs）。

    只填有原文依据的预算、设备与生态、场景、购买目标、明确偏好。
    场景时长/频率对不上指定短语时保持 None；没有明确主次措辞时不编优先级。
    """
    text = user_text or ""
    profile = {key: None for key in _PROFILE_SCALARS}
    for key in _PROFILE_LISTS:
        profile[key] = []
    profile["scenes"] = []
    if not text.strip():
        return profile
    _fill_budget(profile, text)
    _fill_devices(profile, text)
    _fill_scenes(profile, text)
    _fill_purchase_goals(profile, text)
    _fill_preferences(profile, text)
    return profile


def _fill_budget(profile: dict, text: str) -> None:
    matches = [m for m in _BUDGET_RE.finditer(text) if m.group("unit")]
    if not matches:
        return
    chosen = next((m for m in matches if "预算" in m.group(0)), matches[0])
    raw = chosen.group(0)
    if raw not in text:
        return
    n1 = _number(chosen.group("n1"))
    n2 = _number(chosen.group("n2")) if chosen.group("n2") else None
    if n1 is None:
        return
    qual = chosen.group("qual") or ""
    suffix = chosen.group("suffix") or ""
    if n2 is not None:
        low, high = (n1, n2) if n1 <= n2 else (n2, n1)
        profile["budget_min"] = low
        profile["budget_max"] = high
        profile["budget_semantics"] = "区间"
    elif suffix in {"以内", "以下", "之内"} or qual in {"不超过", "最多", "上限"}:
        profile["budget_max"] = n1
        profile["budget_semantics"] = "上限"
    else:
        # 「约/左右/大概」以及无口径单值：数值仍取该数字，口径不升成硬上限
        profile["budget_max"] = n1
        profile["budget_semantics"] = "约(单值)"
    profile["budget_raw"] = raw
    # 任务口径把「元/块」都视为预算货币标记；枚举只有「元」
    profile["currency"] = "元"


def _sentences(text: str) -> list[str]:
    parts = re.split(r"[。！？\n]+", text)
    return [part.strip() for part in parts if part.strip() and part.strip() in text]


def _fill_devices(profile: dict, text: str) -> None:
    devices: list[str] = []

    def _add(phrase: str) -> None:
        phrase = (phrase or "").strip()
        if len(phrase) < 2 or phrase not in text:
            return
        if phrase not in devices:
            devices.append(phrase)

    for match in _DEVICE_RE.finditer(text):
        _add(match.group(0))
    for match in _CAMP_BRAND_RE.finditer(text):
        brand = match.group(1)
        window = text[max(0, match.start() - 12):match.end() + 24]
        if "手机" in window or "电脑" in window or "笔记本" in window:
            _add(brand)
    profile["devices"] = devices

    found = [phrase for phrase in _ECOSYSTEM_PHRASES if phrase in text]
    found = [phrase for phrase in found if not any(
        phrase != other and phrase in other for other in found
    )]
    if not found:
        return
    if "安卓" in found and any(token in text for token in ("换成", "安卓阵营")):
        profile["ecosystem"] = "安卓"
    else:
        profile["ecosystem"] = found[0]
    for sentence in _sentences(text):
        if "换成" in sentence or ("之前" in sentence and "用" in sentence):
            if any(phrase in sentence for phrase in found) or any(item in sentence for item in devices):
                profile["ecosystem_notes"] = sentence
                break


def _compound_scene_names(text: str) -> list[str]:
    """两个场景词在原文里紧挨着（咖啡馆+办公）时，长名才是一个场景。"""
    found = []
    for left in _SCENE_KEYWORDS:
        for right in _SCENE_KEYWORDS:
            if left == right:
                continue
            phrase = left + right
            if phrase in text and phrase not in found:
                found.append(phrase)
    return found


def _embedded_component(text: str, index: int, name: str, compounds: list[str]) -> bool:
    """短场景词已经并进更长场景名时，别再把「学习视频」里的「学习」单列。"""
    if not any(name != compound and name in compound for compound in compounds):
        return False
    end = index + len(name)
    if end >= len(text) or not ("\u4e00" <= text[end] <= "\u9fff"):
        return False
    return not any(text.startswith(compound, index) for compound in compounds)


def _scene_mentions(text: str) -> list[tuple[int, str]]:
    """按出现顺序给出不重叠的场景名。长名占住的字，短名不再单列。"""
    compounds = _compound_scene_names(text)
    names = sorted(
        set(compounds) | set(_SCENE_KEYWORDS),
        key=lambda name: (-len(name), text.find(name), name),
    )
    occupied: list[tuple[int, int]] = []
    found: list[tuple[int, str]] = []
    for name in names:
        start = 0
        while True:
            index = text.find(name, start)
            if index < 0:
                break
            end = index + len(name)
            overlapped = any(index < right and end > left for left, right in occupied)
            if not overlapped and not _embedded_component(text, index, name, compounds):
                occupied.append((index, end))
                found.append((index, name))
            start = index + 1
    found.sort()
    seen = set()
    mentions = []
    for index, name in found:
        if name in seen:
            continue
        seen.add(name)
        mentions.append((index, name))
    return mentions


def _phrase_near(sentence: str, anchor: str, pattern: re.Pattern) -> str | None:
    """取锚点所在句里、离锚点最近的一处匹配；紧挨着的多条频率词连成原文。"""
    if not sentence or anchor not in sentence:
        return None
    matches = list(pattern.finditer(sentence))
    if not matches:
        return None
    groups = [[matches[0]]]
    for match in matches[1:]:
        if match.start() == groups[-1][-1].end():
            groups[-1].append(match)
        else:
            groups.append([match])
    anchor_at = sentence.find(anchor)
    anchor_end = anchor_at + len(anchor)

    def _distance(group) -> tuple[int, int]:
        start = group[0].start()
        end = group[-1].end()
        if end <= anchor_at:
            return anchor_at - end, start
        if start >= anchor_end:
            return start - anchor_end, start
        return 0, start

    best = min(groups, key=_distance)
    phrase = sentence[best[0].start():best[-1].end()]
    if phrase not in sentence:
        return None
    return phrase


def _scene_is_primary(text: str, scene_name: str) -> bool:
    """排序措辞和场景名在同一分句才算主次；「最近/最好」不算。"""
    if not scene_name:
        return False
    for clause in _CLAUSE_SPLIT_RE.split(text):
        if scene_name in clause and _PRIORITY_CUE_RE.search(clause):
            return True
    return False


def _fill_scenes(profile: dict, text: str) -> None:
    scenes = []
    for _index, name in _scene_mentions(text):
        evidence = name
        for sentence in _sentences(text):
            if name in sentence:
                evidence = sentence
                break
        if evidence not in text or name not in evidence:
            evidence = name
        duration = _phrase_near(evidence, name, _SCENE_DURATION_RE)
        frequency = _phrase_near(evidence, name, _SCENE_FREQUENCY_RE)
        if duration and duration not in text:
            duration = None
        if frequency and frequency not in text:
            frequency = None
        scene = {key: None for key in _SCENE_KEYS}
        scene["scene_name"] = name
        scene["usage_duration"] = duration
        scene["usage_frequency"] = frequency
        scene["scene_evidence"] = evidence
        if _scene_is_primary(text, name):
            # 同一处主次措辞覆盖到的场景并列记 1，不在它们之间编造先后
            scene["scene_priority"] = 1
            scene["priority_basis"] = "原文主次措辞"
        else:
            scene["priority_basis"] = "优先级未明确"
        scenes.append(scene)
    profile["scenes"] = scenes


def _desired_product_type(text: str) -> str | None:
    matches = [match.group(0) for match in _DESIRED_TYPE_RE.finditer(text)]
    matches = [item for item in matches if item and item in text and item != "耳机"]
    if not matches:
        return None
    matches.sort(key=lambda item: (-len(item), text.find(item)))
    return matches[0]


def _purchase_purposes(text: str) -> list[str]:
    spans = []
    for pattern in _PURPOSE_RES:
        for match in pattern.finditer(text):
            phrase = match.group(0)
            if phrase and phrase in text:
                spans.append((match.start(), match.end(), phrase))
    kept = []
    for start, end, phrase in spans:
        if any(other_start <= start and end <= other_end and (other_start, other_end) != (start, end)
               for other_start, other_end, _phrase in spans):
            continue
        kept.append((start, phrase))
    purposes = []
    for _start, phrase in sorted(kept, key=lambda item: item[0]):
        if phrase not in purposes:
            purposes.append(phrase)
    return purposes


def _fill_purchase_goals(profile: dict, text: str) -> None:
    for word in _CATEGORY_WORDS:
        if word in text:
            profile["product_category"] = word
            break
    profile["desired_product_type"] = _desired_product_type(text)
    profile["purchase_purposes"] = _purchase_purposes(text)


def _pref_bucket(atom: str) -> str:
    if any(token in atom for token in ("音质", "低音", "轰头", "听感", "音色")):
        return "sound_preferences"
    if any(token in atom for token in ("外观", "浅色", "好看", "颜色", "设计")):
        return "appearance_preferences"
    if any(token in atom for token in ("保修", "售后", "客服", "退换")):
        return "service_preferences"
    if any(token in atom for token in ("性价比", "耐用", "专业", "品牌没有", "没有特别")):
        return "other_preferences"
    if any(token in atom for token in (
        "降噪", "防水", "防汗", "防点汗", "存储", "麦克风", "通话",
        "蓝牙", "佩戴", "听声", "辨位", "配合", "安卓", "噪音",
    )):
        return "functional_preferences"
    return "other_preferences"


def _add_pref(buckets: dict[str, list[str]], text: str, bucket: str, phrase: str) -> None:
    phrase = (phrase or "").strip(" ，,、")
    if len(phrase) < 2 or phrase in _PREF_STOP or phrase not in text:
        return
    current = buckets[bucket]
    if any(phrase == old or phrase in old for old in current):
        return
    buckets[bucket] = [old for old in current if old not in phrase]
    buckets[bucket].append(phrase)


def _pref_atoms(clause: str, text: str) -> list[str]:
    parts = [part.strip() for part in re.split(r"[、]", clause) if part.strip()]
    if len(parts) <= 1:
        return [clause.strip()] if clause.strip() in text else []
    atoms = []
    for part in parts:
        if part in text and len(part) >= 2:
            atoms.append(part)
    return atoms


def _fill_preferences(profile: dict, text: str) -> None:
    buckets = {key: [] for key in (
        "brand_preferences", "brand_avoidances", "appearance_preferences",
        "sound_preferences", "functional_preferences", "service_preferences",
        "other_preferences",
    )}
    brand_match = _PREFERENCE_BRAND_RE.search(text)
    if brand_match:
        chunk = brand_match.group(1)
        for token in re.findall(r"[A-Za-z][A-Za-z0-9]+|[\u4e00-\u9fff]{2,8}", chunk):
            if token in _BRAND_STOP or token not in text:
                continue
            _add_pref(buckets, text, "brand_preferences", token)
    clauses = []
    for match in _PREFERENCE_VERB_RE.finditer(text):
        clauses.append(match.group(0))
    for match in _PREFERENCE_REQUIRE_RE.finditer(text):
        clauses.append(match.group(0))
    for clause in clauses:
        for atom in _pref_atoms(clause, text):
            _add_pref(buckets, text, _pref_bucket(atom), atom)
    for key, values in buckets.items():
        values.sort(key=lambda item: text.find(item))
        profile[key] = values


# ---------------------------------------------------------------------------
# 产品草稿
# ---------------------------------------------------------------------------

def deterministic_product_draft(record, source_records_for_product) -> dict:
    """从该产品的五源记录做确定性抽取，返回 empty_draft 同构草稿。

    :param record: 产品登记（canonical_id / original_ids / brand_raw / model_raw）
    :param source_records_for_product: 该产品的 SourceRecord 列表
    """
    draft = _empty_product_draft(record)
    records = list(source_records_for_product or [])
    header_cache: dict[str, list[str] | None] = {}
    listing_model = ""
    for source in records:
        if _source_is(source, SourceType.ECOMMERCE_LISTING):
            found = _fill_listing(draft, source, header_cache)
            if found:
                listing_model = found
    for source in records:
        if _source_is(source, SourceType.OFFICIAL_SITE):
            _fill_official(draft, source)
    for source in records:
        if _source_is(source, SourceType.MEDIA_COVERAGE):
            _fill_media(draft, source)
    for source in records:
        if _source_is(source, SourceType.THIRD_PARTY_REVIEW):
            _fill_review(draft, source)
    _fill_feedback(draft, records, header_cache)
    # 无官网型号块时，货架「型号」列原文可作身份型号（须与登记值相容）
    if not draft.get("product_name") and listing_model:
        if _model_compatible(listing_model, draft.get("model")):
            draft["model"] = listing_model
    return draft


def _empty_product_draft(record) -> dict:
    draft = {key: [] for key in _product_field_keys()}
    draft["canonical_id"] = getattr(record, "canonical_id", None)
    aliases = list(getattr(record, "original_ids", None) or [])
    draft["aliases"] = sorted(str(alias) for alias in aliases if str(alias).strip())
    draft["product_name"] = None
    draft["brand"] = getattr(record, "brand_raw", None)
    draft["model"] = getattr(record, "model_raw", None)
    return draft


def _source_is(record, source_type: SourceType) -> bool:
    value = getattr(record, "source_type", None)
    return value == source_type or str(value) == str(source_type)


def _spans_of(record):
    spans = list(getattr(record, "spans", None) or [])
    if spans:
        return spans
    return line_spans(getattr(record, "original_text", "") or "")


def _append_obs(draft: dict, field_key: str, obs: dict | None) -> None:
    if not obs or not isinstance(draft.get(field_key), list):
        return
    signature = (
        obs.get("raw_value"),
        tuple(obs.get("items") or ()),
        obs.get("applicable_variant"),
    )
    for existing in draft[field_key]:
        existing_sig = (
            existing.get("raw_value"),
            tuple(existing.get("items") or ()),
            existing.get("applicable_variant"),
        )
        if existing_sig == signature:
            return
    draft[field_key].append(obs)


def _observation(record, span_id: str, quote: str, *, fact_kind: str,
                 raw_value=None, items=None, unit=None, normalized_value=None,
                 status: str | None = None, applicable_variant=None,
                 as_of=None, conditions=None) -> dict | None:
    """构造一条 observation。引文或取值不是原文子串时返回 None（宁缺勿造）。"""
    text = getattr(record, "original_text", "") or ""
    source_id = getattr(record, "source_id", "") or ""
    if not source_id or not quote or quote not in text:
        return None
    span_text = None
    for sid, start, end in _spans_of(record):
        if sid == span_id:
            span_text = text[start:end]
            break
    if span_text is None or quote not in span_text:
        return None
    if isinstance(raw_value, str) and raw_value not in text:
        return None
    if items is not None:
        if not items or any(item not in text or item not in quote for item in items):
            return None
    if applicable_variant and applicable_variant not in text:
        applicable_variant = None
    if as_of and as_of not in text:
        as_of = None
    obs = {
        "fact_kind": fact_kind,
        "status": status or _STATUS_OK,
        "conditions": list(conditions or []),
        "applicable_variant": applicable_variant,
        "as_of": as_of,
        "evidence_refs": [{
            "source_id": source_id,
            "span_id": span_id,
            "exact_quote": quote,
        }],
    }
    if items is not None:
        obs["items"] = list(items)
    else:
        obs["raw_value"] = raw_value
        obs["normalized_value"] = normalized_value
        obs["unit"] = unit
    return obs


def _listing_header(record, cache: dict) -> list[str] | None:
    path = getattr(record, "path", None)
    if not path:
        return None
    key = str(path)
    if key in cache:
        return cache[key]
    header = None
    try:
        header, _rows = read_csv_rows(path)
    except (InputError, OSError, ValueError):
        header = None
    cache[key] = header
    return header


def _positional_ok(cells: list[str]) -> bool:
    return len(cells) >= 8 and bool(_PRODUCT_ID_RE.fullmatch(cells[0].strip()))


def _listing_cell(header, cells, predicate, fallback_index):
    """返回 (单元格原文, 表头名或 None)。"""
    index = None
    header_name = None
    if header:
        for idx, name in enumerate(header):
            if predicate(name.strip()):
                index = idx
                header_name = name.strip()
                break
    elif _positional_ok(cells):
        index = fallback_index
    if index is None or index >= len(cells):
        return "", header_name
    return cells[index].strip(), header_name


def _row_span(record):
    spans = _spans_of(record)
    if not spans:
        return None
    return spans[0][0]


def _fill_listing(draft: dict, record, cache: dict) -> str:
    """抽取货架行。返回型号列原文（没有则为空串）。"""
    text = getattr(record, "original_text", "") or ""
    if not text.strip():
        return ""
    cells = csv_cells(text)
    header = _listing_header(record, cache)
    span_id = _row_span(record)
    if not span_id:
        return ""
    values = {}
    headers = {}
    for role, predicate, index in _LISTING_ROLES:
        cell, header_name = _listing_cell(header, cells, predicate, index)
        values[role] = cell
        headers[role] = header_name

    brand = values["brand"]
    if brand and brand in text:
        registered = draft.get("brand")
        if not registered or _identity(brand) == _identity(str(registered)):
            draft["brand"] = brand
    listing_model = values["model"] if values["model"] and values["model"] in text else ""

    variant = values["variant"]
    if variant and variant in text:
        items = [part.strip() for part in re.split(r"[/、]", variant) if part.strip()]
        if len(items) <= 1:
            items = [variant]
        if all(item in variant for item in items):
            _append_obs(draft, "variants", _observation(
                record, span_id, variant, fact_kind=_KIND_LISTING, items=items,
            ))

    title = values["title"]
    if title and title in text:
        _append_obs(draft, "listing_title", _observation(
            record, span_id, title, fact_kind=_KIND_LISTING, raw_value=title,
        ))

    platform, shop = values["platform"], values["shop"]
    channel = None
    if platform and shop and f"{platform},{shop}" in text:
        channel = f"{platform},{shop}"
    elif platform and platform in text:
        channel = platform
    elif shop and shop in text:
        channel = shop
    if channel:
        _append_obs(draft, "channel_and_shop", _observation(
            record, span_id, channel, fact_kind=_KIND_LISTING, raw_value=channel,
        ))

    # 展示价单元格只有数字。同行平台名是原文，用来标明这是货架展示价口径，
    # 与后写入的渠道报价区间区分（决策只把带该注记的数字当展示价）。
    _append_price(draft, record, span_id, "current_price", values["price"], headers["price"],
                  scope_note=values["platform"])
    _append_price(draft, record, span_id, "original_price", values["original"], headers["original"])

    sales = values["sales"]
    if sales and sales in text:
        _append_obs(draft, "sales_volume", _observation(
            record, span_id, sales, fact_kind=_KIND_LISTING, raw_value=sales,
        ))
    return listing_model


def _model_compatible(candidate: str, registered) -> bool:
    if not candidate:
        return False
    if not registered:
        return True
    registered_form = _identity(str(registered))
    candidate_form = _identity(candidate)
    return registered_form in candidate_form or candidate_form in registered_form


def _append_price(draft, record, span_id, field_key, cell, header_name,
                  scope_note: str | None = None) -> None:
    text = getattr(record, "original_text", "") or ""
    if not cell or cell not in text:
        return
    unit = "元" if header_name and "元" in header_name else None
    number = _number(cell)
    conditions = []
    note = (scope_note or "").strip()
    if note and note in text and note not in cell:
        conditions.append(note)
    _append_obs(draft, field_key, _observation(
        record, span_id, cell, fact_kind=_KIND_LISTING, raw_value=cell,
        unit=unit, normalized_value=number, conditions=conditions or None,
    ))


def _maybe_set_model(draft: dict, model_name: str) -> None:
    if not model_name:
        return
    registered = draft.get("model")
    if registered and not _model_compatible(model_name, registered):
        return
    draft["model"] = model_name
    draft["product_name"] = model_name


def _page_date(text: str):
    match = _PAGE_DATE_RE.search(text or "")
    if match and match.group(1) in text:
        return match.group(1)
    return None


def _fill_official(draft: dict, record) -> None:
    text = getattr(record, "original_text", "") or ""
    as_of = _page_date(text)
    section = None
    for span_id, start, end in _spans_of(record):
        line = text[start:end]
        stripped = line.strip()
        if not stripped:
            continue
        header = _HEADER_RE.fullmatch(stripped)
        if header:
            name = header.group(1).strip()
            if name == "品牌概况":
                section = "brand"
            else:
                section = "model"
                if name in text:
                    _maybe_set_model(draft, name)
            continue
        if section is None:
            continue
        matched = _KV_RE.match(stripped)
        if not matched:
            continue
        key = matched.group(1).strip()
        value = matched.group(2).strip()
        if not value or value not in text or stripped not in text:
            continue
        if section == "brand":
            _fill_brand_kv(draft, record, span_id, stripped, key, value, as_of)
        else:
            _fill_model_kv(draft, record, span_id, stripped, key, value, as_of)


def _fill_brand_kv(draft, record, span_id, line, key, value, as_of) -> None:
    if "归属" in key and "市场" in key:
        field_key = "brand_origin_market"
    elif "品类" in key:
        field_key = "brand_category_focus"
    else:
        return
    _append_obs(draft, field_key, _observation(
        record, span_id, line, fact_kind=_KIND_OFFICIAL, raw_value=value,
        status=_status_for(value), as_of=as_of,
    ))


def _clauses(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[；;]", value) if part.strip()]


def _is_bluetooth_claim(clause: str) -> bool:
    """续航分句里的「蓝牙模式」不是蓝牙能力陈述，不写入 bluetooth 字段。"""
    if "bluetooth" in clause.lower():
        return True
    if "蓝牙" not in clause:
        return False
    if _BATTERY_RE.match(clause) and not any(
        token in clause for token in ("支持", "不支持", "版本", "水下", "不可用")
    ):
        return False
    return True


def _fill_model_kv(draft, record, span_id, line, key, value, as_of) -> None:
    if "降噪" in key:
        _append_obs(draft, "noise_cancellation", _observation(
            record, span_id, line, fact_kind=_KIND_OFFICIAL, raw_value=value,
            status=_status_for(value), as_of=as_of,
        ))
    if "保修" in key:
        _append_obs(draft, "warranty", _observation(
            record, span_id, line, fact_kind=_KIND_OFFICIAL, raw_value=value,
            status=_status_for(value), as_of=as_of,
        ))

    battery_modes = []
    battery_leftover = []
    key_is_battery = ("续航" in key) or ("电池" in key)
    for clause in _clauses(value):
        if clause not in (getattr(record, "original_text", "") or ""):
            continue
        if ("充电" in clause) or ("快充" in clause) or ("充电" in key and "续航" not in key):
            _append_obs(draft, "charging", _observation(
                record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=clause,
                status=_status_for(clause), as_of=as_of,
            ))
        if "存储" in clause or "存储" in key:
            _append_storage(draft, record, span_id, clause, as_of)
        if _is_bluetooth_claim(clause):
            _append_obs(draft, "bluetooth", _observation(
                record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=clause,
                status=_status_for(clause), as_of=as_of,
            ))
            if "水下" in clause:
                _append_obs(draft, "bluetooth_underwater", _observation(
                    record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=clause,
                    status=_status_for(clause), as_of=as_of,
                ))
        if any(token in clause for token in ("防水", "水深", "浸水", "淡水", "海水")) or "防水" in key:
            _append_obs(draft, "waterproof_conditions", _observation(
                record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=clause,
                status=_status_for(clause), as_of=as_of,
            ))
        ip_match = _IP_RE.search(clause)
        if ip_match and (ip_match.group(0) in clause):
            token = ip_match.group(0)
            _append_obs(draft, "protection_rating", _observation(
                record, span_id, token, fact_kind=_KIND_OFFICIAL, raw_value=token,
                status=_STATUS_OK, as_of=as_of,
            ))
        weight_match = _WEIGHT_RE.search(clause)
        if weight_match and ("重量" in key or "重量" in clause or weight_match.group(0) in clause):
            token = weight_match.group(0)
            amount = _number(re.search(r"\d+(?:\.\d+)?", token).group(0))
            _append_obs(draft, "weight_g", _observation(
                record, span_id, token, fact_kind=_KIND_OFFICIAL, raw_value=token,
                unit="g", normalized_value=amount, status=_STATUS_OK, as_of=as_of,
            ))
        if key_is_battery:
            if ("充电" in clause) or ("快充" in clause):
                continue
            parsed = _BATTERY_RE.match(clause)
            if parsed and parsed.group("dur"):
                battery_modes.append((parsed.group("mode").strip(), parsed.group("dur").strip(), clause))
            else:
                battery_leftover.append(clause)

    for mode, duration, clause in battery_modes:
        if duration not in clause:
            continue
        _append_obs(draft, "battery_by_mode", _observation(
            record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=duration,
            unit="小时" if "小时" in duration else None,
            status=_status_for(clause), applicable_variant=mode or None, as_of=as_of,
        ))
    if key_is_battery and not battery_modes and battery_leftover:
        leftover = "；".join(battery_leftover)
        quote = leftover if leftover in (getattr(record, "original_text", "") or "") else value
        raw = quote if quote != line else value
        if raw in (getattr(record, "original_text", "") or ""):
            _append_obs(draft, "battery_by_mode", _observation(
                record, span_id, quote if quote in line else line,
                fact_kind=_KIND_OFFICIAL, raw_value=raw,
                status=_STATUS_COND, as_of=as_of,
            ))


def _append_storage(draft, record, span_id, clause, as_of) -> None:
    match = _STORAGE_RE.search(clause)
    if not match:
        if "存储" in clause:
            _append_obs(draft, "local_storage_gb", _observation(
                record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=clause,
                status=_status_for(clause), as_of=as_of,
            ))
        return
    amount = _number(match.group(1))
    unit = "GB" if "GB" in clause or "gb" in clause else None
    _append_obs(draft, "local_storage_gb", _observation(
        record, span_id, clause, fact_kind=_KIND_OFFICIAL, raw_value=clause,
        unit=unit, normalized_value=amount, status=_status_for(clause), as_of=as_of,
    ))


# ---------------------------------------------------------------------------
# 媒体报道 / 第三方实测 / 用户反馈
# ---------------------------------------------------------------------------

def _source_date(text: str):
    match = _SOURCE_DATE_RE.search(text or "")
    if match and match.group(1) in (text or ""):
        return match.group(1)
    return None


def _iter_lines(record):
    text = getattr(record, "original_text", "") or ""
    for span_id, start, end in _spans_of(record):
        line = text[start:end]
        if line.strip():
            yield span_id, line


def _paren_notes(snippet: str) -> list[str]:
    notes = []
    for match in _PAREN_RE.finditer(snippet or ""):
        note = match.group(1).strip()
        if note and note not in notes:
            notes.append(note)
    return notes


def _price_snippets(line: str) -> list[str]:
    """渠道报价区间原文（语境词与「数字-数字元」同句；提取不到返回空）。"""
    found = []
    for sentence in _sentences(line):
        if sentence not in line or not _PRICE_CONTEXT_RE.search(sentence):
            continue
        for match in _PRICE_RANGE_RE.finditer(sentence):
            left = max(0, match.start() - 30)
            before = list(_PRICE_CONTEXT_RE.finditer(sentence, left, match.end()))
            if before:
                start = before[-1].start()
                if sentence[max(0, start - 2):start] == "渠道":
                    start -= 2
            elif _PRICE_CONTEXT_RE.search(sentence, match.end(), match.end() + 16):
                start = match.start()
            else:
                continue
            end = match.end()
            paren = re.match(r"（[^）]{1,30}）", sentence[end:])
            if paren:
                end += paren.end()
            snippet = sentence[start:end]
            if snippet and snippet in line and snippet not in found:
                found.append(snippet)
    return found


def _claim_snippets(line: str) -> list[str]:
    """含主打/采用/搭载/支持（不含「不支持」）的原文分句。"""
    found = []
    for sentence in _sentences(line):
        if sentence not in line:
            continue
        pieces = [part.strip() for part in re.split(r"[；;]", sentence) if part.strip()]
        hits = [part for part in pieces if part in line and _CLAIM_WORD_RE.search(part)]
        if not hits and _CLAIM_WORD_RE.search(sentence) and sentence in line:
            hits = [sentence]
        for hit in hits:
            if hit not in found:
                found.append(hit)
    return found


def _condition_hits(line: str) -> list[tuple[str, str]]:
    """返回 (所在句, 「在…条件下」短语)。"""
    found = []
    seen = set()
    for sentence in _sentences(line):
        if sentence not in line:
            continue
        for match in _COND_PHRASE_RE.finditer(sentence):
            phrase = match.group(0)
            if phrase in sentence and phrase not in seen:
                seen.add(phrase)
                found.append((sentence, phrase))
    return found


def _condition_field(sentence: str) -> str:
    if "降噪" in sentence:
        return "noise_cancellation"
    if any(token in sentence for token in ("音质", "听感", "音色")):
        return "acoustic_tech"
    if any(token in sentence for token in ("防水", "水深", "浸水", "水下", "淋雨")):
        return "waterproof_conditions"
    if "充电" in sentence or "快充" in sentence:
        return "charging"
    if "蓝牙" in sentence:
        return "bluetooth"
    if "佩戴" in sentence:
        return "wearing_design"
    if _DURATION_RE.search(sentence) and any(token in sentence for token in ("续航", "播放", "小时")):
        return "battery_by_mode"
    return "special_features"


def _mode_before(text: str, index: int):
    before = text[:index]
    found = None
    found_at = -1
    for candidate in _BATTERY_MODES:
        pos = before.rfind(candidate)
        if pos > found_at:
            found_at = pos
            found = candidate
    return found


def _fill_media(draft: dict, record) -> None:
    text = getattr(record, "original_text", "") or ""
    as_of = _source_date(text)
    for span_id, line in _iter_lines(record):
        for snippet in _price_snippets(line):
            _append_obs(draft, "current_price", _observation(
                record, span_id, snippet, fact_kind=_KIND_LISTING, raw_value=snippet,
                unit="元", status=_STATUS_OK, as_of=as_of,
                conditions=_paren_notes(snippet),
            ))
        for snippet in _claim_snippets(line):
            _append_obs(draft, "marketing_claims", _observation(
                record, span_id, snippet, fact_kind=_KIND_MARKETING,
                items=[snippet], status=_STATUS_OK, as_of=as_of,
            ))
        for sentence, phrase in _condition_hits(line):
            field_key = _condition_field(sentence)
            conditions = [phrase]
            if field_key == "battery_by_mode":
                dur = _DURATION_RE.search(sentence)
                if not dur or dur.group(0) not in sentence:
                    field_key = "special_features"
                else:
                    _append_obs(draft, field_key, _observation(
                        record, span_id, sentence, fact_kind=_KIND_LISTING,
                        raw_value=dur.group(0),
                        unit="小时" if "小时" in dur.group(0) else None,
                        status=_STATUS_COND, as_of=as_of, conditions=conditions,
                        applicable_variant=_mode_before(sentence, dur.start()),
                    ))
                    continue
            if field_key == "special_features":
                _append_obs(draft, field_key, _observation(
                    record, span_id, sentence, fact_kind=_KIND_LISTING,
                    items=[sentence], status=_STATUS_COND, as_of=as_of,
                    conditions=conditions,
                ))
            else:
                _append_obs(draft, field_key, _observation(
                    record, span_id, sentence, fact_kind=_KIND_LISTING,
                    raw_value=sentence, status=_STATUS_COND, as_of=as_of,
                    conditions=conditions,
                ))


def _fill_review(draft: dict, record) -> None:
    text = getattr(record, "original_text", "") or ""
    as_of = _source_date(text)
    for span_id, line in _iter_lines(record):
        for sentence in _sentences(line):
            if sentence not in line:
                continue
            _append_measured_battery(draft, record, span_id, sentence, as_of)
            _append_measured_sense(draft, record, span_id, sentence, as_of)


def _append_measured_battery(draft, record, span_id, sentence, as_of) -> None:
    """仅「实测/测得」与时长同句才记续航；转述规格不升级为实测。"""
    if not _MEASURED_WORD_RE.search(sentence):
        return
    for match in _DURATION_RE.finditer(sentence):
        duration = match.group(0)
        if duration not in sentence:
            continue
        conditions = [_THIRD_PARTY_COND]
        for note in _paren_notes(sentence):
            if note not in conditions:
                conditions.append(note)
        amount = None
        number = re.search(r"\d+(?:\.\d+)?", duration)
        if number:
            amount = _number(number.group(0))
        _append_obs(draft, "battery_by_mode", _observation(
            record, span_id, sentence, fact_kind=_KIND_MEASURED,
            raw_value=duration, unit="小时" if "小时" in duration else None,
            normalized_value=amount, status=_STATUS_COND, as_of=as_of,
            conditions=conditions,
            applicable_variant=_mode_before(sentence, match.start()),
        ))


def _append_measured_sense(draft, record, span_id, sentence, as_of) -> None:
    """含「实测/测试中」的降噪或音质句，落到对应产品力字段，附条件。"""
    if not _REVIEW_SENSE_RE.search(sentence):
        return
    if "降噪" in sentence:
        field_key = "noise_cancellation"
    elif any(token in sentence for token in ("音质", "听感", "音色", "低音")):
        field_key = "acoustic_tech"
    else:
        return
    _append_obs(draft, field_key, _observation(
        record, span_id, sentence, fact_kind=_KIND_MEASURED,
        raw_value=sentence, status=_STATUS_COND, as_of=as_of,
        conditions=[_THIRD_PARTY_COND],
    ))


def _negated_at(clause: str, index: int) -> bool:
    prefix = clause[max(0, index - 8):index]
    return bool(_NEG_BEFORE_RE.search(prefix))


def _has_active_keyword(clause: str, words: tuple[str, ...]) -> bool:
    for word in sorted(words, key=len, reverse=True):
        start = 0
        while True:
            index = clause.find(word, start)
            if index < 0:
                break
            if not _negated_at(clause, index):
                return True
            start = index + len(word)
    return False


def _has_active_pattern(clause: str, pattern: re.Pattern) -> bool:
    return any(not _negated_at(clause, match.start()) for match in pattern.finditer(clause))


def _is_praise(clause: str) -> bool:
    return _has_active_keyword(clause, _PRAISE_WORDS) or _has_active_pattern(clause, _PRAISE_HAO_RE)


def _is_complaint(clause: str) -> bool:
    for pattern in _COMPLAINT_RES:
        if _has_active_pattern(clause, pattern):
            return True
    return False


def _review_clauses(review: str) -> list[str]:
    parts = re.split(r"[，,。！？!?；;\n]+", review or "")
    clauses = []
    for part in parts:
        clause = part.strip()
        if len(clause) >= 2 and clause in review and clause not in clauses:
            clauses.append(clause)
    return clauses


def _bucket_clauses(review: str) -> tuple[list[str], list[str]]:
    praise, complaints = [], []
    for clause in _review_clauses(review):
        if _is_praise(clause) and clause not in praise:
            praise.append(clause)
        if _is_complaint(clause) and clause not in complaints:
            complaints.append(clause)
    return praise, complaints


def _feedback_column(header, cells) -> str:
    if not header:
        return ""
    for predicate in _FEEDBACK_HEADER_PREDS:
        for index, name in enumerate(header):
            if not predicate(name.strip()):
                continue
            if index >= len(cells):
                return ""
            return cells[index].strip()
    return ""


def _feedback_as_of(header, cells, text: str):
    if not header:
        return None
    for index, name in enumerate(header):
        if index >= len(cells):
            continue
        if any(token in name for token in ("时间", "日期")):
            token = cells[index].strip()
            if token and token in text and re.search(r"\d{4}", token):
                return token
    return None


def _feedback_review(record, cache):
    text = getattr(record, "original_text", "") or ""
    if not text.strip():
        return None
    cells = csv_cells(text)
    header = _listing_header(record, cache)
    review = _feedback_column(header, cells)
    if not review or review not in text:
        return None
    if re.fullmatch(r"[\d/\-:\s.]+", review):
        return None
    span_id = _row_span(record)
    if not span_id:
        return None
    return span_id, review, _feedback_as_of(header, cells, text)


def _feedback_conditions(row_count: int, bucket_rows: int) -> list[str]:
    if row_count < 2 or bucket_rows < 2:
        return [_SAMPLE_SHORT_NOTE]
    return [_SAMPLE_UNKNOWN_NOTE]


def _fill_feedback(draft: dict, records, cache: dict) -> None:
    parsed = []
    for record in records or []:
        if not _source_is(record, SourceType.USER_FEEDBACK):
            continue
        got = _feedback_review(record, cache)
        if not got:
            continue
        span_id, review, as_of = got
        praise, complaints = _bucket_clauses(review)
        parsed.append((record, span_id, review, praise, complaints, as_of))
    if not parsed:
        return
    row_count = len(parsed)
    praise_rows = sum(1 for row in parsed if row[3])
    complaint_rows = sum(1 for row in parsed if row[4])
    note_conditions = (
        [_SAMPLE_SHORT_NOTE] if row_count < 2 else [_SAMPLE_UNKNOWN_NOTE]
    )
    praise_conditions = _feedback_conditions(row_count, praise_rows)
    complaint_conditions = _feedback_conditions(row_count, complaint_rows)
    for record, span_id, review, praise, complaints, as_of in parsed:
        if praise:
            _append_obs(draft, "common_praise", _observation(
                record, span_id, review, fact_kind=_KIND_FEEDBACK,
                items=praise, status=_STATUS_COND, as_of=as_of,
                conditions=praise_conditions,
            ))
        if complaints:
            _append_obs(draft, "common_complaints", _observation(
                record, span_id, review, fact_kind=_KIND_FEEDBACK,
                items=complaints, status=_STATUS_COND, as_of=as_of,
                conditions=complaint_conditions,
            ))
        _append_obs(draft, "feedback_sample_note", _observation(
            record, span_id, review, fact_kind=_KIND_FEEDBACK,
            raw_value=review, status=_STATUS_COND, as_of=as_of,
            conditions=list(note_conditions),
        ))
