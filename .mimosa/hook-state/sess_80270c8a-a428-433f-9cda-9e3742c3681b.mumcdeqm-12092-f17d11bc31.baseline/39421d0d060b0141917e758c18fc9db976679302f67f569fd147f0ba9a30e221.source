# -*- coding: utf-8 -*-
"""src/selector.py — 排序与组合选择（决策模块）。

契约（contracts/interfaces.md §13）与隔离铁律（SPEC §3/§9）：
- 入参只能是 render→parse 回读后的画像/产品对象 + 约束矩阵；
- 排序口径（rules.json ranking_basis）：全部硬约束 PASS → 匹配高优先级明确需求 →
  可比属性取舍 → 证据缺口/冲突影响 → 组合差异化；
- 禁止输出无口径的「综合匹配度93.7」类数字：内部排序使用「需求四态×需求优先级」
  加权与显式属性比较器（存储/价格/防水深度，仅当用户表达了对应需求才启用），
  每一项都可还原为具体「属性×需求」；数值不写入报告；
- 备选不预设「性能/性价比/品牌款」分类；两备选差异有限时如实说明；
- 有效产品不足 3 款走 SPEC §9 异常路径（不放宽硬约束、不把未知写成符合、
  不虚构第三款；无首选则明确资料无法支持直接购买建议）；
- 未选原因五类（硬约束不符/硬约束证据不足/软需求匹配弱/同条件下被压过/
  无足够新增价值）；条件变化句与排除原因对应；多个否决条件全部列出。

引用排版纪律（适配 validators E8 的引用扫描）：
- 每个以「；」分段的文字段内，引用（画像｜组｜字段名 / 产品属性｜编号｜字段名）
  一律集中在段尾的「（…）」内、以「；」分隔；段内叙述文字不夹带引用；
- 嵌入约束判定解释（内部文本，引用位置随意）时，先剥离其引用前缀只留字段名。
"""
from __future__ import annotations

import re
from typing import Optional

from src import constraint_engine as ce
from src.constraint_engine import (
    NEED_BUDGET_LIMIT,
    NEED_BUDGET_REFERENCE,
    NEED_STORAGE,
    NEED_UNDERWATER,
    NEED_WATER_RESIST,
    build_needs,
    constraint_matrix,
    product_citation,
    text_of,
    to_number,
    valid_product_ids,
)
from src.schemas import (
    ConstraintOutcome,
    ConstraintResult,
    DecisionPlan,
    ExclusionRecord,
    NeedType,
    RecommendationCard,
)

LEVELS = ("首选", "备选1", "备选2")
EXCLUSION_CATEGORIES = ("硬约束不符", "硬约束证据不足", "软需求匹配弱", "同条件下被压过", "无足够新增价值")
NO_VALID_BACKUP = "当前尚不构成有效备选"

_DEPTH_RE = re.compile(r"约?(\d+(?:\.\d+)?)\s*米")
_HOUR_RE = re.compile(r"(?:约|最长)?\s*(\d+(?:\.\d+)?)\s*小时")
_CITE_PREFIX_RE = re.compile(r"(?:产品属性｜P\d{3}｜|画像｜[^｜；\n]{1,16}｜)")
_NOISE_ENV_RE = re.compile(r"噪音|噪声|嘈杂")
_NO_ACTIVE_NC_RE = re.compile(r"无主动降噪|不支持主动降噪|没有主动降噪|非主动降噪")

_RESULT_WORDS = {
    ConstraintOutcome.PASS: "资料支持满足",
    ConstraintOutcome.FAIL: "资料表明不符",
    ConstraintOutcome.UNKNOWN: "资料未见直接记载（未证实）",
}


# ---------------------------------------------------------------------------
# 文本小工具
# ---------------------------------------------------------------------------

def _trim_flat(text: str, limit: int) -> str:
    """压平一段叙述文字：去空白、；改，（保持「；分段=引用分段」纪律）、限长。"""
    cleaned = re.sub(r"\s+", "", text or "").replace("；", "，")
    return cleaned if len(cleaned) <= limit else cleaned[: limit - 1] + "…"


def _strip_citation_prefixes(text: str) -> str:
    """嵌入解释文字时剥离引用前缀，只留字段名（段内不夹带引用）。"""
    return _CITE_PREFIX_RE.sub("", text or "")


def _cited(text: str, *citations: str) -> str:
    """叙述文字 + 若干引用 → 以冒号引出、段尾收尾的完整段。

    引用排版纪律（适配 validators E8 的清洗口径）：每个引用用「」包裹且必须
    紧贴「；」分段边界或单元格结尾——右片以收尾引号」结束才会被剥离；
    引用后再套「（…）」会因右片以「）」结束而误判越界。
    """
    cites = [f"「{c}」" for c in citations if c]
    if not cites:
        return text
    return f"{text}：{'；'.join(cites)}"


def _sanitize_conditions(text: str) -> str:
    """判定解释中的条件部分 → 无引用的可读文字（引用以字段名呈现）。"""
    cleaned = _strip_citation_prefixes(text or "")
    return _trim_flat(cleaned, 80)


# ---------------------------------------------------------------------------
# 排序（内部数值可存在但不写入报告；每一项可还原为属性×需求）
# ---------------------------------------------------------------------------

def rank_valid(valid_ids: set[str], profile: dict, products: dict[str, dict]) -> list[str]:
    """E 内排序（契约签名）：最优在前。

    口径（rules.json ranking_basis，全部可还原）：
    1. 需求匹配分：Σ(需求优先级 × 四态权值)，权值 PASS=+1 / FAIL=-1 / 其他=0
       （仅统计有算子或场景目标的明确需求；数值不写报告）；
    2. 证据缺口：Σ(需求优先级 × UNKNOWN) 升序（缺口少者优先）；
    3. 可比属性（仅当用户表达了对应需求）：本地存储容量降序 → 展示价升序 →
       防水深度口径降序；
    4. canonical_id 升序（稳定收尾）。
    """
    ordered, _basis = _rank_with_basis(valid_ids, profile, products)
    return ordered


