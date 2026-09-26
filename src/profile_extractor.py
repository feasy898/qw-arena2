# -*- coding: utf-8 -*-
"""src/profile_extractor.py — 用户画像抽取（模型环节）。

契约（contracts/interfaces.md §7）：
- extract_profile(user_text, gateway, config, user_description_file=None) -> dict：
  画像对象，key 见 contracts/field_catalog.json
  profile.groups[].fields[].key（扁平；
  scenes 为 list[dict]，item key 见 item_fields[].key）；
  末位可选参数 user_description_file 传用户描述文件名时，代码层在模型输出
  校验后注入 profile_id=User_Description_{n}（编号即文件名中的 n，官方
  value_rule）；不传（None）行为与既有签名完全一致；
- 原子化：一句话含多个事实拆成可独立核验的原子记录（SPEC §6）；
- 硬约束准入与场景优先级按 contracts/rules.json（约(单值)预算不得直接成硬上限）；
- 偏好只收原文明确表达；时长/频率缺失写「未提供」，不常识补全。

实现约定（本模块固化，供管线/测试对齐）：
- Prompt 模板：prompts/profile_extraction.md，占位符 {{USER_TEXT}} 由本模块替换；
- mock 模式固定 mock_key = MOCK_KEY = "profile_extraction"（interfaces.md §17）；
- 校验失败（结构/枚举非法）→ 追加一条「修复指令」重试一次（任务口径：失败一次
  修复重试）；仍失败 raise GatewayError。JSON 语法层修复由 gateway.chat_json 内部
  的「请修复 JSON」重试承担，本模块只做业务结构校验；
- 归一化：标量缺失 → None；列表缺失 → []；枚举非法 → 落到该字段目录的缺失表达；
  不抛异常（单字段异常不拖垮整份画像，记 warning 日志）；
- extract_profile_bundle() 一次网关调用同时返回（画像对象, UserNeed 列表），
  满足任务「profile_extractor 产出 UserNeed 列表+画像字段」；extract_profile 为其
  画像半边（契约签名）。UserNeed.evidence_refs 由代码把模型给的逐字引文定位回
  用户描述原文行片段（EvidenceRef.source_id 按 input_adapter 约定由 profile_id 推导）；
- derive_user_needs() 为确定性兜底派生（预算口径/场景目标），不依赖模型输出
  needs；约束判定主链（constraint_engine.build_needs，契约 §12）不受影响。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from src.input_adapter import line_spans
from src.model_gateway import GatewayError
from src.schemas import EvidenceRef, NeedType, UserNeed

logger = logging.getLogger("qw.profile_extractor")

PROFILE_PROMPT = "prompts/profile_extraction.md"
"""画像抽取 Prompt 模板路径（相对仓库根；prompts/ 归 Prompt 负责人，本模块只读取）。"""

MOCK_KEY = "profile_extraction"
"""mock 模式固定夹具键（interfaces.md §17）。"""

REPO_ROOT = Path(__file__).resolve().parent.parent
FIELD_CATALOG_PATH = REPO_ROOT / "contracts" / "field_catalog.json"
USER_TEXT_PLACEHOLDER = "{{USER_TEXT}}"

PROFILE_ID_RE = re.compile(r"^User_Description_\d+$")
PROFILE_FILENAME_NUMBER_RE = re.compile(r"User_Description_(\d+)")
_NUMBER_RE = re.compile(r"^-?\d+(?:\.\d+)?$")

REPAIR_INSTRUCTION = (
    "上一次输出未通过结构校验：{detail}。请重新输出：只输出一个合法 JSON 对象，"
    "键集合与字段规则严格按系统约定，不要 Markdown 代码围栏，不要解释文字。"
)

_NECESSARY_SCENE_KEYS = ("scene_name", "usage_duration", "usage_frequency",
                         "scene_priority", "priority_basis", "scene_evidence")

# field_catalog 中的枚举与缺失表达（与 contracts/field_catalog.json 保持同步）
GENDER_ENUM = ("男", "女", "未提供")
CURRENCY_ENUM = ("元", "未提供")
BUDGET_SEMANTICS_ENUM = ("区间", "约(单值)", "上限", "未明确")
PRIORITY_BASIS_ENUM = ("原文明示排序", "原文主次措辞", "优先级未明确")
SCALARS = ("profile_id", "name", "gender", "age", "occupation", "city",
           "product_category", "desired_product_type", "budget_min", "budget_max",
           "currency", "budget_raw", "budget_semantics", "ecosystem", "ecosystem_notes")
LISTS = ("purchase_purposes", "devices", "brand_preferences", "brand_avoidances",
         "appearance_preferences", "sound_preferences", "functional_preferences",
         "service_preferences", "other_preferences")
LIST_KEY = "scenes"
NEEDS_KEY = "needs"
NEED_TYPES = tuple(t.value for t in NeedType)


# ---------------------------------------------------------------------------
# 契约入口
# ---------------------------------------------------------------------------

def extract_profile(user_text: str, gateway, config: dict,
                    user_description_file: str | None = None) -> dict:
    """将用户自然语言描述抽取为画像对象（契约签名）。

    :param user_text: 用户描述原文（唯一信息来源）
    :param gateway: ModelGateway 实例
    :param config: resolve_config 产出的配置
    :param user_description_file: 用户描述文件名（如 User_Description_1.txt，
        也可传完整路径）；仅用于按官方 value_rule 解析画像编号注入 profile_id，
        缺省 None 时行为与不传完全一致
    :return: 画像对象 dict（扁平 key，结构化列表 scenes 为 list[dict]）
    :raises src.model_gateway.GatewayError: 模型输出经有限重试仍不合法
    """
    return extract_profile_bundle(user_text, gateway, config,
                                  user_description_file=user_description_file)[0]


def extract_profile_bundle(user_text: str, gateway, config: dict,
                           user_description_file: str | None = None
                           ) -> tuple[dict, list[UserNeed]]:
    """画像抽取 + 原子化 UserNeed 派生（一次网关调用）。

    :param user_description_file: 用户描述文件名/路径（可选）；编号解析规则见
        profile_id_from_filename，解析成功时在「模型输出校验之后、渲染之前」
        注入 profile_id（官方机测 Prompt 输入为 User_Description_n.txt，编号
        即文件名中的 n，代码层注入比模型自报更可靠）
    :return: (画像对象, UserNeed 列表)；UserNeed 优先取模型输出的原子需求
        （证据引文由代码定位回原文行片段），模型未给出时用 derive_user_needs 兜底
    :raises src.model_gateway.GatewayError: 模型输出经一次修复重试仍不合法
    """
    text = user_text or ""
    started = time.monotonic()
    model = config.get("default_model") or "qwen3.6-plus"
    messages = [
        {"role": "system", "content": "你是严格按约定输出 JSON 的信息抽取器。"},
        {"role": "user", "content": render_profile_prompt(text)},
    ]
    attempts = 1 + max(0, int(config.get("max_retries_per_request", 2) or 0))
    # 任务口径「失败一次修复重试」：无论配额多大，业务结构修复只重试一次
    attempts = min(attempts, 2)
    last_error = "未知错误"
    for attempt in range(1, attempts + 1):
        parsed = gateway.chat_json(messages, model, mock_key=MOCK_KEY)
        profile, needs, problems = _normalize_profile(parsed, text)
        if not problems:
            # 画像编号由代码层注入（field_catalog value_rule：取输入文件名编号
            # User_Description_{n}）。注入点在「模型输出结构校验之后、渲染之前」：
            # 不参与模型输出校验与修复重试判定，mock 夹具（模型输出形态）无需改动；
            # 文件名是权威来源，覆盖模型自报值；解析不出则保持模型归一化结果
            # （profile_id 为空 → 渲染「未提供」，与既有行为一致）。防提示注入
            # 纪律：文件名只用于提取编号，绝不进入提示词正文。
            injected_id = profile_id_from_filename(user_description_file)
            if injected_id is not None:
                profile["profile_id"] = injected_id
            logger.info("画像抽取成功 attempt=%d 耗时=%.2fs 需求=%d",
                        attempt, time.monotonic() - started, len(needs))
            return profile, needs
        last_error = "；".join(problems[:5])
        logger.warning("画像输出结构校验失败 attempt=%d problems=%s", attempt, last_error)
        messages = list(messages) + [
            {"role": "assistant",
             "content": json.dumps(parsed, ensure_ascii=False)},
            {"role": "user", "content": REPAIR_INSTRUCTION.format(detail=last_error)},
        ]
    raise GatewayError(f"画像抽取输出经一次修复重试仍不合法：{last_error}")


def render_profile_prompt(user_text: str) -> str:
    """读取 Prompt 模板并替换 {{USER_TEXT}}（模板缺失/非法 raise GatewayError）。"""
    path = REPO_ROOT / PROFILE_PROMPT
    try:
        template = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GatewayError(f"画像抽取 Prompt 模板缺失: {path}（{exc}）") from exc
    if USER_TEXT_PLACEHOLDER not in template:
        raise GatewayError(f"画像抽取 Prompt 模板缺少占位符 {USER_TEXT_PLACEHOLDER}: {path}")
    return template.replace(USER_TEXT_PLACEHOLDER, user_text or "")


def profile_id_from_filename(user_description_file: str | None) -> str | None:
    """从用户描述文件名（或完整路径）解析画像编号（官方 value_rule）。

    field_catalog profile.groups[标识].value_rule：「取输入文件名编号：
    User_Description_{n}（n 为文件名中的数字）」。取基名后按
    PROFILE_FILENAME_NUMBER_RE 提取 n，返回 "User_Description_n"。

    防提示注入纪律：文件名只用于提取编号，绝不进入提示词正文。
    :return: 如 "User_Description_3"；入参为空或解析不出编号 → None
        （调用方保持既有行为，渲染「未提供」）
    """
    if not user_description_file:
        return None
    basename = Path(str(user_description_file)).name
    match = PROFILE_FILENAME_NUMBER_RE.search(basename)
    if not match:
        return None
    return f"User_Description_{match.group(1)}"


def derive_user_needs(user_text: str, profile: dict) -> list[UserNeed]:
    """从画像对象确定性派生原子 UserNeed（不依赖模型；本模块兜底口径）。

    规则（rules.json 硬约束准入 / SPEC §6）：
    - 预算：budget_semantics=「上限」→ 硬约束 <=budget_max；「约(单值)」→ 歧义
      （不得直接当硬上限）；「区间」→ 硬约束 <=budget_max（+软偏好 >=budget_min）；
    - 场景：每个场景一条「目标」需求，priority 由场景优先级映射（数值越大越高，
      未明确=0）；证据取 scene_evidence / budget_raw / 偏好原文逐字引文。
    """
    text = user_text or ""
    needs: list[UserNeed] = []
    idx = 0

    def _add(expression, need_type, operator, expected, priority, quote):
        nonlocal idx
        idx += 1
        needs.append(UserNeed(
            need_id=f"derived_need_{idx:03d}",
            original_expression=expression,
            need_type=need_type,
            operator=operator,
            expected_value=expected,
            priority=priority,
            evidence_refs=_locate_evidence(text, quote),
        ))

    semantics = profile.get("budget_semantics")
    budget_max = _to_number(profile.get("budget_max"))
    budget_min = _to_number(profile.get("budget_min"))
    budget_quote = profile.get("budget_raw") or ""
    if budget_max is not None:
        if semantics == "上限":
            _add(f"预算上限 {budget_max} 元", NeedType.HARD, "<=", budget_max, 8, budget_quote)
        elif semantics == "约(单值)":
            _add(f"预算约 {budget_max} 元（约(单值)不得直接当硬上限）",
                 NeedType.AMBIGUOUS, "<=", budget_max, 8, budget_quote)
        elif semantics == "区间":
            _add(f"预算不超过 {budget_max} 元", NeedType.HARD, "<=", budget_max, 8, budget_quote)
            if budget_min is not None:
                _add(f"预算不低于 {budget_min} 元", NeedType.SOFT, ">=", budget_min, 3, budget_quote)
        else:
            _add(f"预算 {budget_max} 元（口径未明确）", NeedType.AMBIGUOUS, None, budget_max, 5, budget_quote)

    scenes = [s for s in (profile.get("scenes") or []) if isinstance(s, dict)]
    ranked = [s for s in scenes if isinstance(s.get("scene_priority"), int)]
    total_ranked = len(ranked)
    for scene in scenes:
        name = str(scene.get("scene_name") or "").strip()
        if not name:
            continue
        rank = scene.get("scene_priority") if isinstance(scene.get("scene_priority"), int) else None
        priority = (total_ranked - rank + 1) if rank is not None else 0
        parts = [name]
        if scene.get("usage_frequency"):
            parts.append(f"频率：{scene['usage_frequency']}")
        if scene.get("usage_duration"):
            parts.append(f"时长：{scene['usage_duration']}")
        _add("，".join(parts), NeedType.GOAL, None, None, priority,
             str(scene.get("scene_evidence") or name))

    for key in ("functional_preferences", "sound_preferences", "appearance_preferences",
                "service_preferences", "other_preferences"):
        for item in profile.get(key) or []:
            item_text = str(item).strip()
            if item_text:
                _add(item_text, NeedType.SOFT, None, None, 2, item_text)
    return needs


# ---------------------------------------------------------------------------
# 归一化与校验
# ---------------------------------------------------------------------------

def _normalize_profile(parsed: dict, user_text: str) -> tuple[dict, list[UserNeed], list[str]]:
    """模型输出 → (画像对象, UserNeed 列表, 结构问题列表)。

    结构问题非空表示需要修复重试；单字段落不进枚举/数值只降级 + warning，不触发重试。
    """
    if not isinstance(parsed, dict):
        return {}, [], ["顶层必须是 JSON 对象"]
    known_keys = set(SCALARS) | set(LISTS) | {LIST_KEY}
    if not known_keys.intersection(parsed):
        # 键集完全对不上 = 结构级不合格，必须修复重试（防空对象静默通过）
        return {}, [], ["输出不含任何画像字段键"]
    problems: list[str] = []
    profile: dict = {key: None for key in SCALARS}
    for key in LISTS:
        profile[key] = []
    profile[LIST_KEY] = []

    for key in SCALARS:
        value = parsed.get(key)
        if value is None:
            continue
        if key == "profile_id":
            text = str(value).strip()
            profile[key] = text if PROFILE_ID_RE.match(text) else None
            if not PROFILE_ID_RE.match(text):
                logger.warning("profile_id 不符合 User_Description_{n} 约定，置空: %r", text)
            continue
        if key in ("age", "budget_min", "budget_max"):
            number = _to_number(value)
            if number is None:
                logger.warning("%s 不是数值，置空: %r", key, value)
            else:
                profile[key] = number
            continue
        if key == "gender":
            profile[key] = value if value in GENDER_ENUM else "未提供"
            continue
        if key == "currency":
            profile[key] = value if value in CURRENCY_ENUM else "未提供"
            continue
        if key == "budget_semantics":
            if value in BUDGET_SEMANTICS_ENUM:
                profile[key] = value
            else:
                derived = _derive_budget_semantics(profile.get("budget_raw"))
                logger.warning("budget_semantics 非法（%r），按预算原文推导为 %r", value, derived)
                profile[key] = derived
            continue
        text = str(value).strip()
        profile[key] = text or None

    for key in LISTS:
        profile[key] = _string_list(parsed.get(key))

    # 真实模型联调兜底（2026-09-24 run #3 实测）：模型可能漏填 budget_max/budget_min
    # （原文「预算2000元左右」，budget_raw/budget_semantics 正确而 budget_max=null），
    # 决策层因此没有任何预算需求可判定。预算数值从 budget_raw（原文逐字表述）确定性
    # 提取，不引入资料外信息；模型已填数值时不覆盖。
    if profile.get("budget_max") is None:
        budget_text = str(profile.get("budget_raw") or "")
        range_match = re.search(r"(\d+(?:\.\d+)?)\s*[-—~至到]\s*(\d+(?:\.\d+)?)", budget_text)
        if range_match:
            profile["budget_min"] = _to_number(range_match.group(1))
            profile["budget_max"] = _to_number(range_match.group(2))
            logger.warning("budget_max 缺失，已从预算原文区间回填：min=%s max=%s",
                           profile["budget_min"], profile["budget_max"])
        else:
            single = re.search(r"\d+(?:\.\d+)?", budget_text)
            if single:
                profile["budget_max"] = _to_number(single.group(0))
                logger.warning("budget_max 缺失，已从预算原文单值回填：%s",
                               profile["budget_max"])

    scenes: list[dict] = []
    raw_scenes = parsed.get(LIST_KEY)
    if raw_scenes is not None and not isinstance(raw_scenes, list):
        problems.append(f"{LIST_KEY} 必须是数组")
        raw_scenes = []
    for item in raw_scenes or []:
        if not isinstance(item, dict):
            problems.append(f"{LIST_KEY} 元素必须是对象")
            continue
        scene = {}
        empty = True
        for item_key in _NECESSARY_SCENE_KEYS:
            value = item.get(item_key)
            if item_key == "scene_priority":
                scene[item_key] = value if isinstance(value, int) and not isinstance(value, bool) else None
            elif item_key == "priority_basis":
                scene[item_key] = value if value in PRIORITY_BASIS_ENUM else "优先级未明确"
            else:
                scene[item_key] = str(value).strip() if value is not None else None
            empty = empty and not scene[item_key]
        if not empty:
            scenes.append(scene)
    # 场景按优先级从高到低排序（field_catalog value_rule；未明确的排后）
    scenes.sort(key=lambda s: s["scene_priority"] if s["scene_priority"] is not None else 10**9)
    profile[LIST_KEY] = scenes

    needs, need_problems = _normalize_needs(parsed.get(NEEDS_KEY), user_text, profile)
    problems.extend(need_problems)
    return profile, needs, problems


def _normalize_needs(raw_needs, user_text: str,
                     profile: dict) -> tuple[list[UserNeed], list[str]]:
    """模型输出的原子需求 → UserNeed 列表（证据引文定位回原文行片段）。"""
    problems: list[str] = []
    needs: list[UserNeed] = []
    if raw_needs is None:
        return needs, problems
    if not isinstance(raw_needs, list):
        return needs, [f"{NEEDS_KEY} 必须是数组"]
    source_id = _user_source_id(profile.get("profile_id"))
    for index, item in enumerate(raw_needs, 1):
        if not isinstance(item, dict):
            problems.append(f"{NEEDS_KEY}[{index}] 必须是对象")
            continue
        expression = str(item.get("original_expression") or "").strip()
        need_type = item.get("need_type")
        if not expression:
            problems.append(f"{NEEDS_KEY}[{index}] 缺 original_expression")
            continue
        if need_type not in NEED_TYPES:
            problems.append(f"{NEEDS_KEY}[{index}] need_type 非法: {need_type!r}")
            continue
        quote = str(item.get("evidence_quote") or "").strip()
        needs.append(UserNeed(
            need_id=str(item.get("need_id") or f"need_{len(needs) + 1:03d}"),
            original_expression=expression,
            need_type=NeedType(need_type),
            operator=item.get("operator"),
            expected_value=item.get("expected_value"),
            priority=item.get("priority") if isinstance(item.get("priority"), int)
            and not isinstance(item.get("priority"), bool) else 0,
            evidence_refs=_locate_evidence(user_text, quote, source_id),
        ))
    return needs, problems


def _locate_evidence(user_text: str, quote: str,
                     source_id: str = "00_User_Descriptions") -> list[EvidenceRef]:
    """把逐字引文定位回用户描述原文的行片段（与 input_adapter.span 约定一致）。

    引文未命中（模型改写）时返回空列表并记 warning，不编造 span。
    """
    if not quote or not user_text:
        return []
    position = user_text.find(quote)
    if position < 0:
        squeezed = "".join(str(user_text).split())
        hit = squeezed.find("".join(quote.split()))
        if hit >= 0:  # 仅空白差异：仍可定位回原行
            position = _map_squeezed_offset(user_text, hit)
    if position < 0:
        logger.warning("需求引文未能在用户描述原文中逐字定位（不编造 span）：%s", quote[:30])
        return []
    for span_id, start, end in line_spans(user_text):
        if start <= position < end or (start == end == position):
            return [EvidenceRef(source_id=source_id, span_id=span_id, exact_quote=quote)]
    return [EvidenceRef(source_id=source_id, span_id="L1", exact_quote=quote)]


def _map_squeezed_offset(text: str, squeezed_index: int) -> int:
    """把「去空白后文本」的偏移映射回原文偏移。"""
    seen = 0
    for index, ch in enumerate(text):
        if seen == squeezed_index:
            return index
        if not ch.isspace():
            seen += 1
    return -1


def _user_source_id(profile_id) -> str:
    """用户描述 SourceRecord 的 source_id（input_adapter 约定：相对路径）。"""
    if isinstance(profile_id, str) and PROFILE_ID_RE.match(profile_id):
        return f"00_User_Descriptions/{profile_id}.txt"
    return "00_User_Descriptions"


def _string_list(value) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        return [str(value).strip()] if str(value).strip() else []
    return [str(item).strip() for item in value if str(item).strip()]


def _to_number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if isinstance(value, str) and _NUMBER_RE.match(value.strip()):
        text = value.strip()
        return float(text) if "." in text else int(text)
    return None


def _derive_budget_semantics(budget_raw) -> str:
    """预算原文口径的确定性推导（枚举非法时的兜底，rules.json budget_semantics）。"""
    text = str(budget_raw or "")
    if not text:
        return "未明确"
    if re.search(r"\d\s*[-—~至到]\s*\d", text):
        return "区间"
    if re.search(r"以内|不超过|上限|最多", text):
        return "上限"
    if re.search(r"约|左右|大概|大约|上下", text):
        return "约(单值)"
    if re.search(r"\d", text):
        return "约(单值)"  # 单值数字且无口径词：按约(单值)处理，不当硬上限
    return "未明确"
