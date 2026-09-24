# -*- coding: utf-8 -*-
"""src/product_extractor.py — 五源产品事实抽取（模型环节）。

契约（contracts/interfaces.md §8）：
- extract_products(products, source_index, gateway, config) -> dict[str, dict]；
- 逐产品（canonical_id 升序）汇集五源 SourceRecord，产出产品对象草稿
  （key 见 contracts/field_catalog.json product.groups[].fields[].key）；
- FactCell 携带 fact_kind（官网声明|实测观察|用户反馈|宣传表述|有依据归纳）与
  status（有支持|附条件|存在冲突|输入缺失），kind 与 status 必须分开（SPEC §4）；
- 冲突判定不在此做，留给 reconciler（冲突三步法）。

实现约定（本模块固化，供管线/测试/渲染器对齐）：
- 草稿形态（扁平，与 §0「产品对象」一致）：
    {"canonical_id": "P001", "aliases": [...], "product_name": ..., "brand": ...,
     "model": ...,  <36 个 field_catalog 产品字段键>: [observation, ...]}
  observation 为 dict：普通字段含 raw_value/normalized_value/unit/fact_kind/status/
  conditions/applicable_variant/as_of/evidence_refs；列表型字段（core_functions、
  audio_formats、variants、accessories_fit、special_features、marketing_claims、
  common_praise、common_complaints）用 items 代替取值三键；
- 同一字段的多条 observation 并列返回（多来源/多口径），归并交给 reconciler；
- Prompt 模板：prompts/product_extraction.md，占位符 {{PRODUCT_ID}}/
  {{PRODUCT_ALIASES}}/{{SOURCES_BLOCK}}；sources_block 逐行渲染
  「[L行号] 原文」，source_id 在来源标题行给出，供 evidence_refs 回指；
- 校验（每次模型应答全量执行）：键集 ⊆ field_catalog、observation 结构合法、
  fact_kind/status ∈ 枚举、evidence_refs 的 source_id/span_id/exact_quote 可回溯到
  本次提供的 SourceRecord。不合格 → 追加修复指令重试一次（任务口径），仍不合格
  raise GatewayError；JSON 语法层修复由 gateway.chat_json 承担；
- 身份字段（brand/model）：以 registry 基础标识为基准做「去空白 casefold 互为子串」
  兼容校验（与 product_registry 同口径），兼容时采信模型给出的官网型号块写法
  （field_catalog：以官网型号为主值），不兼容时回落 registry 值；
  aliases/canonical_id 由代码确定性填充（registry 权威，不采信模型）。
"""
from __future__ import annotations

import json
import logging
import re
import time
from pathlib import Path

from src.model_gateway import GatewayError
from src.schemas import FactKind, FactStatus

logger = logging.getLogger("qw.product_extractor")

_IDENTITY_WS_RE = re.compile(r"\s+")
"""身份比较用空白正则（与 product_registry._identity_form 同口径，避免跨模块私用）。"""

PRODUCT_PROMPT = "prompts/product_extraction.md"
"""产品抽取 Prompt 模板路径（相对仓库根）。"""

REPO_ROOT = Path(__file__).resolve().parent.parent
USER_TEXT_PLACEHOLDER = "{{PRODUCT_ID}}"
ALIASES_PLACEHOLDER = "{{PRODUCT_ALIASES}}"
SOURCES_PLACEHOLDER = "{{SOURCES_BLOCK}}"

# 列表型字段（observation 用 items；渲染/归并按字符串列表处理）
LIST_FIELDS = frozenset({
    "core_functions", "audio_formats", "variants", "accessories_fit",
    "special_features", "marketing_claims", "common_praise", "common_complaints",
})
STRUCTURED_FIELDS = frozenset({"battery_by_mode"})
OBS_COMMON_KEYS = ("conditions", "applicable_variant", "as_of", "evidence_refs",
                   "fact_kind", "status")
OBS_VALUE_KEYS = ("raw_value", "normalized_value", "unit")
OBS_ITEMS_KEY = "items"

REPAIR_INSTRUCTION = (
    "上一次输出未通过校验：{detail}。请修复后重新输出：只输出一个合法 JSON 对象，"
    "fields 必须覆盖全部约定字段键，每条 observation 的 evidence_refs 必须能逐字"
    "回溯到给出的 [L行号] 原文，不要 Markdown 代码围栏，不要解释文字。"
)


def _mock_key(canonical_id: str) -> str:
    """产品抽取的 mock 夹具键：product_extraction_{canonical_id}。"""
    return f"product_extraction_{canonical_id}"