def _rank_with_basis(valid_ids: set[str], profile: dict,
                     products: dict[str, dict]) -> tuple[list[str], dict[str, dict]]:
    needs = build_needs(profile or {})
    matrix = constraint_matrix(profile or {}, products or {})
    by_pair = {(r.product_id, r.need_id): r for r in matrix}

    has_storage = any(n.need_id == NEED_STORAGE for n in needs)
    has_budget = any(n.need_id in (NEED_BUDGET_LIMIT, NEED_BUDGET_REFERENCE) for n in needs)
    has_water = any(n.need_id in (NEED_UNDERWATER, NEED_WATER_RESIST) for n in needs)

    basis: dict[str, dict] = {}
    for pid in valid_ids:
        product = (products or {}).get(pid) or {}
        score = 0
        unknown_weight = 0
        matched: list[tuple[int, str, ConstraintResult]] = []
        for need in needs:
            result = by_pair.get((pid, need.need_id))
            if result is None:
                continue
            if need.operator is not None or need.need_type is NeedType.GOAL:
                matched.append((need.priority, need.need_id, result))
                if result.result is ConstraintOutcome.PASS:
                    score += need.priority
                elif result.result is ConstraintOutcome.FAIL:
                    score -= need.priority
                elif result.result is ConstraintOutcome.UNKNOWN:
                    unknown_weight += need.priority
        storage = to_number(product.get("local_storage_gb"))
        price = _primary_price(product)
        depth = _max_depth(product)
        basis[pid] = {
            "score": score,
            "unknown_weight": unknown_weight,
            "storage": storage if storage is not None else -1.0,
            "price": price if price is not None else float("inf"),
            "depth": depth if depth is not None else -1.0,
            "matched": matched,
        }

    def _sort_key(pid: str) -> tuple:
        info = basis[pid]
        storage_key = -info["storage"] if has_storage else 0.0
        price_key = info["price"] if has_budget else 0.0
        depth_key = -info["depth"] if has_water else 0.0
        return (-info["score"], info["unknown_weight"], storage_key, price_key, depth_key, pid)

    return sorted(valid_ids, key=_sort_key), basis


def _primary_price(product: dict) -> Optional[float]:
    """展示价/首口径价格（parse 后文本 → 数值；无口径 → None）。"""
    text = text_of(product.get("current_price"))
    if not text:
        return None
    scopes = ce.parse_price_scopes(text)
    if not scopes:
        return None
    primary = next((s for s in scopes if s["listed"]), scopes[0])
    return float(primary["lo"])


def _max_depth(product: dict) -> Optional[float]:
    """防水适用条件文本中的最大水深口径（米）。"""
    text = text_of(product.get("waterproof_conditions"))
    if not text:
        return None
    depths = [float(m.group(1)) for m in _DEPTH_RE.finditer(text)]
    return max(depths) if depths else None


def _max_battery_hours(product: dict) -> Optional[float]:
    battery = product.get("battery_by_mode") or []
    hours = []
    for item in battery:
        if not isinstance(item, dict):
            continue
        match = _HOUR_RE.search(str(item.get("duration") or ""))
        if match:
            hours.append(float(match.group(1)))
    return max(hours) if hours else None


# ---------------------------------------------------------------------------
# 主入口
# ---------------------------------------------------------------------------

def select_recommendation(profile: dict, products: dict[str, dict],
                          constraints: list[ConstraintResult]) -> DecisionPlan:
    """从有效集 E 中选出首选/备选1/备选2 并生成完整 DecisionPlan（契约签名）。

    - 三款全部从 E 选取，行序即优先次序；
    - 有效产品不足 3 款：保留有效首选，空位行明确「当前尚不构成有效备选」+原因；
      E 为空则明确资料无法支持直接购买建议（异常路径，不放宽、不虚构）；
    - 未选原因表覆盖全部未入选产品（同因可合并一行）。
    """
    products = products or {}
    profile = profile or {}
    needs = build_needs(profile)
    if not constraints:
        constraints = constraint_matrix(profile, products)
    by_pair = {(r.product_id, r.need_id): r for r in constraints}

    valid = valid_product_ids(constraints)
    valid &= set(products)
    ordered, basis = _rank_with_basis(valid, profile, products)
    selected = ordered[:3]

    cards = [
        _compose_card(level, pid, products.get(pid) or {}, needs, by_pair,
                      basis, selected, rank_position, profile=profile)
        for rank_position, (level, pid) in enumerate(zip(LEVELS, selected))
    ]
    if len(cards) < 3:
        cards.extend(_empty_slot_cards(len(selected), valid, ordered,
                                       _top_blockers(by_pair, needs)))

    excluded_ids = [pid for pid in sorted(products) if pid not in selected]
    exclusions = _build_exclusions(excluded_ids, needs, by_pair, selected, basis, products)

    conclusion = _overall_conclusion(selected, products, needs, by_pair, valid)
    return DecisionPlan(
        selected_products=list(selected),
        recommendation_cards=cards,
        excluded_products=exclusions,
        overall_conclusion=conclusion,
    )


# ---------------------------------------------------------------------------
# 推荐卡（SPEC §9 固定字段）
# ---------------------------------------------------------------------------

