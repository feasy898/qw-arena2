# -*- coding: utf-8 -*-
"""src/renderer.py — 程序模板渲染与回读（确定性代码，不用模型）。

契约（contracts/interfaces.md §10）：
- 表头/字段顺序/空值标记/节数/表结构全部由模板按 contracts/field_catalog.json 固定，
  不依赖模型；所有产品表格结构完全一致（同表头、同字段名序列、同 3 列；
  结构化列表按 field_catalog 展开为逐项行，项数随数据变化）；
- 缺失一律落成 field_catalog 的缺失表达（「未提供」/「待核验」/「优先级未明确」）；
- parse_* 与 render_* 往返：parse_X(render_X(obj)) 与 obj 取值语义等价
  （缺失表达文本解析回 None；单测保证）；
- render_recommendation 在有效产品不足 3 款时按 SPEC §9 异常路径渲染：
  仍恰好 3 行，空位行产品ID=「无」、理由含「当前尚不构成有效备选」，不虚构第三款。

对象形态约定（interfaces.md §0）：
- 画像对象：扁平 dict，key = field_catalog.profile 子字段 key；scenes 为 list[dict]
  （item key 见 item_fields）；
- 产品对象：内层 key = field_catalog.product 子字段 key；battery_by_mode 为 list[dict]；
- 取值可为本模块可直接渲染的标量（str/int/float/None/list[标量]），
  也可为 FactCell（或其 to_dict 形态）——render_fact_value 统一分发；
- parse_* 返回普通化对象（缺失 → None，列表 → list，数字 → int/float），
  供决策阶段回读（隔离铁律：决策层不接触 FactCell 之外的原始资料形态）。

渲染细节（确定性约定）：
- 单元格清洗：换行 →「；」、ASCII 竖线 →「／」；「｜」为引用格式保留分隔符；
- 列表取值以「；」连接；结构化列表逐项展开，项序号 {n} 按渲染顺序 1 起；
- scenes 按（有优先级在前、优先级数值升序、原序稳定）排序后编号；
- 产品节按 canonical_id 升序；节标题 = 「## P001 产品名称」。
"""
from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional, Union

from src.schemas import FactCell, FactStatus

PROFILE_FILENAME = "user_profile.md"
PRODUCTS_FILENAME = "product_list.md"
RECOMMENDATION_FILENAME = "recommendation.md"

FIELD_CATALOG_PATH = Path(__file__).resolve().parents[1] / "contracts" / "field_catalog.json"

TABLE_COLUMNS = ("字段组", "字段名", "取值")
PROFILE_TITLE = "# 用户画像"
PRODUCTS_TITLE = "# 产品属性列表"
RECOMMENDATION_TITLE = "# 消费推荐报告"
SECTION_RESULT = "推荐结果"
SECTION_EXCLUSION = "未选原因"

MISSING_NOT_PROVIDED = "未提供"
MISSING_VERIFY = "待核验"
PRIORITY_UNCLEAR = "优先级未明确"
NO_VALID_BACKUP = "当前尚不构成有效备选"
EMPTY_SLOT_ID = "无"
EMPTY_SLOT_NAME = "（当前尚不构成有效备选）"
LIST_SEP = "；"
MERGE_SEP = "、"
CONCLUSION_PREFIX = "综合结论："

REC_COLUMNS = ("推荐层级", "产品ID", "产品", "画像匹配与推荐理由", "注意事项")
EXCLUSION_COLUMNS = ("产品ID", "未进入前三的主要原因", "建议改变条件")
LEVELS = ("首选", "备选1", "备选2")

_HEADING_PROFILE_RE = re.compile(r"^# (.+)$")
_HEADING_PRODUCT_RE = re.compile(r"^## (P\d{3}) (.*)$")
_HEADING_SECTION_RE = re.compile(r"^## (.+)$")
_SCENE_ROW_RE = re.compile(r"^场景(\d+)·(.+)$")
_BATTERY_ROW_RE = re.compile(r"^续航(\d+)·(.+)$")
_BATTERY_FIELD_NAME = "续航（分模式）"
_INT_RE = re.compile(r"^[+-]?\d+$")
_FLOAT_RE = re.compile(r"^[+-]?\d+\.\d+$")
_CITATION_RE = re.compile(r"(画像|产品属性)｜([^｜；\n]+)｜([^｜；\n]+)")
CITATION_RE = _CITATION_RE
"""引用格式（contracts/field_catalog.json common.citation_format）的公开正则。"""


