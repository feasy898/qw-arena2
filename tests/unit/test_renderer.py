# -*- coding: utf-8 -*-
"""tests/unit/test_renderer.py — 渲染与回读（parse 回环）单元测试。

覆盖：
- render_fact_value：标量/列表/FactCell/dict 形态/缺失；
- render_profile / parse_profile：7 字段组全覆盖、场景展开与排序、缺失表达、回环相等；
- render_products / parse_products：节结构一致、battery_by_mode 逐项展开、FactCell 取值、回环相等；
- render_recommendation / parse_recommendation：恰 3 行、综合结论、未选表、异常路径空位；
- 结构非法输入 raise ValueError。
"""
from __future__ import annotations

import pytest

from src import renderer
from src.renderer import (
    LEVELS,
    parse_products,
    parse_profile,
    parse_recommendation,
    render_fact_value,
    render_products,
    render_profile,
    render_recommendation,
)
from src.schemas import DecisionPlan, ExclusionRecord, FactCell, FactKind, FactStatus, RecommendationCard


# ---------------------------------------------------------------------------
# 测试夹具对象（手造小型对象）
# ---------------------------------------------------------------------------

def make_profile(**overrides) -> dict:
    """覆盖 7 字段组的最小画像对象（scenes 含 2 个场景，优先级乱序传入）。"""
    profile = {
        "profile_id": "User_Description_2",
        "name": "陈女士",
        "gender": "女",
        "age": 35,
        "occupation": "会计",
        "city": None,
        "product_category": "耳机",
        "desired_product_type": "头戴式游戏耳机",
        "purchase_purposes": ["打游戏", "听声辨位"],
        "budget_min": 500,
        "budget_max": 2000,
        "currency": "元",
        "budget_raw": "预算2000元左右",
        "budget_semantics": "上限",
        "devices": ["Velmora 手机"],
        "ecosystem": "Velmora 封闭系统生态",
        "ecosystem_notes": None,
        "scenes": [
            {"scene_name": "打游戏", "usage_duration": None, "usage_frequency": "周末",
             "scene_priority": None, "priority_basis": "优先级未明确", "scene_evidence": "周末打游戏"},
            {"scene_name": "地铁通勤", "usage_duration": "单程40分钟", "usage_frequency": "每天",
             "scene_priority": 1, "priority_basis": "原文明示排序", "scene_evidence": "每天地铁通勤"},
        ],
        "brand_preferences": ["Tasvin"],
        "brand_avoidances": [],
        "appearance_preferences": ["浅色系"],
        "sound_preferences": [],
        "functional_preferences": ["听声辨位精准"],
        "service_preferences": ["保修2年"],
        "other_preferences": [],
    }
    profile.update(overrides)
    return profile


def make_product(cid: str = "P001", name: str = "Ralun Pro", **overrides) -> dict:
    """单个产品对象：全字段（含 2 模式续航），覆盖产品 5 字段组。"""
    product = {
        "canonical_id": cid,
        "aliases": ["P01"] if cid == "P001" else [],
        "product_name": name,
        "brand": "Zurmek",
        "model": "RalunPro",
        "category": "骨传导游泳耳机",
        "core_functions": ["Vondir 2.0+骨传导", "游泳EQ"],
        "generation": "2024款 / Ralun Pro",
        "variants": ["深灰色", "蓝橙撞色"],
        "channel_and_shop": "电商平台甲 平台自营旗舰店",
        "listing_title": "Ralun Pro 骨传导游泳耳机",
        "noise_cancellation": "无主动降噪",
        "call_capability": None,
        "acoustic_tech": "Vondir 2.0+骨传导",
        "local_storage_gb": 32,
        "audio_formats": ["MP3", "WAV", "FLAC"],
        "bluetooth": "蓝牙5.4（渠道宣称，待核验）",
        "bluetooth_underwater": "渠道宣称可水下串流；第三方实测水下只能使用MP3模式（待核验）",
        "battery_by_mode": [
            {"mode": "蓝牙模式", "duration": "最长9小时", "conditions": "官方声明，音量50%"},
            {"mode": "MP3模式", "duration": "最长6小时", "conditions": None},
        ],
        "charging": "10分钟快充约3小时",
        "wearing_design": "颈后式开放佩戴",
        "weight_g": "约29",
        "protection_rating": "IP68",
        "waterproof_conditions": "官方声明淡水2米内最长2小时",
        "accessories_fit": ["适配泳帽和泳镜"],
        "special_features": ["游泳EQ", "MP3与Larvo离线模式"],
        "brand_origin_market": "海外音频品牌，产品在北美、欧洲与亚洲多个市场同步销售",
        "brand_category_focus": "成立以来只做骨传导与开放式运动音频",
        "brand_reputation": "待核验（「全球游泳耳机销量第一」为渠道宣称）",
        "sales_volume": "已售25",
        "user_rating": "待核验（资料未提供分值）",
        "common_praise": ["有一条反馈提及佩戴稳固"],
        "common_complaints": [],
        "feedback_sample_note": "摘录共3条，均为独立反馈；代表性未知",
        "marketing_claims": ["蓝牙5.4可在水下2米稳定串流（渠道宣传）"],
        "current_price": "1098元（展示价，平台自营旗舰店快照）",
        "original_price": 1298,
        "price_trend": "展示价1098低于优惠前价1298（同快照口径）",
        "warranty": "24个月（以销售地区政策为准）",
        "after_sales": None,
        "ongoing_costs": "待核验（磁吸充电线需单独保管）",
    }
    product.update(overrides)
    return product