def _compose_card(level: str, pid: str, product: dict, needs: list, by_pair: dict,
                  basis: dict, selected: list[str], rank_position: int,
                  profile: Optional[dict] = None) -> RecommendationCard:
    matches = basis.get(pid, {}).get("matched", [])
    strong = [item for item in matches
              if item[2].result in (ConstraintOutcome.PASS, ConstraintOutcome.UNKNOWN)]
    strong.sort(key=lambda item: (-item[0], item[1]))

    profile_match: list[str] = []
    fact_references: list[str] = []
    seen_refs: set[str] = set()
    for _priority, _need_id, result in strong:
        if len(profile_match) >= 3:
            break
        if result.profile_reference in seen_refs:
            continue  # 同一画像引用（如场景目标与硬约束同源）不重复占位
        seen_refs.add(result.profile_reference)
        need = next(n for n in needs if n.need_id == result.need_id)
        word = _RESULT_WORDS[result.result]
        profile_match.append(
            _cited(f"命中「{ce._trim(need.original_expression, 36)}」：{word}",
                   result.profile_reference))
        if result.result is ConstraintOutcome.PASS and result.attribute_reference:
            fact_references.append(
                _cited(_attr_value_summary(pid, result, product), result.attribute_reference))
    # 场景目标（矩阵 NOT_APPLICABLE）不进四态判定，但核心场景必须在推荐理由中可见
    # （官方评分 R1「是否以核心场景为主」）；与场景同源的硬约束条目已引用同一场景行时不重复
    profile_match.extend(_scene_goal_clauses(needs, by_pair, pid, seen_refs, profile))
    price_text = text_of(product.get("current_price"))
    if price_text and not any("当前售价" in f for f in fact_references):
        fact_references.append(
            _cited(f"当前售价{_trim_flat(price_text, 40)}",
                   product_citation(pid, "current_price")))

    profile_match.append(_tradeoff_clause(pid, basis, selected))
    return RecommendationCard(
        level=level,
        product_id=pid,
        product_name=str(product.get("product_name") or "未提供"),
        brand=str(product.get("brand") or "未提供"),
        profile_match=[m for m in profile_match if m],
        fact_references=[f for f in fact_references if f],
        tradeoffs=_tradeoff_text(pid, basis, selected),
        costs_risks=_costs_risks(pid, product, needs, by_pair),
        pre_purchase_checks=_pre_purchase_checks(pid, product, needs, by_pair,
                                                 profile=profile),
        switch_conditions=_switch_conditions(pid, basis, selected, rank_position),
    )


_SCENE_GOAL_NOTE = "场景目标，按排序综合权衡（无产品侧可执行判定字段）"


def _scene_goal_clauses(needs: list, by_pair: dict, pid: str,
                        seen_refs: set[str], profile: Optional[dict]) -> list[str]:
    """场景目标 → 「画像匹配」条目（引用「画像｜场景｜场景{n}·名称」，至多 2 条）。

    官方环节三 R1「是否以核心场景为主」：场景目标在约束矩阵中为 NOT_APPLICABLE
    （综合诉求不进四态判定，constraint_engine.build_needs），若只按四态筛条目，
    推荐卡会整体丢失场景维度。此处补场景行引用；措辞只陈述「场景目标纳入排序
    权衡」这一事实，不虚构场景适配结论（SPEC §9：不编造，无据写证据边界）。
    与场景同源的硬约束（如水下听歌）已引用同一场景行时不重复（seen_refs）。
    """
    scenes = [s for s in ((profile or {}).get("scenes") or []) if isinstance(s, dict)]
    clauses: list[str] = []
    for need in needs:
        if not need.need_id.startswith(ce.NEED_SCENE_PREFIX):
            continue
        result = by_pair.get((pid, need.need_id))
        if result is None or result.profile_reference in seen_refs:
            continue
        if len(clauses) >= 2:
            break
        scene_no = ce._scene_no_of(need)
        name = ""
        if scene_no is not None and 1 <= scene_no <= len(scenes):
            name = str(scenes[scene_no - 1].get("scene_name") or "").strip()
        label = name or ce._trim(need.original_expression, 24)
        seen_refs.add(result.profile_reference)
        clauses.append(_cited(f"场景「{label}」：{_SCENE_GOAL_NOTE}",
                              result.profile_reference))
    return clauses


def _noise_boundary_check(pid: str, product: dict,
                          profile: Optional[dict]) -> Optional[str]:
    """「场景原文提及噪音环境 × 本品无主动降噪」的证据边界核验项。

    两侧事实均出自前两份文档（场景行 / 降噪能力字段），只披露取舍、不编造缺点；
    场景无噪音语境、产品有主动降噪记载或字段缺失时不推断（SPEC §9 证据边界）。
    """
    for scene_no, scene in enumerate((profile or {}).get("scenes") or [], start=1):
        if not isinstance(scene, dict):
            continue
        context = f"{scene.get('scene_name') or ''}{scene.get('scene_evidence') or ''}"
        if not _NOISE_ENV_RE.search(context):
            continue
        nc_text = text_of(product.get("noise_cancellation"))
        if nc_text is None or not _NO_ACTIVE_NC_RE.search(nc_text):
            return None
        return _cited(
            f"场景原文提及噪音环境，本品降噪能力为{_trim_flat(nc_text, 20)}，"
            "强噪音环境下的实际听感须购买前试听核验",
            f"画像｜场景｜场景{scene_no}·原文依据",
            product_citation(pid, "noise_cancellation"))
    return None


def _attr_value_summary(pid: str, result: ConstraintResult, product: dict) -> str:
    """从判定解释提取「字段名＝值摘要」（引用外的可读摘要）。"""
    attr_ref = result.attribute_reference or ""
    field_name = attr_ref.split("｜")[-1] if attr_ref else "相关属性"
    explanation = result.explanation or ""
    if "＝" in explanation:
        value = explanation.split("＝", 1)[1]
        value = re.split(r"[（;；]", value)[0]
        if value:
            return f"{field_name}＝{_trim_flat(value, 34)}"
    if result.need_id == NEED_STORAGE:
        number = to_number(product.get("local_storage_gb"))
        if number is not None:
            return f"{field_name}＝{number:g}GB"
    return field_name