# ---------------------------------------------------------------------------
# field_catalog 装载与模板展开
# ---------------------------------------------------------------------------

@lru_cache(maxsize=1)
def load_field_catalog() -> dict:
    """读取 contracts/field_catalog.json（缓存）。文件缺失 raise RuntimeError。"""
    try:
        text = FIELD_CATALOG_PATH.read_text(encoding="utf-8")
    except OSError as exc:  # pragma: no cover - 环境损伤
        raise RuntimeError(f"field_catalog 缺失：{FIELD_CATALOG_PATH}（{exc}）") from exc
    return json.loads(text)


def catalog_fields(section: str) -> list[tuple[str, dict]]:
    """[(字段组名, 字段定义)]，顺序 = field_catalog[section].groups（公开，供 validators 复用）。"""
    return [
        (group["name"], field_def)
        for group in load_field_catalog()[section]["groups"]
        for field_def in group["fields"]
    ]


def _profile_template() -> list[tuple[str, dict]]:
    """[(字段组名, 字段定义)]，顺序 = field_catalog.profile.groups。"""
    return catalog_fields("profile")


def _product_template() -> list[tuple[str, dict]]:
    """[(字段组名, 字段定义)]，顺序 = field_catalog.product.groups。"""
    return catalog_fields("product")


def _missing_text(field_def: Optional[dict]) -> str:
    """字段级缺失表达（field_catalog.missing；缺省「未提供」）。"""
    if field_def and field_def.get("missing"):
        return str(field_def["missing"])
    return MISSING_NOT_PROVIDED


# ---------------------------------------------------------------------------
# 取值渲染
# ---------------------------------------------------------------------------

def _sanitize_cell(text: str) -> str:
    """单元格清洗：换行→「；」、ASCII 竖线→「／」、去首尾空白。"""
    return text.replace("\r\n", LIST_SEP).replace("\r", LIST_SEP).replace("\n", LIST_SEP).replace("|", "／").strip()


def _is_fact_like(value: Any) -> bool:
    if isinstance(value, FactCell):
        return True
    return isinstance(value, dict) and ("raw_value" in value or "normalized_value" in value)


def _scalar_text(value: Any, unit: Optional[str]) -> str:
    """标量 → 取值文本；数字结尾且带单位时补单位（保留原文限定词与既有单位表述）。"""
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, (int, float)):
        return f"{value}{unit}" if unit else str(value)
    text = _sanitize_cell(str(value))
    if (
        unit and text and text[-1] in "0123456789."
        and not text.endswith(str(unit))
    ):
        return f"{text}{unit}"
    return text


# verbatim_rule 明示保留的限定词/量级前后缀词族（约/最长/已售…+ 等）
_QUALIFIER_PREFIX_RE = re.compile(r"(?:约|大概|大约|近|高达|超过|至少|最多|最低|最长|大于|小于|已售|售出|累计)+")
_VERBATIM_TAIL_RE = re.compile(r"[\d.,，]+\s*(?:[-—~至到]\s*[\d.,，]+\s*)?(?:[＋+]|以上|及以上|左右)?")


def _raw_hides_verbatim(raw_value: Any, unit: Optional[str]) -> bool:
    """raw 为「限定词+数字[+单位][+量级后缀]」的原文形（verbatim_rule：须按原文保留）。

    contracts/field_catalog.json common.verbatim_rule：「数字保留原文精度与
    『约/最长』等限定词」。normalized_value 是数值化结果，渲染它会丢失
    「约27g」的「约」、「已售2000+」的「已售…+」（≥ 语义）等原文信息，此时改用
    raw_value；若 raw 含目录外附加语（如「32GB本地存储」——数字字段按 catalog
    types 须落纯数值）则不适用，仍渲染 normalized，避免破坏下游数值判定。
    """
    if not isinstance(raw_value, str):
        return False
    text = raw_value.strip()
    if not text:
        return False
    unit_text = str(unit or "").strip()
    if unit_text and text.endswith(unit_text):
        text = text[: -len(unit_text)].strip()
    text = _QUALIFIER_PREFIX_RE.sub("", text).strip()
    return _VERBATIM_TAIL_RE.fullmatch(text) is not None