def make_products() -> dict[str, dict]:
    return {
        "P002": make_product("P002", "Ralun", battery_by_mode=None),
        "P001": make_product("P001", "Ralun Pro"),
    }


def make_plan() -> DecisionPlan:
    return DecisionPlan(
        selected_products=["P001", "P002", "P003"],
        recommendation_cards=[
            RecommendationCard(
                level="首选", product_id="P001", product_name="Ralun Pro", brand="Zurmek",
                profile_match=["命中「地铁通勤听歌」需求「画像｜场景｜场景」"],
                fact_references=["产品属性｜P001｜续航（分模式）", "产品属性｜P001｜当前售价"],
                tradeoffs="较备选存储更大、续航更长",
                costs_risks="展示价1098元高于预算上限口径，需确认实际到手价",
                pre_purchase_checks=["确认到手价是否在预算内", "确认水下使用模式限制"],
                switch_conditions=["若预算降至600元以下应改选P002"],
            ),
            RecommendationCard(
                level="备选1", product_id="P002", product_name="Ralun", brand="Zurmek",
                profile_match=["价格显著更低「画像｜预算｜预算上限」"],
                fact_references=["产品属性｜P002｜当前售价"],
                tradeoffs="存储与续航弱于首选",
                costs_risks="无本地存储时依赖手机播放",
                pre_purchase_checks=["确认防水条件"],
                switch_conditions=["若需要32GB本地存储应改选P001"],
            ),
            RecommendationCard(
                level="备选2", product_id="P003", product_name="Kandro 2 Pro", brand="Yovrix",
                profile_match=["多运动场景覆盖「画像｜购买目标｜购买目的」"],
                fact_references=["产品属性｜P003｜防护等级"],
                tradeoffs="价格介于首选与备选1之间",
                costs_risks="重量较大",
                pre_purchase_checks=["确认佩戴舒适度"],
                switch_conditions=["若偏好轻量应改选备选1"],
            ),
        ],
        excluded_products=[
            ExclusionRecord(
                product_ids=["P004"],
                reason_category="硬约束不符",
                reason_detail="无蓝牙不能满足无线播放「产品属性｜P004｜蓝牙能力」",
                change_conditions=["若允许纯MP3有线方案可考虑P004"],
            ),
            ExclusionRecord(
                product_ids=["P005"],
                reason_category="软需求匹配弱",
                reason_detail="重量约30g以上与轻量诉求有差距「产品属性｜P005｜重量」",
                change_conditions=["若重量诉求放宽可考虑P005"],
            ),
        ],
        overall_conclusion="重续航与存储选P001，重价格选P002，多场景均衡选P003。",
    )


# ---------------------------------------------------------------------------
# 回环语义等价的规范形（单测口径：缺失/缺失表达文本/空列表 → None）
# ---------------------------------------------------------------------------

def _catalog_type(section: str, key: str) -> str | None:
    for _group, field_def in renderer.catalog_fields(section):
        if field_def["key"] == key:
            return field_def.get("type")
    return None