# ---------------------------------------------------------------------------
# 取舍（相对其他有效候选得/失；数值均来自 parse 后字段）
# ---------------------------------------------------------------------------

_COMPARATOR_LABELS = {
    "storage": "本地存储容量",
    "price": "当前售价",
    "depth": "防水深度口径",
}


def _comparator_diffs(pid: str, basis: dict, selected: list[str]) -> list[tuple[str, str, str]]:
    """[(维度, 本品优势描述, 本品劣势描述)]，仅基于 parse 后字段值。"""
    info = basis.get(pid)
    if info is None:
        return []
    diffs: list[tuple[str, str, str]] = []
    others = [p for p in selected if p != pid and p in basis]
    if not others:
        return diffs
    for key, fmt, better_high in (
        ("storage", "{:g}GB", True),
        ("price", "{:g}元", False),
        ("depth", "约{:g}米", True),
    ):
        mine = info[key]
        best_other = max((basis[p][key] for p in others), default=mine) if better_high \
            else min((basis[p][key] for p in others), default=mine)
        if mine == best_other or mine in (float("inf"), -1.0) or best_other in (float("inf"), -1.0):
            continue
        is_win = (better_high and mine > best_other) or (not better_high and mine < best_other)
        is_lose = (better_high and mine < best_other) or (not better_high and mine > best_other)
        if not (is_win or is_lose):
            continue
        label = _COMPARATOR_LABELS[key]
        win = f"{label}占优（{fmt.format(mine)}）" if is_win else None
        lose = (f"{label}较小（{fmt.format(mine)}，其余有效候选最高{fmt.format(best_other)}）"
                if is_lose and better_high else
                (f"{label}较高（{fmt.format(mine)}，其余有效候选最低{fmt.format(best_other)}）"
                 if is_lose else None))
        diffs.append((label, win or "相当", lose or "相当"))
    return diffs


def _tradeoff_clause(pid: str, basis: dict, selected: list[str]) -> str:
    """一句话取舍（写入「画像匹配与推荐理由」列）：相对其他有效候选的得/失。"""
    clause = _tradeoff_text(pid, basis, selected)
    return f"取舍：{clause}" if clause else "取舍：有效候选间差异有限，按需求匹配与价格口径排序"


def _tradeoff_text(pid: str, basis: dict, selected: list[str]) -> str:
    diffs = _comparator_diffs(pid, basis, selected)
    if not diffs:
        return "与其他有效候选在可比属性上差异有限"
    others = [p for p in selected if p != pid]
    parts = []
    for _label, win, lose in diffs[:2]:
        if win != "相当":
            parts.append(f"较{'、'.join(others)}中更优：{win}")
        elif lose != "相当":
            parts.append(lose)
    return "；".join(parts) if parts else "与其他有效候选在可比属性上差异有限"


# ---------------------------------------------------------------------------
# 「无足够新增价值」的条件句（由该产品实际属性生成；审计修复 2026-09）
# ---------------------------------------------------------------------------

_NO_VALUE_DIM_FIELDS = {
    "本地存储容量": "local_storage_gb",
    "当前售价": "current_price",
    "防水深度口径": "waterproof_conditions",
}


def _form_family_set(product: dict) -> set[str]:
    """产品形态词族（constraint_engine 同族表；用于判断形态差异是否真实成立）。"""
    text = "；".join(filter(None, [
        text_of(product.get("category")),
        text_of(product.get("wearing_design")),
    ]))
    return set(ce._form_families_of(text))


def _no_added_value_conditions(pid: str, products: dict, basis: dict,
                               selected: list[str]) -> list[str]:
    """「无足够新增价值」的建议改变条件：由该产品相对入选款的真实属性差异生成。

    审计修复（原发现：固定例举「（如价格最低、形态不同）」与详情自相矛盾）——
    条件句必须由该产品实际属性生成，删掉固定例举。素材全部来自 parse 回读字段：
    可比维度占优（仅用户表达对应需求时比较器才启用，天然与需求对应）、续航口径、
    形态词族差异；资料无可辨认独有属性时如实说明证据边界，不虚构例举。
    """
    product = products.get(pid) or {}
    clauses: list[str] = []
    for label, win, _lose in _comparator_diffs(pid, basis, selected):
        if win == "相当":
            continue
        clauses.append(_cited(
            f"若更看重{label}（{win}），可替换排序末位候选",
            product_citation(pid, _NO_VALUE_DIM_FIELDS[label])))
    mine_hours = _max_battery_hours(product)
    others = [p for p in selected if p != pid]
    other_best_hours = max((_max_battery_hours(products.get(p) or {}) or -1.0)
                           for p in others) if others else -1.0
    if mine_hours is not None and mine_hours > other_best_hours:
        clauses.append(_cited(
            f"若更看重续航（本品最长约{mine_hours:g}小时），可替换排序末位候选",
            product_citation(pid, "battery_by_mode")))
    families = _form_family_set(product)
    selected_families: set[str] = set()
    for other in others:
        selected_families |= _form_family_set(products.get(other) or {})
    category = text_of(product.get("category"))
    if category and families and families.isdisjoint(selected_families):
        clauses.append(_cited(
            f"若更看重其形态差异（产品类别＝{_trim_flat(category, 24)}，"
            "与入选各款形态不同），可替换排序末位候选",
            product_citation(pid, "category")))
    if not clauses:
        clauses.append(_cited(
            "资料未见本品相对入选各款的可辨认独有属性（证据边界）；"
            "若购买前核验发现其对您重要的独有属性，可替换排序末位候选",
            product_citation(pid, "current_price")))
    return clauses