def _fact_cell_text(
    raw_value: Any,
    normalized_value: Any,
    unit: Optional[str],
    status: Any,
    conditions: Any,
) -> str:
    """FactCell → 取值文本；缺失按状态落「未提供/待核验」；条件并列「；」分隔。"""
    status_text = str(status) if status is not None else ""
    empty = normalized_value is None and (raw_value is None or str(raw_value).strip() == "")
    if empty:
        if status_text in (FactStatus.CONDITIONAL.value, FactStatus.CONFLICTED.value):
            return MISSING_VERIFY
        return MISSING_NOT_PROVIDED
    if _raw_hides_verbatim(raw_value, unit):
        value = raw_value
    else:
        value = normalized_value if normalized_value is not None else raw_value
    if isinstance(value, (list, tuple)):
        text = LIST_SEP.join(_scalar_text(item, None) for item in value)
    else:
        text = _scalar_text(value, unit)
    cleaned = [str(c).strip() for c in (conditions or []) if str(c).strip()]
    if cleaned:
        text = f"{text}（{LIST_SEP.join(cleaned)}）"
    return text


def render_fact_value(cell: object) -> str:
    """FactCell（或其 dict 形态）→ 表格取值文本。

    条件并列用「；」分隔；缺失按 field_catalog 缺失表达；
    list 逐项以「；」连接；None → 「未提供」。
    非 FactCell 的 dict（无 raw_value/normalized_value 键）raise ValueError（不猜）。
    """
    if cell is None:
        return MISSING_NOT_PROVIDED
    if isinstance(cell, FactCell):
        return _fact_cell_text(cell.raw_value, cell.normalized_value, cell.unit, cell.status, cell.conditions)
    if isinstance(cell, dict) and _is_fact_like(cell):
        return _fact_cell_text(
            cell.get("raw_value"), cell.get("normalized_value"), cell.get("unit"),
            cell.get("status"), cell.get("conditions"),
        )
    if isinstance(cell, str):
        text = _sanitize_cell(cell)
        return text if text else MISSING_NOT_PROVIDED
    if isinstance(cell, (list, tuple)):
        parts = []
        for item in cell:
            if _is_fact_like(item) or not isinstance(item, dict):
                parts.append(render_fact_value(item))
            else:
                raise ValueError(f"render_fact_value：列表含未知 dict 项（{item!r}），须经模板逐项展开")
        return LIST_SEP.join(parts) if parts else MISSING_NOT_PROVIDED
    if isinstance(cell, dict):
        raise ValueError(f"render_fact_value：未知 dict 形态（keys={sorted(cell)}），无法确定取值")
    return _scalar_text(cell, None)


def _render_value(value: Any, field_def: Optional[dict]) -> str:
    """按字段目录渲染一个取值（标量/列表/FactCell/None）。"""
    if _is_fact_like(value):
        return render_fact_value(value)
    if value is None:
        return _missing_text(field_def)
    if isinstance(value, str):
        text = _sanitize_cell(value)
        return text if text else _missing_text(field_def)
    if isinstance(value, (int, float)):
        unit = field_def.get("unit") if field_def else None
        return f"{value}{unit}" if unit else str(value)
    if isinstance(value, (list, tuple)):
        if any(isinstance(item, (dict, list)) for item in value):
            raise ValueError(
                f"字段 {field_def.get('key') if field_def else '?'} 的列表含结构化项，须由模板逐项展开"
            )
        rendered = [_sanitize_cell(str(item)) for item in value if str(item).strip()]
        return LIST_SEP.join(rendered) if rendered else _missing_text(field_def)
    raise ValueError(f"字段 {field_def.get('key') if field_def else '?'} 取值类型不可渲染：{type(value)!r}")


def _sorted_scenes(scenes: list[dict]) -> list[dict]:
    """场景按（有优先级在前、优先级数值升序）稳定排序；未明确的保持原序在后。"""
    def key(pair: tuple[int, dict]) -> tuple[int, int]:
        index, scene = pair
        priority = scene.get("scene_priority") if isinstance(scene, dict) else None
        if isinstance(priority, bool) or not isinstance(priority, int):
            return (1, index)
        return (0, priority)

    return [scene for _, scene in sorted(enumerate(scenes), key=key)]


# ---------------------------------------------------------------------------
# Markdown 表格底层
# ---------------------------------------------------------------------------

def _render_table(headers: tuple[str, ...], rows: list[list[str]]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "| " + " | ".join(["---"] * len(headers)) + " |"]
    lines.extend("| " + " | ".join(row) + " |" for row in rows)
    return "\n".join(lines)