def canonical(value, ftype: str | None):
    """取值语义规范形：缺失表达文本/空列表 → None；数字字符串 → 数值。"""
    if value is None:
        return None
    if isinstance(value, FactCell):
        return canonical(value.normalized_value if value.normalized_value is not None else value.raw_value, ftype)
    if isinstance(value, dict) and ("raw_value" in value or "normalized_value" in value):
        return canonical(value.get("normalized_value") if value.get("normalized_value") is not None
                         else value.get("raw_value"), ftype)
    if isinstance(value, str):
        text = value.strip()
        if text in {"未提供", "待核验", "优先级未明确"} or text.startswith(("未提供", "待核验", "优先级未明确")):
            return None
        if ftype == "数字":
            try:
                return float(text)
            except ValueError:
                return text
        return text
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, list):
        if not value:
            return None
        item_type = "数字" if ftype == "数字" else None
        return [canonical(item, item_type) for item in value]
    return value


def canonical_profile(profile: dict) -> dict:
    """画像对象规范形（scenes 先按渲染排序规则重排，与 renderer._sorted_scenes 同口径）。"""
    out: dict = {}
    for _group, field_def in renderer.catalog_fields("profile"):
        key = field_def["key"]
        if key == "scenes":
            scenes = profile.get("scenes") or []
            out[key] = (
                [{item["key"]: canonical(scene.get(item["key"]), item.get("type"))
                  for item in field_def["item_fields"]}
                 for scene in renderer._sorted_scenes(list(scenes))]
                if scenes else None
            )
        else:
            out[key] = canonical(profile.get(key), field_def.get("type"))
    return out


def canonical_products(products: dict[str, dict]) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for cid, obj in products.items():
        normalized: dict = {}
        for _group, field_def in renderer.catalog_fields("product"):
            key = field_def["key"]
            if key == "canonical_id":
                normalized[key] = cid
            elif key == "battery_by_mode":
                items = obj.get(key) or []
                normalized[key] = (
                    [{item_def["key"]: canonical(mode.get(item_def["key"]), item_def.get("type"))
                      for item_def in field_def["item_fields"]} for mode in items]
                    if items else None
                )
            else:
                normalized[key] = canonical(obj.get(key), field_def.get("type"))
        out[cid] = normalized
    return out


# ---------------------------------------------------------------------------
# render_fact_value
# ---------------------------------------------------------------------------

class TestRenderFactValue:
    def test_scalar_and_missing(self):
        assert render_fact_value(None) == "未提供"
        assert render_fact_value("IP68") == "IP68"
        assert render_fact_value("  蓝牙5.4 ") == "蓝牙5.4"
        assert render_fact_value("") == "未提供"

    def test_list_joined_with_fullwidth_semicolon(self):
        assert render_fact_value(["MP3", "WAV"]) == "MP3；WAV"
        assert render_fact_value([]) == "未提供"

    def test_factcell_dataclass(self):
        cell = FactCell(
            field_key="battery", raw_value="最长9小时", normalized_value=None,
            fact_kind=FactKind.OFFICIAL_CLAIM, status=FactStatus.SUPPORTED,
            conditions=["音量50%"],
        )
        assert render_fact_value(cell) == "最长9小时（音量50%）"

    def test_factcell_missing_by_status(self):
        missing = FactCell(field_key="x", raw_value="", normalized_value=None, status=FactStatus.MISSING)
        assert render_fact_value(missing) == "未提供"
        conflicted = FactCell(field_key="x", raw_value="", normalized_value=None, status=FactStatus.CONFLICTED)
        assert render_fact_value(conflicted) == "待核验"

    def test_factcell_dict_form_and_unit(self):
        assert render_fact_value({"raw_value": 32, "normalized_value": 32, "unit": "GB"}) == "32GB"
        assert render_fact_value({"normalized_value": None, "raw_value": "约29", "unit": "g"}) == "约29g"
        assert render_fact_value(
            {"normalized_value": 6, "unit": "小时", "status": "附条件", "conditions": ["MP3模式"]}
        ) == "6小时（MP3模式）"

    def test_unknown_dict_raises(self):
        with pytest.raises(ValueError):
            render_fact_value({"a": 1})

    def test_cell_sanitizes_pipes_and_newlines(self):
        assert render_fact_value("a|b\nc") == "a／b；c"


# ---------------------------------------------------------------------------
# render_profile / parse_profile
# ---------------------------------------------------------------------------