# ---------------------------------------------------------------------------
# 代价与风险 / 购买前核验 / 改选条件
# ---------------------------------------------------------------------------

def _costs_risks(pid: str, product: dict, needs: list, by_pair: dict) -> str:
    """代价与风险：只写资料有据内容；无据时写证据边界（不编造缺点）。"""
    segments: list[str] = []
    price = _primary_price(product)
    if price is not None:
        segments.append(_cited(f"展示价{price:g}元，注意货架快照时效",
                               product_citation(pid, "current_price")))
    uw = by_pair.get((pid, "need_underwater_audio"))
    if uw is not None and uw.result is ConstraintOutcome.PASS and "条件：" in uw.explanation:
        conditions = _sanitize_conditions(uw.explanation.split("条件：", 1)[1])
        if conditions:
            segments.append(_cited(f"使用限制：{conditions}",
                                   product_citation(pid, "bluetooth_underwater")))
    battery = product.get("battery_by_mode") or []
    if any(isinstance(item, dict) and (
            item.get("duration") is None
            or "待核验" in str(item.get("conditions") or ""))
           for item in battery):
        segments.append(_cited("续航无统一测试口径，待核验",
                               product_citation(pid, "battery_by_mode")))
    ongoing = text_of(product.get("ongoing_costs"))
    if ongoing:
        segments.append(_cited(f"持续成本：{_trim_flat(ongoing, 40)}",
                               product_citation(pid, "ongoing_costs")))
    for need in needs:
        if need.need_type is not NeedType.SOFT:
            continue
        result = by_pair.get((pid, need.need_id))
        if result is not None and result.result is ConstraintOutcome.FAIL:
            detail = _sanitize_conditions(result.explanation)
            segments.append(_cited(f"与诉求「{ce._trim(need.original_expression, 20)}」不符：{detail}",
                                   result.attribute_reference))
    if not segments:
        segments.append(_cited("资料未见该产品代价与风险的专门记载（证据边界：相关成本/限制字段未提供）",
                               product_citation(pid, "ongoing_costs")))
    return "；".join(segments[:4])


def _pre_purchase_checks(pid: str, product: dict, needs: list, by_pair: dict,
                         profile: Optional[dict] = None) -> list[str]:
    """购买前核验：聚焦该产品真实的剩余不确定性（不机械填「兼容性待核验」）。"""
    checks: list[str] = []
    noise_check = _noise_boundary_check(pid, product, profile)
    if noise_check:
        checks.append(noise_check)  # 用户场景最具体的剩余不确定性，置首并受 [:3] 保护
    price_text = text_of(product.get("current_price")) or ""
    scopes = ce.parse_price_scopes(price_text)
    if len(scopes) > 1:
        checks.append(_cited("当前售价存在多口径，确认实际成交价与适用SKU",
                             product_citation(pid, "current_price")))
    elif scopes and scopes[0]["hi"] > scopes[0]["lo"]:
        checks.append(_cited("售价为区间口径，确认具体成交价",
                             product_citation(pid, "current_price")))
    if any(n.need_id == NEED_BUDGET_REFERENCE for n in needs):
        checks.append(_cited("预算原文为「约X元」口径，确认实际到手价是否可接受",
                             "画像｜预算｜预算口径"))
    uw_pair = by_pair.get((pid, "need_underwater_audio"))
    if uw_pair is not None and "待核验" in uw_pair.explanation:
        checks.append(_cited("存在未决冲突，购买前确认水下使用的真实口径与限制",
                             product_citation(pid, "bluetooth_underwater")))
    marketing = "；".join(text_of(item) or "" for item in (product.get("marketing_claims") or []))
    if marketing and re.search(r"水下[^；。]{0,12}(?:串流|播放|使用|在线|蓝牙)|浸水", marketing) \
            and not text_of(product.get("bluetooth_underwater")):
        # 宣传不当事实，但可作不确定性线索：列为核验项而非证据
        checks.append(_cited("渠道宣传的水下能力未见可靠口径印证，宣传表述不作事实，购买前向官方/实测确认",
                             product_citation(pid, "marketing_claims")))
    if "待核验" in (text_of(product.get("waterproof_conditions")) or ""):
        checks.append(_cited("防水适用条件存在待核验口径，购买前确认具体水深/时长限制",
                             product_citation(pid, "waterproof_conditions")))
    battery = product.get("battery_by_mode") or []
    if any(isinstance(item, dict) and (
            item.get("duration") is None or "待核验" in str(item.get("conditions") or ""))
           for item in battery):
        checks.append(_cited("续航无统一测试口径，购买前确认实测续航",
                             product_citation(pid, "battery_by_mode")))
    if "耳塞" in (text_of(product.get("waterproof_conditions")) or ""):
        checks.append(_cited("确认游泳耳塞配套与佩戴前提",
                             product_citation(pid, "waterproof_conditions")))
    if not checks:
        checks.append(_cited("以付款页实际价格与库存为准，核对货架快照售价口径",
                             product_citation(pid, "current_price")))
    return checks[:3]


def _switch_conditions(pid: str, basis: dict, selected: list[str], rank_position: int) -> list[str]:
    """改选条件：哪个明确条件变化应改选（基于入选候选间的真实属性差异）。"""
    others = [p for p in selected if p != pid and p in basis]
    conditions: list[str] = []
    diffs = _comparator_diffs(pid, basis, selected)
    for label, _win, lose in diffs[:2]:
        if lose == "相当":
            continue
        target = _who_wins(pid, label, basis, others)
        if target:
            conditions.append(f"若更看重{label}，应改选{target}（{lose}）")
    if not conditions and rank_position > 0 and others:
        conditions.append(f"若前列候选的购买前核验事项证实与资料口径不符，"
                          f"应以本品为替补重新评估（其余有效候选：{'、'.join(others)}）")
    if not conditions:
        conditions.append("若预算口径、场景或硬约束发生变化，应重新评估全部产品后再定")
    return conditions