def _scan_blocks(md: str) -> list[tuple[str, Any]]:
    """把 Markdown 扫描为 [("heading", 文本) | ("table", 原始行) | ("line", 文本)] 块。

    连续的以「|」开头/结尾的行构成表格块；其余非空行为 line/heading。
    """
    blocks: list[tuple[str, Any]] = []
    table_lines: list[str] = []
    for line in md.splitlines():
        stripped = line.strip()
        if stripped.startswith("|") and stripped.endswith("|"):
            table_lines.append(stripped)
            continue
        if table_lines:
            blocks.append(("table", table_lines))
            table_lines = []
        if not stripped:
            continue
        if stripped.startswith("#"):
            blocks.append(("heading", stripped))
        else:
            blocks.append(("line", stripped))
    if table_lines:
        blocks.append(("table", table_lines))
    return blocks


def _parse_markdown_table(raw_lines: list[str], context: str) -> tuple[list[str], list[list[str]]]:
    """表格原始行 → (表头, 正文行)。列数不一致/缺分隔行/格式非法 raise ValueError。"""
    parsed: list[list[str]] = []
    for line in raw_lines:
        cells = [c.strip() for c in line[1:-1].split("|")]
        parsed.append(cells)
    ncols = len(parsed[0])
    if ncols < 2:
        raise ValueError(f"{context}：表格列数不足（{ncols}）")
    for idx, row in enumerate(parsed):
        if len(row) != ncols:
            raise ValueError(
                f"{context}：第 {idx + 1} 行列数 {len(row)} 与表头 {ncols} 不一致（坏行：{raw_lines[idx][:60]}）"
            )
    header, sep, body = parsed[0], parsed[1], parsed[2:]
    if not sep or not all(re.fullmatch(r":?-{3,}:?", cell) for cell in sep):
        raise ValueError(f"{context}：表头下缺合法分隔行（| --- |）")
    return header, body


# ---------------------------------------------------------------------------
# 渲染：画像 / 产品 / 推荐
# ---------------------------------------------------------------------------

def render_profile(profile: dict) -> str:
    """画像对象 → user_profile.md 全文（三列表格，覆盖 7 字段组，字段序=field_catalog）。"""
    rows: list[list[str]] = []
    for group_name, field_def in _profile_template():
        key = field_def["key"]
        if key == "scenes":
            scenes = profile.get("scenes") or []
            if not scenes:
                rows.append([group_name, field_def["name"], _missing_text(field_def)])
                continue
            item_fields = field_def["item_fields"]
            for n, scene in enumerate(_sorted_scenes(list(scenes)), start=1):
                for item_def in item_fields:
                    item_name = item_def["name"].format(n=n)
                    rows.append([group_name, item_name, _render_value(scene.get(item_def["key"]), item_def)])
        else:
            rows.append([group_name, field_def["name"], _render_value(profile.get(key), field_def)])
    body = _render_table(TABLE_COLUMNS, rows)
    return f"{PROFILE_TITLE}\n\n{body}\n"


def render_products(products: dict[str, dict]) -> str:
    """{canonical_id: 产品对象} → product_list.md 全文。

    每款一节「## P001 产品名称」（canonical_id 升序）；节内一张五行组属性表，
    字段集合/顺序/表头对所有产品完全一致（结构化列表按项展开）。
    """
    if not products:
        raise ValueError("render_products：产品对象为空，官方要求覆盖资料包内全部产品")
    parts: list[str] = [PRODUCTS_TITLE]
    name_def = next(f for _g, f in _product_template() if f["key"] == "product_name")
    for cid in sorted(products):
        obj = products[cid] or {}
        heading_name = _sanitize_cell(str(obj.get("product_name") or "")) or _missing_text(name_def)
        parts.append(f"## {cid} {heading_name}")
        rows: list[list[str]] = []
        for group_name, field_def in _product_template():
            key = field_def["key"]
            if key == "canonical_id":
                rows.append([group_name, field_def["name"], _sanitize_cell(cid)])
                continue
            if field_def.get("item_fields"):  # 结构化列表（battery_by_mode）
                items = obj.get(key) or []
                if not items:
                    rows.append([group_name, field_def["name"], _missing_text(field_def)])
                    continue
                for n, item in enumerate(items, start=1):
                    for item_def in field_def["item_fields"]:
                        item_name = item_def["name"].format(n=n)
                        rows.append([group_name, item_name, _render_value(item.get(item_def["key"]), item_def)])
            else:
                rows.append([group_name, field_def["name"], _render_value(obj.get(key), field_def)])
        parts.append(_render_table(TABLE_COLUMNS, rows))
    return "\n\n".join(parts) + "\n"