def extract_products(products: list, source_index, gateway, config: dict) -> dict[str, dict]:
    """抽取全部产品的事实草稿。

    :param products: ProductRecord 列表（registry 产出，仅基础标识）
    :param source_index: SourceIndex（按 canonical_id 取五源记录）
    :param gateway: ModelGateway 实例
    :param config: resolve_config 产出的配置
    :return: {canonical_id: 产品对象草稿}（canonical_id 升序处理）
    :raises src.model_gateway.GatewayError: 模型输出经一次修复重试仍不合法
    """
    ordered = sorted(products, key=lambda p: p.canonical_id)
    drafts: dict[str, dict] = {}
    started = time.monotonic()
    for product in ordered:
        records = source_index.by_product(product.canonical_id)
        if not records:
            logger.warning("产品 %s 无任何来源记录，产出空草稿（不调用模型）",
                           product.canonical_id)
            drafts[product.canonical_id] = _empty_draft(product)
            continue
        drafts[product.canonical_id] = _extract_one(product, records, gateway, config)
    logger.info("产品抽取完成 款数=%d 耗时=%.2fs", len(drafts), time.monotonic() - started)
    return drafts


def render_product_prompt(canonical_id: str, aliases: list[str],
                          records: list) -> str:
    """读取 Prompt 模板并替换占位符；渲染五源逐行文本（模板缺失/非法 raise GatewayError）。"""
    path = REPO_ROOT / PRODUCT_PROMPT
    try:
        template = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise GatewayError(f"产品抽取 Prompt 模板缺失: {path}（{exc}）") from exc
    for placeholder in (USER_TEXT_PLACEHOLDER, ALIASES_PLACEHOLDER, SOURCES_PLACEHOLDER):
        if placeholder not in template:
            raise GatewayError(f"产品抽取 Prompt 模板缺少占位符 {placeholder}: {path}")
    block = render_sources_block(records)
    return (template
            .replace(USER_TEXT_PLACEHOLDER, canonical_id)
            .replace(ALIASES_PLACEHOLDER, json.dumps(aliases, ensure_ascii=False))
            .replace(SOURCES_PLACEHOLDER, block))


def render_sources_block(records: list) -> str:
    """SourceRecord 列表 → 逐行带 [L行号] 前缀的资料文本（确定性顺序）。"""
    def sort_key(record):
        return (str(record.source_type), record.source_id)

    parts: list[str] = []
    for record in sorted(records, key=sort_key):
        header = (f"#### source_id={record.source_id}（类型：{record.source_type}）")
        lines = [header]
        span_map = {span_id: (start, end) for span_id, start, end in record.spans}
        for span_id, (start, end) in span_map.items():
            lines.append(f"[{span_id}] {record.original_text[start:end]}")
        parts.append("\n".join(lines))
    return "\n\n".join(parts)


# ---------------------------------------------------------------------------
# 单产品抽取
# ---------------------------------------------------------------------------

def _extract_one(product, records: list, gateway, config: dict) -> dict:
    canonical_id = product.canonical_id
    model = config.get("default_model") or "qwen3.6-plus"
    messages = [
        {"role": "system", "content": "你是严格按约定输出 JSON 的信息抽取器。"},
        {"role": "user",
         "content": render_product_prompt(canonical_id, list(product.original_ids), records)},
    ]
    # 任务口径「失败一次修复重试」：业务结构/证据校验失败只重试一次
    attempts = 2
    source_index_by_id = {record.source_id: record for record in records}
    last_error = "未知错误"
    for attempt in range(1, attempts + 1):
        parsed = gateway.chat_json(messages, model, mock_key=_mock_key(canonical_id))
        draft, problems = _normalize_product(parsed, product, source_index_by_id)
        if not problems:
            logger.info("产品 %s 抽取成功 attempt=%d 字段=%d",
                        canonical_id, attempt, len(draft))
            return draft
        last_error = "；".join(problems[:5])
        logger.warning("产品 %s 输出校验失败 attempt=%d problems=%s",
                       canonical_id, attempt, last_error)
        messages = list(messages) + [
            {"role": "assistant", "content": json.dumps(parsed, ensure_ascii=False)},
            {"role": "user", "content": REPAIR_INSTRUCTION.format(detail=last_error)},
        ]
    raise GatewayError(f"产品 {canonical_id} 抽取输出经一次修复重试仍不合法：{last_error}")


def _empty_draft(product) -> dict:
    """无来源产品的空草稿（全部字段键为空 observation 列表，由渲染层落缺失表达）。"""
    return _base_draft(product)