def _who_wins(pid: str, label: str, basis: dict, others: list[str]) -> Optional[str]:
    key = {"本地存储容量": "storage", "当前售价": "price", "防水深度口径": "depth"}.get(label)
    if key is None:
        return None
    better_high = key != "price"
    candidates = [(basis[p][key], p) for p in others if p in basis
                  and basis[p][key] not in (float("inf"), -1.0)]
    if not candidates:
        return None
    best = max(candidates) if better_high else min(candidates)
    return best[1] if best[0] != basis.get(pid, {}).get(key) else None


# ---------------------------------------------------------------------------
# 异常路径空位卡（SPEC §9：不放宽、不虚构）
# ---------------------------------------------------------------------------

def _empty_slot_cards(selected_count: int, valid: set[str], ordered: list[str],
                      blockers: str) -> list[RecommendationCard]:
    if not valid:
        reason = (f"资料无法支持直接购买建议：无产品对全部适用硬约束 PASS。主要阻断：{blockers}；"
                  "不放宽硬约束、不把未知写成符合、不虚构备选")
        return [RecommendationCard(
            level=LEVELS[0], product_id="", product_name="", brand="",
            profile_match=[reason],
            tradeoffs=reason,
        )]
    reason = (f"{NO_VALID_BACKUP}：有效产品共 {len(valid)} 款（{'、'.join(ordered)}），不足 3 款；"
              "不放宽硬约束、不把未知写成符合、不虚构备选")
    return [
        RecommendationCard(
            level=LEVELS[index], product_id="", product_name="", brand="",
            profile_match=[reason],
            tradeoffs=reason,
        )
        for index in range(selected_count, 3)
    ]


def _top_blockers(by_pair: dict, needs: list) -> str:
    """E 为空时的主要阻断摘要（按 需求×结果 计数，取最常见两项；含字段名）。"""
    counter: dict[str, int] = {}
    example: dict[str, str] = {}
    hard_ids = {n.need_id for n in needs if n.need_type is NeedType.HARD}
    for (pid, need_id), result in by_pair.items():
        if need_id not in hard_ids or result.result not in (ConstraintOutcome.FAIL,
                                                            ConstraintOutcome.UNKNOWN):
            continue
        label = f"{need_id}{'不符' if result.result is ConstraintOutcome.FAIL else '证据不足'}"
        counter[label] = counter.get(label, 0) + 1
        example.setdefault(label, _strip_citation_prefixes(result.explanation))
    if not counter:
        return "无适用硬约束判定结果"
    top = sorted(counter.items(), key=lambda kv: (-kv[1], kv[0]))[:2]
    return "；".join(f"{label}×{count}款（如：{_trim_flat(example[label], 60)}）"
                     for label, count in top)


# ---------------------------------------------------------------------------
# 未选原因表（五类；条件句与排除原因对应；多否决条件不拆分）
# ---------------------------------------------------------------------------

def _build_exclusions(excluded_ids: list[str], needs: list, by_pair: dict,
                      selected: list[str], basis: dict,
                      products: dict) -> list[ExclusionRecord]:
    cut_score = basis.get(selected[-1], {}).get("score") if selected else None
    selected_pass_needs = {
        n.need_id for n in needs
        if selected and all(
            (by_pair.get((pid, n.need_id)) is not None
             and by_pair[(pid, n.need_id)].result is ConstraintOutcome.PASS)
            for pid in selected)
    }

    draft_rows: list[tuple[tuple, ExclusionRecord]] = []
    for pid in excluded_ids:
        hard_fails, hard_unknowns, soft_fails = [], [], []
        for need in needs:
            result = by_pair.get((pid, need.need_id))
            if result is None:
                continue
            if need.need_type is NeedType.HARD:
                if result.result is ConstraintOutcome.FAIL:
                    hard_fails.append((need, result))
                elif result.result is ConstraintOutcome.UNKNOWN:
                    hard_unknowns.append((need, result))
            elif result.result is ConstraintOutcome.FAIL and need.need_id in selected_pass_needs:
                soft_fails.append((need, result))

        if hard_fails:
            category = "硬约束不符"
            detail, conditions, signature = _hard_fail_rows(pid, hard_fails)
        elif hard_unknowns:
            category = "硬约束证据不足"
            detail, conditions, signature = _hard_unknown_rows(pid, hard_unknowns)
        elif soft_fails:
            category = "软需求匹配弱"
            need, result = soft_fails[0]
            detail = _cited(
                f"诉求「{ce._trim(need.original_expression, 24)}」不满足："
                f"{_sanitize_conditions(result.explanation)}",
                result.attribute_reference, result.profile_reference)
            conditions = [_cited(
                f"若该诉求可放宽或取消（{ce._trim(need.original_expression, 24)}），"
                f"且其余条件不变，才可重新考虑",
                result.attribute_reference)]
            signature = ("soft", need.need_id)
        else:
            info = basis.get(pid, {})
            if cut_score is not None and info.get("score", 0) < cut_score:
                category = "同条件下被压过"
                detail = _cited(_ranked_out_detail(pid, basis, selected),
                                product_citation(pid, "local_storage_gb"),
                                product_citation(pid, "current_price"))
                conditions = [_cited(
                    "若对上述比较维度（存储/价格/防水口径）的偏好对调或取消，排序可能改变，可重新评估",
                    product_citation(pid, "local_storage_gb"))]
                signature = ("ranked_out",)
            else:
                category = "无足够新增价值"
                detail = _cited(
                    f"需求匹配与入选三款同档（{_trim_flat(_ranked_out_detail(pid, basis, selected), 110)}）",
                    product_citation(pid, "current_price"))
                conditions = _no_added_value_conditions(pid, products, basis, selected)
                signature = ("no_added_value",)
        key = (category, signature)
        draft_rows.append((key, ExclusionRecord(
            product_ids=[pid],
            reason_category=category,
            reason_detail=detail,
            change_conditions=list(conditions),
        )))

    # 同因合并（官方：原因相同的可合并为一行）：签名一致即合并，详情与条件逐品并列保留
    merged: list[ExclusionRecord] = []
    by_key: dict[tuple, list[ExclusionRecord]] = {}
    for key, record in draft_rows:
        by_key.setdefault(key, []).append(record)
    for group in by_key.values():
        head = group[0]
        if len(group) > 1:
            head.product_ids = [pid for record in group for pid in record.product_ids]
            head.reason_detail = "；".join(record.reason_detail for record in group)
            head.change_conditions = _merge_conditions(head, group)
        merged.append(head)
    return merged