def _card_field(card: dict, key: str) -> Any:
    return card.get(key)


def _empty_slot_row(level: str, card: Optional[dict]) -> list[str]:
    """异常路径空位行（SPEC §9：空位明确说明，不虚构第三款）。"""
    reason = NO_VALID_BACKUP
    if card:
        parts = [str(x) for x in (_card_field(card, "profile_match") or []) if str(x).strip()]
        detail = _sanitize_cell(str(_card_field(card, "tradeoffs") or ""))
        text = LIST_SEP.join(parts) or detail
        if text:
            reason = NO_VALID_BACKUP if NO_VALID_BACKUP in text else f"{NO_VALID_BACKUP}：{text}"
    return [level, EMPTY_SLOT_ID, EMPTY_SLOT_NAME, reason, _sanitize_cell("无")]


def render_recommendation(plan: object) -> str:
    """DecisionPlan（或其 to_dict 形态）→ recommendation.md 全文。

    推荐结果表恰好 3 行（首选/备选1/备选2，行序即优先次序）+ 表后一句综合结论
    + 未选原因表（产品ID/未进入前三的主要原因/建议改变条件）。
    """
    data = plan.to_dict() if hasattr(plan, "to_dict") else dict(plan)
    cards_raw = data.get("recommendation_cards") or []
    cards: list[dict] = [c.to_dict() if hasattr(c, "to_dict") else dict(c) for c in cards_raw]
    if len(cards) > 3:
        raise ValueError(f"render_recommendation：推荐卡超过 3 张（{len(cards)}）")

    rec_rows: list[list[str]] = []
    for index in range(3):
        level = LEVELS[index]
        if index >= len(cards):
            rec_rows.append(_empty_slot_row(level, None))
            continue
        card = cards[index]
        card_level = _sanitize_cell(str(card.get("level") or "")) or level
        pid = _sanitize_cell(str(card.get("product_id") or ""))
        if not pid:
            rec_rows.append(_empty_slot_row(card_level, card))
            continue
        name = _sanitize_cell(str(card.get("product_name") or "")) or MISSING_NOT_PROVIDED
        match_parts = [str(x) for x in (card.get("profile_match") or []) if str(x).strip()]
        fact_parts = [str(x) for x in (card.get("fact_references") or []) if str(x).strip()]
        reason = LIST_SEP.join([*match_parts, *fact_parts]) or MISSING_NOT_PROVIDED
        note_parts = [str(card.get("costs_risks")).strip()] if str(card.get("costs_risks") or "").strip() else []
        note_parts += [str(x).strip() for x in (card.get("pre_purchase_checks") or []) if str(x).strip()]
        notes = LIST_SEP.join(note_parts) or MISSING_NOT_PROVIDED
        rec_rows.append([card_level, pid, name, _sanitize_cell(reason), _sanitize_cell(notes)])

    conclusion = _sanitize_cell(str(data.get("overall_conclusion") or "")) or MISSING_NOT_PROVIDED

    exclusion_rows: list[list[str]] = []
    for item_raw in data.get("excluded_products") or []:
        item = item_raw.to_dict() if hasattr(item_raw, "to_dict") else dict(item_raw)
        ids = [_sanitize_cell(str(x)) for x in (item.get("product_ids") or []) if str(x).strip()]
        if not ids:
            raise ValueError("render_recommendation：未选原因行缺产品ID")
        detail = _sanitize_cell(str(item.get("reason_detail") or "")) or MISSING_NOT_PROVIDED
        conditions = [str(x).strip() for x in (item.get("change_conditions") or []) if str(x).strip()]
        exclusion_rows.append([MERGE_SEP.join(ids), detail, LIST_SEP.join(conditions) or MISSING_NOT_PROVIDED])

    blocks: list[str] = [
        RECOMMENDATION_TITLE,
        f"## {SECTION_RESULT}",
        _render_table(REC_COLUMNS, rec_rows),
        f"{CONCLUSION_PREFIX}{conclusion}",
    ]
    if exclusion_rows:
        blocks += [f"## {SECTION_EXCLUSION}", _render_table(EXCLUSION_COLUMNS, exclusion_rows)]
    return "\n\n".join(blocks) + "\n"


# ---------------------------------------------------------------------------
# 取值解析
# ---------------------------------------------------------------------------

_MISSING_EXACT = {MISSING_NOT_PROVIDED, MISSING_VERIFY, PRIORITY_UNCLEAR}
_MISSING_PREFIXES = (MISSING_NOT_PROVIDED, MISSING_VERIFY, PRIORITY_UNCLEAR)