class TestRenderProfile:
    def test_covers_seven_groups_in_catalog_order(self):
        md = render_profile(make_profile())
        lines = [line for line in md.splitlines() if line.startswith("|")]
        groups_in_order = []
        for line in lines[2:]:
            group = line.split("|")[1].strip()
            if not groups_in_order or groups_in_order[-1] != group:
                groups_in_order.append(group)
        assert groups_in_order == ["标识", "基本情况", "购买目标", "预算", "设备", "场景", "偏好"]
        assert md.startswith("# 用户画像\n")
        assert "| 字段组 | 字段名 | 取值 |\n| --- | --- | --- |" in md

    def test_scene_rows_sorted_by_priority_and_numbered(self):
        md = render_profile(make_profile())
        # 场景1 = 有优先级的「地铁通勤」（priority 1），未明确的「打游戏」排在最后
        assert "| 场景 | 场景1·名称 | 地铁通勤 |" in md
        assert "| 场景 | 场景2·名称 | 打游戏 |" in md
        assert "| 场景 | 场景2·使用时长 | 未提供 |" in md
        assert "| 场景 | 场景2·优先级 | 优先级未明确 |" in md

    def test_missing_markers_from_catalog(self):
        md = render_profile(make_profile())
        assert "| 基本情况 | 所在城市/地区 | 未提供 |" in md
        assert "| 设备 | 生态备注 | 未提供 |" in md

    def test_cells_sanitized(self):
        md = render_profile(make_profile(ecosystem="安卓|iOS\n双机"))
        assert "安卓／iOS；双机" in md


class TestProfileRoundTrip:
    def test_round_trip_full_object(self):
        profile = make_profile()
        parsed = parse_profile(render_profile(profile))
        assert canonical_profile(parsed) == canonical_profile(profile)

    def test_round_trip_scene_order_preserved_after_sort(self):
        parsed = parse_profile(render_profile(make_profile()))
        assert [s["scene_name"] for s in parsed["scenes"]] == ["地铁通勤", "打游戏"]
        assert parsed["scenes"][0]["scene_priority"] == 1
        assert parsed["scenes"][1]["scene_priority"] is None

    def test_round_trip_empty_scenes_and_lists(self):
        profile = make_profile(scenes=[], brand_preferences=[], purchase_purposes=["买东西"])
        parsed = parse_profile(render_profile(profile))
        assert canonical_profile(parsed) == canonical_profile(profile)
        assert parsed["scenes"] is None
        assert parsed["brand_preferences"] is None

    def test_round_trip_all_missing(self):
        profile = {key: None for key in canonical_profile(make_profile())}
        parsed = parse_profile(render_profile(profile))
        assert all(value is None for value in parsed.values())

    def test_parse_numeric_with_unit(self):
        parsed = parse_profile(render_profile(make_profile(age=28, budget_max=800)))
        assert parsed["age"] == 28
        assert parsed["budget_max"] == 800

    def test_render_parse_render_idempotent(self):
        # 冻结→回读铁律：parse 只消费渲染产出，且回读后再渲染须逐字节一致
        md = render_profile(make_profile())
        assert render_profile(parse_profile(md)) == md

    def test_annotated_missing_marker_keeps_note_on_read_back(self):
        # 裸「待核验」→ None；带说明的「待核验（…）」→ 原样保留（rules.json 证据三档 C：
        # 待核验须附条件说明，说明是取值的一部分）
        profile = make_profile(city="待核验（用户提到外地出差，常住城市待确认）")
        parsed = parse_profile(render_profile(profile))
        assert parsed["city"] == "待核验（用户提到外地出差，常住城市待确认）"
        assert parsed["ecosystem_notes"] is None  # None → 裸「未提供」→ None


class TestParseProfileErrors:
    def test_wrong_header_raises(self):
        bad = render_profile(make_profile()).replace("| 字段组 | 字段名 | 取值 |", "| 字段组 | 字段 | 取值 |", 1)
        with pytest.raises(ValueError, match="表头"):
            parse_profile(bad)

    def test_ragged_row_raises(self):
        md = render_profile(make_profile()).replace("| 预算 | 预算上限 | 2000元 |", "| 预算 | 预算上限 |", 1)
        with pytest.raises(ValueError, match="列数"):
            parse_profile(md)

    def test_unknown_row_raises(self):
        md = render_profile(make_profile()).replace(
            "| 标识 | 画像编号 |", "| 标识 | 非法字段 |", 1)
        with pytest.raises(ValueError, match="未知字段行"):
            parse_profile(md)

    def test_duplicate_row_raises(self):
        base = render_profile(make_profile())
        row = "| 标识 | 画像编号 | User_Description_2 |\n"
        md = base.replace(row, row + row, 1)
        with pytest.raises(ValueError, match="重复行"):
            parse_profile(md)

    def test_stray_line_raises(self):
        md = render_profile(make_profile()) + "这是表格外的自由文本\n"
        with pytest.raises(ValueError, match="非标题行"):
            parse_profile(md)