def empty_draft(product) -> dict:
    """空草稿的公共出口（src/pipeline 降级路径使用：token 超限/mock 缺键时
    以缺失表达落表，未知如实保留、不丢产品；字段键全集与 _base_draft 一致）。"""
    return _base_draft(product)


def _base_draft(product) -> dict:
    draft = {}
    for field_key in _field_keys():
        draft[field_key] = []
    # 身份字段在字段循环之后落位（registry 权威；field_catalog 的身份键不参与观察归并）
    draft["canonical_id"] = product.canonical_id
    draft["aliases"] = sorted(product.original_ids)
    draft["product_name"] = None
    draft["brand"] = product.brand_raw
    draft["model"] = product.model_raw
    return draft


_CATALOG_CACHE: list[str] | None = None


def _field_keys() -> list[str]:
    """field_catalog.product 全部子字段键（缓存；契约文件缺失/非法 raise GatewayError）。"""
    global _CATALOG_CACHE
    if _CATALOG_CACHE is None:
        path = REPO_ROOT / "contracts" / "field_catalog.json"
        try:
            catalog = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise GatewayError(f"field_catalog.json 读取失败: {path}（{exc}）") from exc
        try:
            keys = [f["key"] for group in catalog["product"]["groups"] for f in group["fields"]]
        except (KeyError, TypeError) as exc:
            raise GatewayError(f"field_catalog.json 结构非法: {exc}") from exc
        _CATALOG_CACHE = keys
    return list(_CATALOG_CACHE)


_FACT_KINDS = frozenset(k.value for k in FactKind)
_FACT_STATUSES = frozenset(s.value for s in FactStatus)


def _normalize_product(parsed, product, records_by_id: dict) -> tuple[dict, list[str]]:
    """模型输出 → (扁平草稿, 问题列表)。结构/证据问题触发修复重试。"""
    problems: list[str] = []
    if not isinstance(parsed, dict):
        return _base_draft(product), ["顶层必须是 JSON 对象"]
    fields_raw = parsed.get("fields")
    if not isinstance(fields_raw, dict):
        return _base_draft(product), ["fields 必须是对象"]

    draft = _base_draft(product)
    known = set(_field_keys())

    unknown = [key for key in fields_raw if key not in known]
    if unknown:
        problems.append(f"fields 含未知字段键: {sorted(unknown)[:5]}")

    for field_key in known:
        if field_key == "aliases":
            # aliases 是编号字符串列表（提示词明示如 ["P001","P01"]，程序并集校对），
            # 不是观察对象列表：豁免出通用观察循环。真实模型联调（2026-09-24）发现：
            # 真实模型按提示词返回普通字符串数组时，通用循环误报「必须是对象」，
            # 触发无谓修复重试（mock 夹具该键为 []，从未暴露）。
            continue
        observations = fields_raw.get(field_key)
        if observations is None:
            continue  # 模型未给 → 空列表（未提供）
        if not isinstance(observations, list):
            problems.append(f"{field_key} 必须是数组")
            continue
        normalized: list[dict] = []
        for index, obs in enumerate(observations, 1):
            clean, obs_problems = _normalize_observation(
                field_key, index, obs, records_by_id, bool(field_key in LIST_FIELDS))
            normalized.extend(clean)
            problems.extend(f"{field_key}[{index}] {p}" for p in obs_problems)
        draft[field_key] = normalized

    # 身份字段：模型给值 + registry 基准兼容校验（原样沿用，不采信改写）
    name = _clean_text(parsed.get("product_name"))
    draft["product_name"] = name or product.model_raw
    brand = _clean_text(parsed.get("brand"))
    draft["brand"] = _pick_identity(brand, product.brand_raw) or product.brand_raw
    model_value = _clean_text(parsed.get("model"))
    draft["model"] = _pick_identity(model_value, product.model_raw) or product.model_raw
    # aliases：registry 权威并集模型在 fields.aliases 中给出的编号写法（不采信改写）；
    # 形态兼容：提示词约定是普通字符串数组，模型若误给观察对象（items）也照收其 items
    model_aliases: list[str] = []
    for entry in (fields_raw.get("aliases") or []):
        if isinstance(entry, str):
            text = entry.strip()
            if text:
                model_aliases.append(text)
        elif isinstance(entry, dict):
            for item in (entry.get("items") or []):
                text = str(item).strip()
                if text:
                    model_aliases.append(text)
    draft["aliases"] = sorted({*product.original_ids, *model_aliases})
    if product.model_raw and model_value and not _identity_compatible(model_value, product.model_raw):
        problems.append(
            f"model 与登记型号不一致（模型输出 {model_value!r}，登记 {product.model_raw!r}）")
    return draft, problems