def _is_missing_text(text: str) -> bool:
    t = text.strip()
    return t in _MISSING_EXACT or t.startswith(_MISSING_PREFIXES)


def _parse_numeric(text: str, unit: Optional[str]) -> Any:
    t = text.strip()
    if unit and t.endswith(unit) and len(t) > len(unit):
        t = t[: -len(unit)].strip()
    if _INT_RE.match(t):
        return int(t)
    if _FLOAT_RE.match(t):
        return float(t)
    return t  # 保留原文限定词（如「约29」）


_ANNOTATED_MISSING_RE = re.compile(r"^(未提供|待核验|优先级未明确)[（(]")


def _parse_cell(text: str, field_def: Optional[dict]) -> Any:
    """单元格 → 取值。缺失表达规则：
    - 裸缺失表达（含该字段在 field_catalog 登记的缺失表达）→ None；
    - 带说明的缺失表达（如「待核验（口径不一）」）→ 原样保留字符串
      （rules.json 证据三档 C：待核验须附条件说明，说明是取值的一部分，
      供决策层与再渲染使用；判定方应以 startswith 判缺失）；
    数字 → int/float；列表 → split「；」；其余原文。
    """
    t = text.strip()
    if t in _MISSING_EXACT or (field_def and t == _missing_text(field_def)):
        return None
    if _ANNOTATED_MISSING_RE.match(t):
        return t
    if _is_missing_text(t):
        return None
    ftype = (field_def or {}).get("type")
    if ftype == "列表":
        items = [seg.strip() for seg in t.split(LIST_SEP) if seg.strip()]
        return items or None
    if ftype == "数字":
        return _parse_numeric(t, (field_def or {}).get("unit"))
    return t


def _expect(cond: bool, message: str) -> None:
    if not cond:
        raise ValueError(message)


def _match_table(header: list[str], body: list[list[str]], expected: tuple[str, ...], context: str) -> list[list[str]]:
    _expect(tuple(header) == expected, f"{context}：表头 {header} ≠ 约定 {list(expected)}")
    return body


# ---------------------------------------------------------------------------
# 解析：画像 / 产品 / 推荐
# ---------------------------------------------------------------------------

def parse_profile(md: str) -> dict:
    """user_profile.md 全文 → 画像对象（决策阶段真实回读用）。

    「未提供/待核验/优先级未明确」文本解析回 None；未知行/重复行/结构非法 raise ValueError。
    """
    blocks = _scan_blocks(md)
    tables = [b for b in blocks if b[0] == "table"]
    _expect(len(tables) == 1, f"user_profile.md：应恰有 1 张画像表，实见 {len(tables)} 张")
    for kind, payload in blocks:
        if kind == "line":
            raise ValueError(f"user_profile.md：表格外的非标题行「{str(payload)[:40]}」")
        if kind == "heading":
            _expect(_HEADING_PROFILE_RE.match(payload), f"user_profile.md：异常标题「{payload[:40]}」")
    header = [c.strip() for c in tables[0][1][0][1:-1].split("|")]
    _header, body = _parse_markdown_table(tables[0][1], "user_profile.md")
    body = _match_table(header, body, TABLE_COLUMNS, "user_profile.md")

    template = _profile_template()
    name_to_field = {f["name"]: (g, f) for g, f in template}
    item_lookup: dict[str, dict] = {}
    for _g, f in template:
        if f["key"] == "scenes":
            for item in f["item_fields"]:
                # 「场景{n}·名称」→ 后缀「·名称」为场景行键
                item_lookup[item["name"].split("{n}", 1)[1]] = item

    result: dict[str, Any] = {f["key"]: None for _g, f in template}
    scenes_by_num: dict[int, dict[str, Any]] = {}
    seen_rows: set[tuple[str, str]] = set()
    for row in body:
        _expect(len(row) == 3, f"user_profile.md：画像行须 3 列，实见 {len(row)}（{row}）")
        group_name, field_name, cell = row
        row_key = (group_name, field_name)
        _expect(row_key not in seen_rows, f"user_profile.md：重复行 {row_key}")
        seen_rows.add(row_key)
        scene_match = _SCENE_ROW_RE.match(field_name)
        if scene_match:
            num = int(scene_match.group(1))
            suffix = field_name.split("·", 1)[1]
            item_def = item_lookup.get("·" + suffix)
            _expect(item_def is not None, f"user_profile.md：未知场景行「{field_name}」")
            scenes_by_num.setdefault(num, {})[item_def["key"]] = _parse_cell(cell, item_def)
            continue
        _expect(field_name in name_to_field, f"user_profile.md：未知字段行「{field_name}」")
        _group, field_def = name_to_field[field_name]
        _expect(_group == group_name, f"user_profile.md：字段「{field_name}」字段组应为「{_group}」实为「{group_name}」")
        result[field_def["key"]] = _parse_cell(cell, field_def)
    result["scenes"] = [scenes_by_num[n] for n in sorted(scenes_by_num)] if scenes_by_num else None
    return result