# ---------------------------------------------------------------------------
# render_products / parse_products
# ---------------------------------------------------------------------------

class TestRenderProducts:
    def test_sections_sorted_by_canonical_id_with_name(self):
        md = render_products(make_products())
        assert md.index("## P001 Ralun Pro") < md.index("## P002 Ralun")
        assert md.startswith("# 产品属性列表\n")

    def test_all_product_tables_identical_structure(self):
        md = render_products(make_products())
        structures = renderer.product_table_structures(md)

        import re as _re

        def project(rows):
            # 续航逐模式行（或缺失标记行）折叠为单 token 后比较（与 validators E4 同口径）
            out = []
            for group, name in rows:
                if _re.match(r"续航\d+·", name) or name == "续航（分模式）":
                    token = (group, "续航（分模式）")
                    if not out or out[-1] != token:
                        out.append(token)
                else:
                    out.append((group, name))
            return out

        projected = {tuple(project(rows)) for _cid, rows in structures}
        assert len(projected) == 1  # 折叠后两款产品表结构完全一致

    def test_battery_rows_expanded_per_mode(self):
        md = render_products(make_products())
        assert "| 产品力 | 续航1·模式 | 蓝牙模式 |" in md
        assert "| 产品力 | 续航1·时长 | 最长9小时 |" in md
        assert "| 产品力 | 续航1·条件 | 官方声明，音量50% |" in md
        assert "| 产品力 | 续航2·时长 | 最长6小时 |" in md
        # P002 无续航 → 整字段行缺失表达「待核验」
        assert "| 产品力 | 续航（分模式） | 待核验 |" in md

    def test_missing_values_use_catalog_markers(self):
        md = render_products({"P002": make_product("P002", "Ralun", battery_by_mode=None, generation=None)})
        assert "| 基础标识 | 产品代际 | 未提供 |" in md
        assert "| 基础标识 | 编号别名 | 未提供 |" in md

    def test_empty_products_raises(self):
        with pytest.raises(ValueError, match="产品对象为空"):
            render_products({})


class TestProductsRoundTrip:
    def test_round_trip_two_products(self):
        products = make_products()
        parsed = parse_products(render_products(products))
        assert canonical_products(parsed) == canonical_products(products)
        assert list(parsed) == ["P001", "P002"]

    def test_round_trip_factcell_values(self):
        products = {
            "P003": make_product(
                "P003", "Kandro 2 Pro",
                local_storage_gb=FactCell(field_key="local_storage_gb", raw_value="32",
                                          normalized_value=32, unit="GB"),
                weight_g=FactCell(field_key="weight_g", raw_value="约30", unit="g"),
            )
        }
        md = render_products(products)
        assert "| 产品力 | 本地存储容量 | 32GB |" in md
        assert "| 产品力 | 重量 | 约30g |" in md
        parsed = parse_products(md)
        assert parsed["P003"]["local_storage_gb"] == 32
        assert parsed["P003"]["weight_g"] == "约30"

    def test_round_trip_single_battery_mode(self):
        products = {"P004": make_product("P004", "VD-140", battery_by_mode=[
            {"mode": "未区分模式", "duration": "约8小时", "conditions": None},
        ])}
        parsed = parse_products(render_products(products))
        assert parsed["P004"]["battery_by_mode"] == [
            {"mode": "未区分模式", "duration": "约8小时", "conditions": None},
        ]

    def test_render_parse_render_idempotent(self):
        # 冻结→回读铁律：parse 只消费渲染产出，且回读后再渲染须逐字节一致
        md = render_products(make_products())
        assert render_products(parse_products(md)) == md

    def test_parse_canonical_id_row_mismatch_raises(self):
        md = render_products(make_products()).replace(
            "| 基础标识 | 产品编号 | P002 |", "| 基础标识 | 产品编号 | P02 |", 1)
        with pytest.raises(ValueError, match="不一致"):
            parse_products(md)

    def test_parse_two_digit_section_id_raises(self):
        md = render_products(make_products()).replace("## P002 Ralun", "## P2 Ralun", 1)
        with pytest.raises(ValueError):
            parse_products(md)

    def test_parse_heading_name_mismatch_raises(self):
        md = render_products(make_products()).replace(
            "| 基础标识 | 产品名称 | Ralun |", "| 基础标识 | 产品名称 | RalunSE |", 1)
        with pytest.raises(ValueError, match="节标题名称"):
            parse_products(md)

    def test_parse_duplicate_section_raises(self):
        products = make_products()
        products["P002"] = make_product("P002", "Ralun")
        md = render_products(products)
        # 把 P002 节复制一份（同编号两节）
        sections = md.split("## ")
        duplicated = md + "\n".join(["## " + s for s in sections[2:3]])
        with pytest.raises(ValueError, match="重复"):
            parse_products(duplicated)