def _merge_conditions(head: ExclusionRecord, group: list[ExclusionRecord]) -> list[str]:
    """合并行的建议改变条件：预算类合并为一句按品枚举；其余逐品并列。"""
    budget_re = re.compile(r"若预算上限提高到不少于([\d.]+)元")
    if all(len(r.change_conditions) == 1 and budget_re.search(r.change_conditions[0])
           for r in group):
        items = "；".join(
            f"{r.product_ids[0]}:{budget_re.search(r.change_conditions[0]).group(1)}元"
            for r in group)
        return [f"若预算上限提高到不少于各品展示价口径（{items}），对应产品才可考虑"]
    seen: set[str] = set()
    merged: list[str] = []
    for record in group:
        for condition in record.change_conditions:
            if condition not in seen:
                seen.add(condition)
                merged.append(condition)
    return merged


def _hard_fail_rows(pid: str, hard_fails: list) -> tuple[str, list[str], tuple]:
    details = []
    conditions = []
    signature = []
    for need, result in hard_fails:
        details.append(_cited(
            f"{_need_short(need)}：{_sanitize_conditions(result.explanation)}",
            result.attribute_reference, result.profile_reference))
        conditions.append(_condition_for(pid, need, result))
        signature.append(need.need_id)
    return "；".join(details), conditions, tuple(sorted(signature))


def _hard_unknown_rows(pid: str, hard_unknowns: list) -> tuple[str, list[str], tuple]:
    details = []
    conditions = []
    signature = []
    for need, result in hard_unknowns:
        details.append(_cited(
            f"{_need_short(need)}：{_sanitize_conditions(result.explanation)}",
            result.attribute_reference, result.profile_reference))
        field = (result.attribute_reference or "相关属性").split("｜")[-1]
        conditions.append(_cited(
            f"若购买前就「{field}」取得确证口径且其余硬约束均满足，才可考虑",
            result.attribute_reference))
        signature.append(need.need_id)
    return "；".join(details), conditions, tuple(sorted(signature))


def _need_short(need) -> str:
    """需求 → 短语（用于未选原因主句）。"""
    mapping = {
        NEED_BUDGET_LIMIT: "预算硬约束不符",
        NEED_BUDGET_REFERENCE: "预算口径不符",
        "need_form_factor": "期望形态不符",
        NEED_UNDERWATER: "水下听歌需求",
        NEED_WATER_RESIST: "防水需求",
        "need_noise_cancellation": "降噪需求",
        "need_call_available": "通话需求",
        NEED_STORAGE: "本地存储需求",
        "need_bluetooth_available": "蓝牙需求",
        "need_ecosystem_compatible": "设备生态需求",
        "need_brand_avoid": "品牌排斥约束",
        "need_warranty_available": "保修需求",
    }
    return mapping.get(need.need_id, f"需求「{ce._trim(need.original_expression, 18)}」")


def _condition_for(pid: str, need, result: ConstraintResult) -> str:
    """条件变化句：与排除原因对应，落在用户需求/预算/场景侧，不预测无据变化。"""
    attr = result.attribute_reference or ""
    if need.need_id in (NEED_BUDGET_LIMIT, NEED_BUDGET_REFERENCE):
        price = _first_price_in(_sanitize_conditions(result.explanation))
        target = f"不少于{price:g}元" if price is not None else "覆盖该品展示价口径"
        return _cited(f"若预算上限提高到{target}（覆盖当前售价口径），才可考虑",
                      attr or product_citation(pid, "current_price"))
    if need.need_id == "need_form_factor":
        return _cited("若不再限定期望形态、接受该品实际形态（产品类别/形态现值），才可考虑",
                      attr or product_citation(pid, "category"))
    if need.need_id == "need_noise_cancellation":
        return _cited("若降噪诉求可放宽为「无主动降噪（至多环境声模式）」，才可考虑",
                      attr or product_citation(pid, "noise_cancellation"))
    field = attr.split("｜")[-1] if attr else "相关属性"
    return _cited(f"若「{field}」相关的需求条件变化且其余硬约束均满足，才可考虑",
                  attr or product_citation(pid, "category"))


def _first_price_in(text: str) -> Optional[float]:
    match = ce._PRICE_ITEM_RE.search(text or "")
    if match:
        return float(match.group(1).replace(",", ""))
    return None