def parse_products(md: str) -> dict[str, dict]:
    """product_list.md 全文 → {canonical_id: 产品对象}。

    节标题须为规范三位编号；未知字段/重复节/编号不一致 raise ValueError。
    """
    blocks = _scan_blocks(md)
    template = _product_template()
    name_to_field = {f["name"]: (g, f) for g, f in template}
    battery_suffix_lookup: dict[str, dict] = {}
    for _g, f in template:
        if f.get("item_fields"):
            for item in f["item_fields"]:
                battery_suffix_lookup[item["name"].split("{n}", 1)[1]] = item

    result: dict[str, dict] = {}
    current_id: Optional[str] = None
    current_name: Optional[str] = None
    seen_title = False
    for kind, payload in blocks:
        if kind == "line":
            raise ValueError(f"product_list.md：表格外的非标题行「{str(payload)[:40]}」")
        if kind == "heading":
            product_match = _HEADING_PRODUCT_RE.match(payload)
            if product_match:
                seen_title = True
                current_id = product_match.group(1)
                current_name = product_match.group(2).strip()
                _expect(current_id not in result, f"product_list.md：产品节重复「{current_id}」")
                result[current_id] = {f["key"]: None for _g, f in template}
                result[current_id]["canonical_id"] = current_id
            else:
                _expect(payload.startswith("# ") and not payload.startswith("## "),
                        f"product_list.md：异常节标题「{payload[:40]}」")
                _expect(not seen_title, f"product_list.md：产品节之后出现文档标题「{payload[:40]}」")
            continue
        # table
        _expect(current_id is not None, "product_list.md：表出现在任何产品节之前")
        context = f"product_list.md#{current_id}"
        header = [c.strip() for c in payload[0][1:-1].split("|")]
        _header, body = _parse_markdown_table(payload, context)
        body = _match_table(header, body, TABLE_COLUMNS, context)

        battery_by_num: dict[int, dict[str, Any]] = {}
        seen_rows: set[str] = set()
        heading_name = current_name
        for row in body:
            _expect(len(row) == 3, f"{context}：产品行须 3 列，实见 {len(row)}")
            group_name, field_name, cell = row
            _expect(field_name not in seen_rows, f"{context}：重复字段行「{field_name}」")
            seen_rows.add(field_name)
            battery_match = _BATTERY_ROW_RE.match(field_name)
            if battery_match:
                num = int(battery_match.group(1))
                suffix = field_name.split("·", 1)[1]
                item_def = battery_suffix_lookup.get("·" + suffix)
                _expect(item_def is not None, f"{context}：未知续航行「{field_name}」")
                battery_by_num.setdefault(num, {})[item_def["key"]] = _parse_cell(cell, item_def)
                continue
            _expect(field_name in name_to_field, f"{context}：未知字段行「{field_name}」")
            _group, field_def = name_to_field[field_name]
            _expect(_group == group_name, f"{context}：字段「{field_name}」字段组应为「{_group}」实为「{group_name}」")
            if field_def["key"] == "canonical_id":
                _expect(cell == current_id, f"{context}：产品编号行 {cell} 与节标题 {current_id} 不一致")
                continue
            if field_def["key"] == "product_name":
                row_name = cell.strip()
                if _is_missing_text(row_name):
                    _expect(heading_name in _MISSING_EXACT or heading_name == row_name,
                            f"{context}：节标题名称「{heading_name}」与产品名称行「{row_name}」不一致")
                    result[current_id]["product_name"] = None
                else:
                    _expect(heading_name == row_name,
                            f"{context}：节标题名称「{heading_name}」与产品名称行「{row_name}」不一致")
                    result[current_id]["product_name"] = row_name
                continue
            result[current_id][field_def["key"]] = _parse_cell(cell, field_def)
        result[current_id]["battery_by_mode"] = (
            [battery_by_num[n] for n in sorted(battery_by_num)] if battery_by_num else None
        )
    _expect(bool(result), "product_list.md：未发现任何产品节")
    return result