# ---------------------------------------------------------------------------
# render_recommendation / parse_recommendation
# ---------------------------------------------------------------------------

class TestRenderRecommendation:
    def test_exactly_three_rows_in_level_order(self):
        md = render_recommendation(make_plan())
        rows = [line for line in md.splitlines() if line.startswith("| 首选") or line.startswith("| 备选")]
        assert len(rows) == 3
        assert [row.split("|")[1].strip() for row in rows] == list(LEVELS)

    def test_columns_and_conclusion(self):
        md = render_recommendation(make_plan())
        assert "| 推荐层级 | 产品ID | 产品 | 画像匹配与推荐理由 | 注意事项 |" in md
        assert "综合结论：重续航与存储选P001" in md

    def test_exclusion_table_merged_rows(self):
        md = render_recommendation(make_plan())
        assert "| 产品ID | 未进入前三的主要原因 | 建议改变条件 |" in md
        assert "| P004 |" in md
        assert "| P005 |" in md

    def test_exception_path_empty_slots(self):
        plan = make_plan()
        plan.recommendation_cards = plan.recommendation_cards[:1]
        plan.selected_products = ["P001"]
        md = render_recommendation(plan)
        rows = [line for line in md.splitlines() if line.startswith("| 备选")]
        assert len(rows) == 2  # 仍恰好 3 行（1 实 + 2 空位）
        assert "| 备选1 | 无 | （当前尚不构成有效备选） |" in md
        assert "当前尚不构成有效备选" in rows[0]
        parsed = parse_recommendation(md)
        assert [row["product_id"] for row in parsed["recommendations"]] == ["P001", None, None]

    def test_more_than_three_cards_raises(self):
        plan = make_plan()
        plan.recommendation_cards = list(plan.recommendation_cards) + [
            RecommendationCard(level="备选3", product_id="P004", product_name="VD-140", brand="Norvek")
        ]
        with pytest.raises(ValueError, match="超过 3"):
            render_recommendation(plan)


class TestParseRecommendation:
    def test_round_trip_fields(self):
        parsed = parse_recommendation(render_recommendation(make_plan()))
        assert [(r["level"], r["product_id"], r["product"]) for r in parsed["recommendations"]] == [
            ("首选", "P001", "Ralun Pro"),
            ("备选1", "P002", "Ralun"),
            ("备选2", "P003", "Kandro 2 Pro"),
        ]
        assert parsed["recommendations"][0]["reason"].index("产品属性｜P001｜续航（分模式）") >= 0
        assert parsed["exclusions"][0]["product_ids"] == ["P004"]
        assert parsed["exclusions"][1]["product_ids"] == ["P005"]
        assert parsed["exclusions"][0]["change_conditions"] == ["若允许纯MP3有线方案可考虑P004"]
        assert parsed["overall_conclusion"].startswith("重续航与存储选P001")

    def test_missing_conclusion_raises(self):
        md = render_recommendation(make_plan())
        bad = "\n".join(line for line in md.splitlines() if not line.startswith("综合结论：")) + "\n"
        with pytest.raises(ValueError, match="综合结论"):
            parse_recommendation(bad)

    def test_absent_exclusion_table_allowed_when_no_exclusions(self):
        # 全部产品入选（未入选为空）时未选原因表可整体缺省
        md = render_recommendation(make_plan())
        bad = md.split("## 未选原因")[0]
        parsed = parse_recommendation(bad)
        assert parsed["exclusions"] == []
        assert len(parsed["recommendations"]) == 3