def _normalize_observation(field_key: str, index: int, obs, records_by_id: dict,
                           is_list_field: bool) -> tuple[list[dict], list[str]]:
    """单条 observation 校验/归一：非法条目丢弃并返回问题（触发修复重试）。"""
    problems: list[str] = []
    if not isinstance(obs, dict):
        return [], ["必须是对象"]
    kind = obs.get("fact_kind")
    status = obs.get("status")
    if kind not in _FACT_KINDS:
        problems.append(f"fact_kind 非法: {kind!r}")
    if status not in _FACT_STATUSES:
        problems.append(f"status 非法: {status!r}")
    evidence, evidence_problems = _normalize_evidence(
        obs.get("evidence_refs"), records_by_id)
    problems.extend(evidence_problems)
    if problems:
        return [], problems

    clean = {
        "fact_kind": kind,
        "status": status,
        "conditions": [str(c).strip() for c in obs.get("conditions") or [] if str(c).strip()],
        "applicable_variant": _clean_text(obs.get("applicable_variant")),
        "as_of": _clean_text(obs.get("as_of")),
        "evidence_refs": evidence,
    }
    if is_list_field:
        items = [str(i).strip() for i in obs.get("items") or [] if str(i).strip()]
        if not items:
            return [], []  # 空条目无信息，丢弃不计为问题
        clean["items"] = items
    else:
        raw_value = obs.get("raw_value")
        clean["raw_value"] = _clean_text(raw_value) if isinstance(raw_value, str) else raw_value
        clean["normalized_value"] = obs.get("normalized_value")
        clean["unit"] = _clean_text(obs.get("unit"))
        if clean["raw_value"] is None and clean["status"] == FactStatus.SUPPORTED.value \
                and field_key != "battery_by_mode" and not clean["conditions"]:
            # 「无取值却称有支持」= 待核验型/缺失型表述，须降级为附条件，防止空值当事实
            clean["status"] = FactStatus.CONDITIONAL.value
            clean["conditions"] = ["仅有相关表述，无统一口径（待核验）"]
    return [clean], []


def _normalize_evidence(raw_refs, records_by_id: dict) -> tuple[list[dict], list[str]]:
    """evidence_refs 校验：source_id ∈ 本次提供记录、span_id 存在、exact_quote 逐字命中。"""
    problems: list[str] = []
    evidence: list[dict] = []
    if raw_refs is None:
        return evidence, []
    if not isinstance(raw_refs, list):
        return [], ["evidence_refs 必须是数组"]
    for index, ref in enumerate(raw_refs, 1):
        if not isinstance(ref, dict):
            problems.append(f"evidence_refs[{index}] 必须是对象")
            continue
        source_id = str(ref.get("source_id") or "").strip()
        span_id = str(ref.get("span_id") or "").strip()
        quote = str(ref.get("exact_quote") or "").strip()
        record = records_by_id.get(source_id)
        if record is None:
            problems.append(f"evidence_refs[{index}] source_id 不在本次资料中: {source_id!r}")
            continue
        span_map = {s[0]: (s[1], s[2]) for s in record.spans}
        if span_id not in span_map:
            problems.append(f"evidence_refs[{index}] span_id 不存在: {span_id!r}")
            continue
        start, end = span_map[span_id]
        span_text = record.original_text[start:end]
        if quote not in span_text:
            # 带上 source_id/span_id 定位信息：真实模型的修复重试需要知道
            # 具体哪条引用错了（2026-09-24 真实联调：P001 引文挂错 span）
            problems.append(
                f"evidence_refs[{index}]（source_id={source_id!r}, span_id={span_id!r}）"
                f"exact_quote 无法逐字回溯: {quote[:30]!r}")
            continue
        evidence.append({"source_id": source_id, "span_id": span_id, "exact_quote": quote})
    return evidence, problems


def _clean_text(value):
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _identity_compatible(candidate: str, registered: str) -> bool:
    """与 product_registry 同口径的身份比较（去空白 casefold 互为子串）。"""
    a = _IDENTITY_WS_RE.sub("", candidate).casefold()
    b = _IDENTITY_WS_RE.sub("", registered).casefold()
    return a in b or b in a


def _pick_identity(model_value, registered):
    """身份字段取值：模型给出的官网写法优先（需与登记值身份兼容），否则用登记值。"""
    if model_value and (not registered or _identity_compatible(model_value, registered)):
        return model_value
    return registered