def product_table_structures(md: str) -> list[tuple[str, list[tuple[str, str]]]]:
    """product_list.md 各产品节的表结构：[(canonical_id, [(字段组, 字段名), ...])]。

    供 validators 做表结构一致性检查（E4）；结构非法 raise ValueError（同 parse_products）。
    """
    blocks = _scan_blocks(md)
    structures: list[tuple[str, list[tuple[str, str]]]] = []
    current_id: Optional[str] = None
    current_rows: list[tuple[str, str]] = []
    for kind, payload in blocks:
        if kind == "table":
            _expect(current_id is not None, "product_list.md：表出现在任何产品节之前")
            header = [c.strip() for c in payload[0][1:-1].split("|")]
            _header, body = _parse_markdown_table(payload, f"product_list.md#{current_id}")
            _match_table(header, body, TABLE_COLUMNS, f"product_list.md#{current_id}")
            for row in body:
                _expect(len(row) == 3, f"product_list.md#{current_id}：产品行须 3 列")
                current_rows.append((row[0], row[1]))
            continue
        if kind == "heading":
            product_match = _HEADING_PRODUCT_RE.match(payload)
            if product_match:
                if current_id is not None:
                    structures.append((current_id, current_rows))
                current_id = product_match.group(1)
                current_rows = []
    if current_id is not None:
        structures.append((current_id, current_rows))
    return structures


def parse_recommendation(md: str) -> dict:
    """recommendation.md 全文 → {"recommendations": [...], "overall_conclusion": str|None,
    "exclusions": [...]}（validators 回读用；表缺失/结构非法 raise ValueError）。"""
    blocks = _scan_blocks(md)
    parsed: dict[str, Any] = {"recommendations": [], "overall_conclusion": None, "exclusions": []}
    section: Optional[str] = None
    for kind, payload in blocks:
        if kind == "line":
            text = str(payload)
            _expect(text.startswith(CONCLUSION_PREFIX),
                    f"recommendation.md：表格外 unexpected 行「{text[:40]}」")
            _expect(parsed["overall_conclusion"] is None, "recommendation.md：综合结论行重复")
            parsed["overall_conclusion"] = text[len(CONCLUSION_PREFIX):].strip()
            continue
        if kind == "heading":
            title = payload.lstrip("#").strip()
            _expect(payload.startswith("##") or payload == RECOMMENDATION_TITLE,
                    f"recommendation.md：异常标题「{payload[:40]}」")
            if title == RECOMMENDATION_TITLE.lstrip("# ").strip():
                continue
            _expect(title in (SECTION_RESULT, SECTION_EXCLUSION),
                    f"recommendation.md：未知小节「{title}」")
            _expect(title != section, f"recommendation.md：小节「{title}」重复")
            section = title
            continue
        # table
        context = f"recommendation.md#{section}"
        header = [c.strip() for c in payload[0][1:-1].split("|")]
        _header, body = _parse_markdown_table(payload, context)
        if section == SECTION_RESULT:
            body = _match_table(header, body, REC_COLUMNS, context)
            for row in body:
                _expect(len(row) == 5, f"{context}：推荐行须 5 列，实见 {len(row)}")
                level, pid, name, reason, notes = row
                parsed["recommendations"].append({
                    "level": level,
                    "product_id": None if pid.strip() == EMPTY_SLOT_ID else pid.strip(),
                    "product": None if _is_missing_text(name) else name,
                    "reason": reason,
                    "notes": notes,
                })
        elif section == SECTION_EXCLUSION:
            body = _match_table(header, body, EXCLUSION_COLUMNS, context)
            for row in body:
                _expect(len(row) == 3, f"{context}：未选行须 3 列，实见 {len(row)}")
                ids_cell, reason_cell, conditions_cell = row
                ids = [seg.strip() for seg in ids_cell.split(MERGE_SEP) if seg.strip()]
                parsed["exclusions"].append({
                    "product_ids": ids,
                    "reason": reason_cell,
                    "change_conditions": [seg.strip() for seg in conditions_cell.split(LIST_SEP) if seg.strip()],
                })
        else:
            raise ValueError("recommendation.md：表出现在任何小节之前")
    _expect(section is not None or bool(parsed["recommendations"]), "recommendation.md：未发现任何小节表")
    _expect(parsed["overall_conclusion"] is not None, "recommendation.md：缺少综合结论行")
    return parsed