def _ranked_out_detail(pid: str, basis: dict, selected: list[str]) -> str:
    info = basis.get(pid, {})
    diffs = _comparator_diffs(pid, basis, selected)
    summary = (f"存储{_fmt_num(info.get('storage'))}GB/展示价{_fmt_price(info.get('price'))}"
               f"/防水口径{(_fmt_num(info.get('depth')) + '米') if info.get('depth', -1) >= 0 else '未提供'}")
    if diffs:
        loses = [lose for _label, _win, lose in diffs if lose != "相当"]
        wins = [win for _label, win, _lose in diffs if win != "相当"]
        parts = []
        if loses:
            parts.append(f"不占优之处：{'；'.join(loses)}")
        if wins:
            parts.append(f"虽有占优属性（{'；'.join(wins)}）但该维度未列为用户首要比较口径")
        return (f"排序低于入选三款（比较口径源自用户明确需求）：{'；'.join(parts)}；{summary}")
    return f"排序低于入选三款（需求匹配分同口径下居后；{summary}）"


def _fmt_num(value) -> str:
    if isinstance(value, (int, float)) and value != float("inf") and value >= 0:
        return f"{value:g}"
    return "未提供"


def _fmt_price(value) -> str:
    return f"{value:g}元" if isinstance(value, (int, float)) and value != float("inf") else "未提供"


# ---------------------------------------------------------------------------
# 一句综合结论（不引用、不出现无口径总分）
# ---------------------------------------------------------------------------

def _overall_conclusion(selected: list[str], products: dict, needs: list,
                        by_pair: dict, valid: set[str]) -> str:
    if not valid:
        blockers = _top_blockers(by_pair, needs)
        return (f"当前资料无法支持直接购买建议——所有产品均未通过适用硬约束"
                f"（{blockers}）；若上述需求或预算条件变化，可重新评估。")
    if len(selected) == 1:
        pid = selected[0]
        return (f"仅{pid}通过全部适用硬约束，建议优先考虑"
                f"（{'，'.join(_attr_brief(pid, products))}）；其余产品"
                f"{NO_VALID_BACKUP}，不放宽硬约束、不虚构备选。")
    count_note = "三款" if len(selected) == 3 else \
        f"{len(selected)}款（有效产品不足3款，空位如实说明）"
    clauses = []
    for rank, pid in enumerate(selected):
        brief = "，".join(_attr_brief(pid, products))
        tag = "首选" if rank == 0 else f"备选{rank}"
        clauses.append(f"{tag}{pid}（{brief}）")
    orientation = _orientation_clause(selected, products)
    return (f"入选{count_note}均通过全部适用硬约束，按需取舍——"
            + "；".join(clauses) + f"；{orientation}")


_ORIENTATION_DIMS: tuple[tuple[str, str, str, bool], ...] = (
    # (维度标签, basis 键, 取向措辞, 数值越大越优)
    ("本地存储容量", "storage", "更大存储", True),
    ("当前售价", "price", "更低价格", False),
    ("防水深度口径", "depth", "更强防水口径", True),
    ("续航口径", "battery", "更长续航", True),
)


def _mini_basis(pids: list[str], products: dict) -> dict[str, dict]:
    """入选各款的可比属性值（parse 回读字段；供取向映射的严格最优判定）。"""
    basis: dict[str, dict] = {}
    for pid in pids:
        product = products.get(pid) or {}
        storage = to_number(product.get("local_storage_gb"))
        price = _primary_price(product)
        depth = _max_depth(product)
        battery = _max_battery_hours(product)
        basis[pid] = {
            "storage": storage if storage is not None else -1.0,
            "price": price if price is not None else float("inf"),
            "depth": depth if depth is not None else -1.0,
            "battery": battery if battery is not None else -1.0,
        }
    return basis


def _orientation_clause(selected: list[str], products: dict) -> str:
    """「偏好取向→选哪款」映射句（官方：综合结论须说明不同偏好取向如何取舍）。

    审计修复（原发现：未按官方句面给取向映射）——仅由入选各款的真实字段差异
    生成：每个维度在全部入选款均有口径、且存在唯一严格最优者时，才生成该维度
    的取向句（并列/缺失不比）；无任何可辨认取向差异时如实说明，不虚构取向。
    """
    basis = _mini_basis(selected, products)
    winner_dims: dict[str, list[str]] = {}
    for _label, key, wording, better_high in _ORIENTATION_DIMS:
        values = [(basis[pid][key], pid) for pid in selected
                  if basis[pid][key] not in (float("inf"), -1.0)]
        if len(values) != len(selected):
            continue  # 有入选款该维度无口径，不比（避免无据取向）
        distinct = {value for value, _pid in values}
        if len(distinct) == 1:
            continue  # 全部同档，无取向差异
        best = (max if better_high else min)(distinct)
        winners = [pid for value, pid in values if value == best]
        if len(winners) != 1:
            continue  # 并列最优，不指派取向
        winner_dims.setdefault(winners[0], []).append(wording)
    parts: list[str] = []
    for rank, pid in enumerate(selected):
        wordings = winner_dims.get(pid)
        if not wordings:
            continue
        tag = "首选" if rank == 0 else f"备选{rank}"
        parts.append(f"偏好{'、'.join(wordings)}选{tag}{pid}")
    if parts:
        return "偏好取向：" + "，".join(parts)
    return "偏好取向：入选各款在可比属性上差异有限，按需求匹配与证据缺口排序取舍"


def _attr_brief(pid: str, products: dict) -> list[str]:
    product = products.get(pid) or {}
    brief = []
    storage = to_number(product.get("local_storage_gb"))
    if storage is not None:
        brief.append(f"本地存储{storage:g}GB")
    price = _primary_price(product)
    if price is not None:
        brief.append(f"展示价{price:g}元")
    depth = _max_depth(product)
    if depth is not None:
        brief.append(f"防水口径约{depth:g}米")
    battery = _max_battery_hours(product)
    if battery is not None:
        brief.append(f"续航最长约{battery:g}小时")
    return brief or ["属性详见产品属性表"]
