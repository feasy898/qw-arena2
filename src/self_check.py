# -*- coding: utf-8 -*-
"""src/self_check.py — 出厂质检（quality gate）：规则核心单一事实源。

定位（README-package.md「出厂质检」一节）：交付前按公开任务规格逐项自检，
不过则定点修复 / 回退。本机制不针对评分器，仅保证交付物符合任务说明的硬性要求。

单一事实源：全部纯规则函数（结构层 + 泛化事实层）与 judge 组装内核在本文件；
开发仓的确定性评分器 tools/deterministic_judge.py 反向 import 本模块，
并在此基础上注入「样例金标准回归锁」（golden 参数，开发期专用、不随包分发）——
出厂质检与打包产物永远只跑 golden=None 的口径。

纯标准库（os/re/csv）；禁止 requests/argparse。

run(output_dir, input_dir) 接口（advisor_pipeline 质检闭环用）：
  {"passed": bool,            # 无 failed_atoms 即通过
   "score_hint": 0-100,       # judge 总分提示（确定性评分口径）
   "failed_atoms": [          # 颗粒度 = 单条可执行修正指令
       {"doc": "user_profile.md", "rule": "规则3画像字段组", "message": "…"}, …]}

两层检查：
  A) 结构层：文件命名 / 表格可解析 / 字段组齐全与跨产品一致 / 推荐表形态 /
     未选覆盖 / 过程性措辞与模板残留 / 售价口径 / 推荐 ID 与预算；
  B) 泛化事实层（_generalized_fact_atoms，仅作质检 atoms，不进 judge 评分口径）：
     全部判定依据在运行时从输入资料推导，不含任何样例取值——
     - 画像硬字段与用户描述数值归一比对：年龄/预算提取数字比对
       （「30岁」≡「30」、「约3000元」≡3000）；品牌/品类用关键词包含；
     - 「现有主要设备」通用禁入规则：字段值不得含用户描述中旧设备转折语
       （此前使用/之前用/之前一直用/曾经用…）之后提及的品牌名。
       注：「换成了/最近换成」之后提及的是换机后的现用品牌，不属于禁入对象——
       禁入的是被换掉的旧品牌。
"""
import csv
import os
import re

# ---------------------------------------------------------------- 常量

REQUIRED_FILES = ["user_profile.md", "product_list.md", "recommendation.md"]

PROFILE_GROUPS = ["标识", "基本情况", "购买目标", "预算", "设备", "场景", "偏好"]           # 规则3
PRODUCT_GROUPS = ["基础标识", "产品力", "品牌力", "市场验证", "购买与使用成本"]              # 规则4
REC_LEVELS = ["首选", "备选1", "备选2"]                                                    # 规则5

BUDGET_TOLERANCE = 1.10  # 规则10 容差 +10%

ABSTAIN_PID = "无"        # 规则10：§9 异常路径的诚实拒推空位（产品ID=「无」）
MISMATCH_DECL_PATTERNS = [  # 三行全「无」时要求的错配/无法支持声明
    "无法支持", "错配", "不构成有效", "不产生有效推荐", "无有效推荐",
    "无法给出", "尚不构成有效备选", "资料不足",
]

# 规则7 过程性措辞 / 模板残留 / 截断
RESIDUE_PATTERNS = [
    "多来源取值并列保留", "并列保留", "未取平均", "资料未见直接记载", "未证实",
    "<UNDEFINED", "<图片:", "程序按来源行补录", "模型抽取缺失", "单列一条观察",
    "取值存在差异", "不直接当硬", "比较口径源自用户明确需求",
]
TRUNCATION_MARK = "…"            # 单元格内出现省略号 = 截断残留
PROCESS_LOG_PATTERNS = ["程序按来源行补录", "模型抽取缺失", "单列一条观察"]

# P4：夸张促销话术（出现在事实字段且无待核验/宣传限定）
PROMO_PATTERNS = ["爆款", "销量第一", "全球第一", "评分5.0", "太好了", "最好用", "完美"]

# P6 市场反馈字段（范畴：市场热度、用户综合评分、共性好评点与共性槽点）——按字段名匹配
P6_FIELD_PATTERNS = {
    "市场热度(销量)": r"市场热度|已售|销量",
    "用户综合评分": r"综合评分|评分",
    "共性好评点": r"共性好评|好评点|用户反馈",
    "共性槽点": r"共性槽|槽点",
}
SAMPLE_NOTE_PATTERNS = ["样本", "代表性", "个案", "不能推及", "条评价", "条反馈", "不构成共性"]

WEIGHTS = {
    "U": {"U1": 55, "U2": 20, "U3": 15, "U4": 10},
    "P": {"P1": 10, "P2": 25, "P3": 20, "P4": 20, "P5": 10, "P6": 15},
    "R": {"R1": 55, "R2": 20, "R3": 15, "R4": 10},
}


class Finding:
    """一条扣分：dimension∈{U1..R4}，deduct=该维度百分制内扣掉的分。"""

    def __init__(self, rule, dimension, deduct, message):
        self.rule = rule
        self.dimension = dimension
        self.deduct = round(float(deduct), 2)
        self.message = message

    def __repr__(self):
        return f"[{self.rule}|{self.dimension} -{self.deduct}] {self.message}"


# ---------------------------------------------------------------- 解析（纯函数）

def parse_md_table(text):
    """解析 markdown 表格 → (rows, bad_rows)。rows 为 dict 列表（键=表头）；
    列数与表头不一致的行记入 bad_rows。返回 rows 为原始单元格列表拼接。"""
    rows, bad = [], []
    header = None
    for ln in text.splitlines():
        s = ln.strip()
        if not s.startswith("|"):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if header is None:
            header = cells
            continue
        if all(re.fullmatch(r":?-{2,}:?", c) for c in cells if c) and any(c for c in cells):
            continue  # 分隔行
        if len(cells) != len(header):
            bad.append(s)
            continue
        rows.append(dict(zip(header, cells)))
    return rows, bad


def _row3(r):
    """三列画像/产品表行 → {group, field, value}"""
    return {"group": r.get("字段组", ""), "field": r.get("字段名", ""),
            "value": r.get("取值", r.get("值", ""))}


def parse_profile(path):
    rows, bad = parse_md_table(open(path, encoding="utf-8").read())
    return [_row3(r) for r in rows], bad


def parse_products(path):
    """product_list.md → {pid: {section, n_ids_in_header, rows, bad_rows}}（按 ## 分节）"""
    text = open(path, encoding="utf-8").read()
    prods = {}
    for sec in re.split(r"^##\s+", text, flags=re.M)[1:]:
        head = sec.splitlines()[0].strip()
        pids = re.findall(r"\bP\d{3}\b", head)
        if not pids:
            continue
        rows, bad = parse_md_table(sec)
        prods[pids[0]] = {"section": head, "n_ids_in_header": len(set(pids)),
                          "rows": [_row3(r) for r in rows], "bad_rows": bad}
    return prods


def parse_recommendation(path):
    """recommendation.md → {recs, conclusion, unselected, bad_rows}。推荐表与未选原因表分开解析。"""
    text = open(path, encoding="utf-8").read()
    # 「未选原因」小节缺失时（退化输出）不再崩溃：tail 记空 → 规则5/规则6 出缺陷项。
    # （原 judge 对此输入直接 ValueError；判定语义在原文档上逐字不变。）
    parts = re.split(r"未选原因", text, maxsplit=1)
    head, tail = (parts[0], "") if len(parts) == 1 else (parts[0], parts[1])
    rec_rows, bad1 = parse_md_table(head)
    unsel_rows, bad2 = parse_md_table(tail)
    conclusion = ""
    m = re.search(r"#+\s*综合结论[^\n]*\n(.*?)(?=\n#+|\Z)", text, re.S)
    if m and m.group(1).strip():
        conclusion = " ".join(x.strip() for x in m.group(1).strip().splitlines() if x.strip())
    else:
        m2 = re.search(r"综合结论[:：]\s*(\S.*)", text)
        if m2:
            conclusion = m2.group(1).strip()
    recs = [{"level": r.get("推荐层级", ""), "pid": r.get("产品ID", ""),
             "product": r.get("产品", ""), "reason": r.get("画像匹配与推荐理由", ""),
             "note": r.get("注意事项", "")} for r in rec_rows]
    unselected = []
    for r in unsel_rows:
        pid = (r.get("产品ID", "") or "").strip()
        # 官方题面允许「原因相同的可合并为一行」：展开顿号/逗号分隔的合并行
        parts = [s.strip() for s in re.split(r"[、，,]", pid) if s.strip()] if pid else [""]
        for pp in parts:
            unselected.append({"pid": pp, "reason": r.get("未进入前三的主要原因", ""),
                               "advice": r.get("建议改变条件", "")})
    return {"recs": recs, "conclusion": conclusion, "unselected": unselected,
            "bad_rows": bad1 + bad2, "text": text}


def _num(s):
    m = re.search(r"\d+(?:\.\d+)?", str(s))
    return float(m.group()) if m else None


def load_shelf(input_dir):
    """货架 CSV → {pid: {brand, model, price(展示价), orig_price(优惠前), sold}}（utf-8-sig 兼容 BOM）"""
    path = os.path.join(input_dir, "01_Ecommerce_Listings", "Ecommerce_Listing_Snapshot.csv")
    shelf = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("测试产品ID") or "").strip()
            if not pid:
                continue
            shelf[pid] = {
                "brand": (row.get("品牌") or "").strip(),
                "model": (row.get("型号") or "").strip(),
                "price": _num((row.get("展示价(元)") or "").strip()),
                "orig_price": _num((row.get("优惠前价格(元)") or "").strip()),
                "sold": (row.get("已售/付款人数") or "").strip(),
            }
    return shelf


def load_feedback_counts(input_dir):
    path = os.path.join(input_dir, "03_User_Feedback_and_Complaints", "User_Feedback_Excerpts.csv")
    cnt = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            pid = (row.get("测试产品ID") or "").strip()
            if pid:
                cnt[pid] = cnt.get(pid, 0) + 1
    return cnt


def load_user_desc(input_dir, profile_rows, explicit=None):
    """定位用户描述原文：1) --user-desc 2) 画像编号字段 3) 职业+年龄唯一匹配。返回 (text, name)。"""
    if explicit:
        return open(explicit, encoding="utf-8").read(), os.path.basename(explicit)
    desc_dir = os.path.join(input_dir, "00_User_Descriptions")
    if not os.path.isdir(desc_dir):
        return "", ""
    files = sorted(f for f in os.listdir(desc_dir) if f.endswith(".txt"))
    pid_row = next((r for r in profile_rows if "画像编号" in r["field"]), None)
    ref = pid_row["value"].strip() if pid_row else ""
    if ref in files:
        return open(os.path.join(desc_dir, ref), encoding="utf-8").read(), ref
    m = re.search(r"User_Description_(\d+)", ref)
    if m:
        name = f"User_Description_{m.group(1)}.txt"
        if os.path.exists(os.path.join(desc_dir, name)):
            return open(os.path.join(desc_dir, name), encoding="utf-8").read(), name
    def get(pat):
        for r in profile_rows:
            if re.search(pat, r["field"]):
                return r["value"]
        return ""
    occ, age = get(r"职业"), get(r"年龄")
    hits = []
    for f in files:
        t = open(os.path.join(desc_dir, f), encoding="utf-8").read()
        if occ and occ in t and (not age or age in t):
            hits.append(f)
    if len(hits) == 1:
        return open(os.path.join(desc_dir, hits[0]), encoding="utf-8").read(), hits[0]
    return "", ""


# ---------------------------------------------------------------- 规则（每条纯函数，0/1 + 扣分）

def weighted_avg(pairs):
    """[(value, weight)] 加权平均；value=None 的维度视为未计测——剔除并重归一权重
    （「未计测」≠ 0 分）。权重和为 0 或全未计测 → None。"""
    vals = [(v, w) for v, w in pairs if v is not None]
    den = sum(w for _, w in vals)
    if not den:
        return None
    return round(sum(v * w for v, w in vals) / den, 1)

def rule1_file_naming(output_dir):
    """规则1：三份文件命名精确。缺一份 → 对应文件结构维度记 0（-100）。"""
    out = []
    dim = {"user_profile.md": "U4", "product_list.md": "P5", "recommendation.md": "R4"}
    for name in REQUIRED_FILES:
        if not os.path.isfile(os.path.join(output_dir, name)):
            out.append(Finding("规则1命名", dim[name], 100, f"缺少必需文件 {name}（命名须完全一致）"))
    extras = [f for f in os.listdir(output_dir) if f.endswith(".md") and f not in REQUIRED_FILES]
    if extras:
        out.append(Finding("规则1命名", "U4", 5, f"产出目录存在多余 .md 文件: {extras}"))
    return out


def rule2_table_parse(bad_rows_by_file):
    """规则2：Markdown 表格可解析（列数一致、无坏行）。每处坏行 -10。"""
    out = []
    dim_map = {"user_profile.md": "U4", "product_list.md": "P5", "recommendation.md": "R4"}
    for fname, bad in bad_rows_by_file.items():
        for i, br in enumerate(bad[:5], 1):
            out.append(Finding("规则2表格", dim_map[fname], 10,
                               f"{fname} 第{i}处坏行（列数与表头不一致）: {br[:60]}"))
    return out


def rule3_profile_groups(profile_rows):
    """规则3：画像 7 字段组（标识/基本情况/购买目标/预算/设备/场景/偏好）齐全。缺一组 -15（U4）。"""
    out = []
    present = {r["group"] for r in profile_rows}
    for g in PROFILE_GROUPS:
        if not any(g == p or g in p for p in present):
            out.append(Finding("规则3画像字段组", "U4", 15, f"画像缺少字段组「{g}」"))
    return out


def rule4_product_groups_consistency(prods):
    """规则4：5 字段组齐全（缺组 -10）+ 所有产品表结构一致（同组字段名集合逐一比对，
    每个偏离产品每组 -5）。均归 P5。"""
    out = []
    for pid, p in sorted(prods.items()):
        groups = {r["group"] for r in p["rows"]}
        for g in PRODUCT_GROUPS:
            if not any(g == x or g in x for x in groups):
                out.append(Finding("规则4产品字段组", "P5", 10, f"{pid} 缺少字段组「{g}」"))
    per = {pid: {} for pid in prods}
    for pid, p in prods.items():
        for r in p["rows"]:
            per[pid].setdefault(r["group"], set()).add(r["field"])
    pids = sorted(prods)
    if len(pids) >= 2:
        ref = pids[0]
        for g in PRODUCT_GROUPS:
            ref_set = per[ref].get(g, set())
            for pid in pids[1:]:
                cur = per[pid].get(g, set())
                if cur != ref_set:
                    out.append(Finding("规则4结构一致", "P5", 5,
                                       f"表结构不一致：{pid} 与 {ref} 在「{g}」组字段名不同: {sorted(ref_set ^ cur)}"))
    return out


def rule5_rec_shape(rec):
    """规则5：推荐表恰好 3 行（首选/备选1/备选2）+ 综合结论非空。归 R4。"""
    out = []
    levels = [r["level"] for r in rec["recs"]]
    if levels != REC_LEVELS:
        out.append(Finding("规则5推荐表", "R4", 40, f"推荐表层级/行数异常: {levels}（应恰为 首选/备选1/备选2）"))
    if not rec["conclusion"]:
        out.append(Finding("规则5综合结论", "R4", 20, "综合结论缺失或为空"))
    return out


def rule7_residue(cells, dimension, tag, per_cell=3.0, cap=60.0):
    """规则7：过程性措辞/模板残留检测。每命中单元格 -per_cell（封顶 cap）；
    出现程序日志式短语再平顶 -10。cells = [(标签, 文本), …]"""
    out = []
    n, examples = 0, []
    for label, cell in cells:
        hits = [p for p in RESIDUE_PATTERNS if p in cell]
        if hits or TRUNCATION_MARK in cell:
            n += 1
            if len(examples) < 3:
                examples.append(f"{label}: {';'.join(hits) or '截断…'}")
    if n:
        out.append(Finding("规则7模板残留", dimension, min(n * per_cell, cap),
                           f"{tag} 命中 {n} 处模板/过程性措辞或截断残留，如 {' | '.join(examples)}"))
    if any(any(p in cell for p in PROCESS_LOG_PATTERNS) for _, cell in cells):
        out.append(Finding("规则7程序日志", dimension, 10,
                           f"{tag} 出现程序日志式短语（模型抽取缺失/程序按来源行补录/单列一条观察）"))
    return out


def rule8_price_from_shelf(prods, shelf):
    """规则8：当前售价须取自货架「展示价(元)」列，禁止用优惠前价格。
    单元格不含货架展示价 -10；把优惠前价标注为展示价/当前售价 -10。归 P3。"""
    out = []
    for pid, p in sorted(prods.items()):
        row = next((r for r in p["rows"] if r["field"].strip() == "当前售价"), None)
        if row is None:
            out.append(Finding("规则8售价来源", "P3", 10, f"{pid} 缺少「当前售价」字段"))
            continue
        cell = row["value"]
        info = shelf.get(pid)
        if not info:
            continue
        if info["price"] is not None and \
           not re.search(r"(?<!\d)" + re.escape(str(int(info["price"]))) + r"(?!\d)", cell):
            out.append(Finding("规则8售价来源", "P3", 10,
                               f"{pid} 当前售价未包含货架展示价 {int(info['price'])} 元"))
        if info["orig_price"] is not None and info["price"] != info["orig_price"]:
            op = str(int(info["orig_price"]))
            if re.search(r"(展示价|当前售价)[^|；;]*" + re.escape(op), cell):
                out.append(Finding("规则8禁用优惠前价", "P3", 10,
                                   f"{pid} 当前售价把优惠前价格 {op} 元并列为展示价/当前售价"
                                   f"（货架展示价应为 {int(info['price'])} 元）"))
    return out


def rule10_rec_ids_budget(rec, prods, budget, rec_text=""):
    """规则10：推荐产品 ID 必须存在于产品表，且当前售价≤预算×1.1。
    「无」行 = §9 异常路径的诚实拒推空位：不计为不存在、不参与预算/阶梯有效性。
    三行全「无」时改为检查错配/无法支持声明：在场 → 阶梯记 None（R1 不计测，
    聚合时剔除权重）；缺失 → 阶梯 0（缺陷）。
    返回 (findings, R1阶梯分或None, 有效性dict)。阶梯：首选无效=0；全有效=100；
    首选+1=67；仅首选=34。"""
    out = []
    cap = budget * BUDGET_TOLERANCE if budget else None
    validity = {}
    pids = [r["pid"].strip() for r in rec["recs"]]
    abstain_all = bool(pids) and all(p == ABSTAIN_PID for p in pids)
    if abstain_all:
        if any(p in rec_text for p in MISMATCH_DECL_PATTERNS):
            return out, None, validity
        out.append(Finding("规则10拒推声明", "R1", 55,
                           "三行推荐全为「无」（§9 异常路径）但推荐报告未含错配/无法支持声明"
                           "（须明示资料无法支持直接购买建议，R1 记 0）"))
        return out, 0.0, validity
    for r in rec["recs"]:
        pid = r["pid"].strip()
        if pid == ABSTAIN_PID:
            continue  # 诚实拒推空位：不计为不存在，不计入阶梯
        ok_id = pid in prods
        price = None
        if ok_id:
            row = next((x for x in prods[pid]["rows"] if x["field"].strip() == "当前售价"), None)
            price = _num(row["value"]) if row else None
        ok_price = price is not None and cap is not None and price <= cap
        validity[pid] = ok_id and ok_price
        if not ok_id:
            out.append(Finding("规则10推荐ID", "R1", 0, f"推荐产品 {pid} 不存在于产品表"))
        elif not ok_price:
            out.append(Finding("规则10超预算", "R1", 0,
                               f"推荐产品 {pid} 当前售价 {price} 元超出预算上限 "
                               f"{budget}×{BUDGET_TOLERANCE}={cap:.0f} 元"))
    if rec["recs"] and rec["recs"][0]["level"] == "首选":
        first_ok = validity.get(rec["recs"][0]["pid"].strip(), False)
        rest_ok = sum(1 for r in rec["recs"][1:] if validity.get(r["pid"].strip(), False))
        ladder = 0 if not first_ok else (100 if rest_ok >= 2 else (67 if rest_ok == 1 else 34))
    else:
        ladder = 0
    return out, ladder, validity


def rule_rec_price_consistency(rec, prods, shelf):
    """规则8 推荐侧延伸（R3）：推荐报告把优惠前价当作当前/展示价引用 → 事实错误 -15。"""
    out = []
    for r in rec["recs"]:
        pid = r["pid"].strip()
        info = shelf.get(pid)
        if not info or info["orig_price"] is None or info["price"] == info["orig_price"]:
            continue
        cited = r["reason"] + " " + r["note"]
        if re.search(r"(展示价|当前售价)[^|；;]*" + re.escape(str(int(info["orig_price"]))), cited):
            out.append(Finding("规则8推荐侧错价", "R3", 15,
                               f"{pid} 推荐报告把优惠前价格 {int(info['orig_price'])} 元当作当前/展示价引用"
                               f"（货架展示价 {int(info['price'])} 元）"))
    return out


def p2_shelf_facts(prods, shelf):
    """P2 专项：品牌/型号与货架一致（品牌 -8、型号 -5）；重量 >1000g 视为跨源污染 -10
    （耳机品类不合理，典型如把价格数值误作重量）。"""
    out = []
    norm = lambda s: re.sub(r"[\s/／]+", "", s)
    for pid, p in sorted(prods.items()):
        brand = next((r["value"] for r in p["rows"] if r["field"].strip() == "品牌"), "")
        model = next((r["value"] for r in p["rows"] if r["field"].strip() == "型号"), "")
        info = shelf.get(pid)
        if info:
            if info["brand"] and brand and info["brand"] not in brand:
                out.append(Finding("P2品牌核对", "P2", 8, f"{pid} 品牌「{brand}」与货架「{info['brand']}」不一致"))
            if info["model"] and model and \
               norm(info["model"]) not in norm(model) and norm(model) not in norm(info["model"]):
                out.append(Finding("P2型号核对", "P2", 5,
                                   f"{pid} 型号「{model}」与货架型号「{info['model']}」不匹配"))
        for r in p["rows"]:
            if "重量" in r["field"]:
                m = re.search(r"(\d+(?:\.\d+)?)\s*g", r["value"])
                if m and float(m.group(1)) > 1000:
                    out.append(Finding("P2事实合理性", "P2", 10,
                                       f"{pid}「{r['field']}」=「{r['value']}」超 1000g，"
                                       f"耳机品类不合理（疑似价格等数值误作重量，跨源污染）"))
    return out


def p4_promo_as_fact(prods):
    """P4 专项：夸张促销话术写进事实字段（基础标识/产品力/品牌力）且无待核验/渠道宣称限定。-15/处。"""
    out = []
    fact_groups = ("基础标识", "产品力", "品牌力")
    for pid, p in sorted(prods.items()):
        for r in p["rows"]:
            if not any(g in r["group"] for g in fact_groups):
                continue
            for pat in PROMO_PATTERNS:
                if pat in r["value"] and not re.search(r"待核验|渠道宣称|宣传|不作为事实", r["value"]):
                    out.append(Finding("P4促销当事实", "P4", 15,
                                       f"{pid}「{r['group']}|{r['field']}」把宣传/评价语「…{pat}…」写成事实"
                                       f"且无待核验限定"))
    return out


def p6_market_feedback(prods, feedback_counts):
    """P6 专项：市场验证组须覆盖 热度/评分/好评点/槽点 四类字段（字段名匹配，缺 -5/产品/类）；
    反馈样本 <3 条而写「共性」结论时须有 样本不足/代表性/个案 标注（-10）。"""
    out = []
    for pid, p in sorted(prods.items()):
        market_rows = [r for r in p["rows"] if "市场验证" in r["group"]]
        names_blob = " ".join(r["field"] for r in market_rows)
        for fname, pat in P6_FIELD_PATTERNS.items():
            if not re.search(pat, names_blob):
                out.append(Finding("P6字段覆盖", "P6", 5, f"{pid} 市场验证缺少「{fname}」类字段"))
        if feedback_counts.get(pid, 0) < 3:
            common_claims = [r for r in market_rows
                             if ("共性" in r["field"] or re.search(r"共性|普遍|大多", r["value"]))
                             and r["value"].strip() not in ("", "未提供", "待核验")]
            note = " ".join(r["value"] for r in market_rows)
            if common_claims and not any(s in note for s in SAMPLE_NOTE_PATTERNS):
                out.append(Finding("P6样本纪律", "P6", 10,
                                   f"{pid} 反馈样本仅 {feedback_counts.get(pid, 0)} 条却写「共性」结论，"
                                   f"无 样本不足/代表性/个案 标注（不得以个案冒充共性）"))
    return out


def p1_score(prods, shelf):
    """P1：产品识别与型号归一——覆盖货架全集（漏/多 -20）、无重复（-30）、无表头合并（-30）。"""
    if not prods:
        return 0.0
    score = 100.0
    ids = list(prods.keys())
    score -= 30 * sum(1 for p in prods.values() if p.get("n_ids_in_header", 1) > 1)
    want, got = set(shelf), set(ids)
    score -= 20 * len(want - got)
    score -= 20 * len(got - want)
    score -= 30 * (len(ids) - len(got))
    return max(0.0, round(score, 1))


# ---------------------------------------------------------------- 组装内核

def _core(output_dir, input_dir, user_desc_path=None, golden=None):
    """judge 组装内核。返回 judge 公开输出所需的全部内部量；judge() 与 run() 共用，
    保证单一事实源。

    golden = 可选的外部事实核对层（开发仓评分器注入的样例金标准回归锁；
    出厂质检与打包产物恒为 None）。提供时须具备：
      golden.active(desc, explicit) -> bool          —— 是否启用金标准比对
      golden.profile_crosscheck(profile_rows, desc)  —— 画像事实核对
          -> (findings, field_hits, scene_hits)
      golden.preference_items(profile_rows)          —— 偏好逐项命中核对 -> findings
      golden.u1_atoms: int                           —— U1 计分原子总数
    """
    issues = []

    def add(fs):
        issues.extend(fs)

    add(rule1_file_naming(output_dir))
    paths = {n: os.path.join(output_dir, n) for n in REQUIRED_FILES}
    missing = [n for n in REQUIRED_FILES if not os.path.isfile(paths[n])]

    profile_rows, prof_bad = ([], []) if "user_profile.md" in missing else parse_profile(paths["user_profile.md"])
    prods = {} if "product_list.md" in missing else parse_products(paths["product_list.md"])
    rec = {"recs": [], "conclusion": "", "unselected": [], "bad_rows": []} \
        if "recommendation.md" in missing else parse_recommendation(paths["recommendation.md"])

    # 货架/反馈 CSV 缺失时按无参照数据处理（交叉核对类规则自动不命中）；
    # 文件在场时加载行为与原 judge 逐字一致。
    shelf = load_shelf(input_dir) if input_dir and os.path.isfile(
        os.path.join(input_dir, "01_Ecommerce_Listings", "Ecommerce_Listing_Snapshot.csv")) else {}
    feedback_counts = load_feedback_counts(input_dir) if input_dir and os.path.isfile(
        os.path.join(input_dir, "03_User_Feedback_and_Complaints", "User_Feedback_Excerpts.csv")) else {}
    desc, desc_name = load_user_desc(input_dir, profile_rows, user_desc_path) if input_dir else ("", "")
    fact_on = bool(golden is not None
                   and golden.active(desc, explicit=bool(user_desc_path)))

    bad_by_file = {"user_profile.md": prof_bad,
                   "product_list.md": [b for p in prods.values() for b in p["bad_rows"]],
                   "recommendation.md": rec["bad_rows"]}
    add(rule2_table_parse(bad_by_file))
    add(rule3_profile_groups(profile_rows))
    add(rule4_product_groups_consistency(prods))
    add(rule5_rec_shape(rec))
    u1_hits = scene_hits = 0
    if fact_on:
        r9_findings, u1_hits, scene_hits = golden.profile_crosscheck(profile_rows, desc)
        add(r9_findings)

    prod_cells = [(f"{pid}|{r['group']}|{r['field']}", r["value"])
                  for pid, p in sorted(prods.items()) for r in p["rows"]]
    prof_cells = [(f"{r['group']}|{r['field']}", r["value"]) for r in profile_rows]
    rec_cells = [(f"推荐|{r['level']}|{col}", r[col]) for r in rec["recs"] for col in ("reason", "note")]
    unsel_cells = [(f"未选|{u['pid']}", u["reason"] + " " + u["advice"]) for u in rec["unselected"]]
    add(rule7_residue(prof_cells, "U3", "user_profile.md"))
    add(rule7_residue(prod_cells, "P3", "product_list.md"))
    add(rule7_residue(rec_cells, "R3", "recommendation.md 推荐表"))
    add(rule7_residue(unsel_cells, "R2", "recommendation.md 未选原因"))
    add(rule8_price_from_shelf(prods, shelf))
    add(rule_rec_price_consistency(rec, prods, shelf))
    add(p2_shelf_facts(prods, shelf))
    add(p4_promo_as_fact(prods))
    add(p6_market_feedback(prods, feedback_counts))
    if fact_on:
        add(golden.preference_items(profile_rows))

    # 规则10：R1 阶梯（预算取自画像「预算」字段数值）；「无」行按 §9 异常路径处理
    budget = None
    for r in profile_rows:
        if "预算" in r["field"] and _num(r["value"]) is not None:
            budget = _num(r["value"])
            break
    r10_findings, r1_ladder, validity = rule10_rec_ids_budget(
        rec, prods, budget, rec_text=rec.get("text", ""))
    add(r10_findings)

    # 规则6：未选原因覆盖全部未入选产品
    rec_pids = [r["pid"].strip() for r in rec["recs"]]
    unselected_expected = sorted(set(prods) - set(rec_pids))
    covered = {u["pid"].strip() for u in rec["unselected"]}
    for pid in unselected_expected:
        if pid not in covered:
            add([Finding("规则6未选覆盖", "R2", 30, f"未选原因缺少未入选产品 {pid}")])

    # ---- 维度聚合（各维度百分制起评 100，按 Findings 扣分；U1/R1 按官方专门算法；
    #      未计测维度（结构层下的 U1/U2、§9 全「无」有声明时的 R1）记 None，
    #      聚合时剔除对应权重重新归一——「未计测」≠ 0 分）
    deducted = {}
    for f in issues:
        deducted[f.dimension] = deducted.get(f.dimension, 0) + f.deduct

    raw = {}
    if fact_on:
        raw["U1"] = round(100.0 * (u1_hits + scene_hits) / golden.u1_atoms, 2)
        raw["U2"] = max(0.0, 100 - deducted.get("U2", 0))
    else:
        raw["U1"] = None   # 事实层未启用：结构层不计测，剔除权重
        raw["U2"] = None
    raw["U3"] = max(0.0, 100 - deducted.get("U3", 0))  # 结构层仅残留扣分；事实层另含金标准冲突
    raw["U4"] = 0.0 if "user_profile.md" in missing else max(0.0, 100 - deducted.get("U4", 0))
    raw["P1"] = p1_score(prods, shelf)
    raw["P2"] = max(0.0, 100 - deducted.get("P2", 0))
    raw["P3"] = max(0.0, 100 - deducted.get("P3", 0))
    raw["P4"] = max(0.0, 100 - deducted.get("P4", 0))
    raw["P5"] = 0.0 if "product_list.md" in missing else max(0.0, 100 - deducted.get("P5", 0))
    raw["P6"] = max(0.0, 100 - deducted.get("P6", 0))
    raw["R1"] = float(r1_ladder) if r1_ladder is not None else None
    raw["R2"] = 0.0 if "recommendation.md" in missing else max(0.0, 100 - deducted.get("R2", 0))
    raw["R3"] = 0.0 if "recommendation.md" in missing else max(0.0, 100 - deducted.get("R3", 0))
    raw["R4"] = 0.0 if "recommendation.md" in missing else max(0.0, 100 - deducted.get("R4", 0))

    def wsum(env):
        return weighted_avg([(raw[s], w) for s, w in WEIGHTS[env].items()])

    U, P, R = wsum("U"), wsum("P"), wsum("R")
    envs = [x for x in (U, P, R) if x is not None]
    total = round(sum(envs) / len(envs), 1) if envs else 0.0

    abstain_all = bool(rec["recs"]) and all(r["pid"].strip() == ABSTAIN_PID for r in rec["recs"])
    hard_rules = {
        "1_文件命名精确": {"status": "pass" if not missing else "FAIL", "note": f"缺失={missing or '无'}"},
        "2_表格可解析": {"status": "pass" if not any(bad_by_file.values()) else "FAIL",
                     "note": {k: len(v) for k, v in bad_by_file.items()}},
        "3_画像7字段组": {"status": "pass" if not any(f.rule == "规则3画像字段组" for f in issues) else "FAIL",
                      "note": sorted({r["group"] for r in profile_rows})},
        "4_产品5字段组+结构一致": {"status": "pass" if not any(f.rule.startswith("规则4") for f in issues) else "FAIL",
                          "note": f"产品数={len(prods)}；明细见 规则4 issues"},
        "5_推荐3行+结论非空": {"status": "pass" if not any(f.rule.startswith("规则5") for f in issues) else "FAIL",
                        "note": f"行数={len(rec['recs'])} 结论={'有' if rec['conclusion'] else '空'}"},
        "6_未选原因覆盖": {"status": "pass" if set(unselected_expected) <= covered else "FAIL",
                       "note": f"应覆盖={unselected_expected} 已覆盖={sorted(covered)}"},
        "7_无模板残留": {"status": "pass" if not any(f.rule.startswith("规则7") for f in issues) else "FAIL",
                     "note": "命中明细见 规则7 issues"},
        "8_售价取展示价": {"status": "pass" if not any(f.rule.startswith("规则8") for f in issues) else "FAIL",
                      "note": "命中明细见 规则8 issues"},
        "9_画像事实核对": {"status": "skipped" if not fact_on else
                       ("pass" if not any(f.rule.startswith("规则9") for f in issues) else "FAIL"),
                     "note": (f"事实层未启用（用户描述={desc_name or '未找到'}；出厂质检不含"
                              "金标准回归锁，U1/U2 不计测）" if not fact_on else
                              f"用户描述={desc_name}；U1命中 {u1_hits}/{9}+场景 {scene_hits}/{5}")},
        "10_推荐ID与预算": {"status": ("pass" if r1_ladder is None else "FAIL")
                        if abstain_all else ("pass" if all(validity.values()) else "FAIL"),
                       "note": ("三行全「无」（§9 异常路径）；错配声明在场，R1 不计测"
                                if abstain_all and r1_ladder is None else
                                "三行全「无」（§9 异常路径）；缺错配声明，R1=0"
                                if abstain_all else
                                f"有效性={validity} 预算={budget}(×{BUDGET_TOLERANCE}容差)")},
    }

    return {
        "issues": issues,
        "hard_rules": hard_rules,
        "raw": raw,
        "scores": {"U": U, "P": P, "R": R},
        "total": total,
        "missing": missing,
        "bad_by_file": bad_by_file,
        "fact_on": fact_on,
        "desc": desc,
        "desc_name": desc_name,
        "budget": budget,
        "u1_hits": u1_hits,
        "scene_hits": scene_hits,
        "abstain_all": abstain_all,
        "profile_rows": profile_rows,
        "prods": prods,
        "rec": rec,
        "shelf": shelf,
        "unselected_expected": unselected_expected,
        "covered": covered,
        "validity": validity,
        "r1_ladder": r1_ladder,
    }


def judge(output_dir, input_dir, user_desc_path=None, label=None, golden=None):
    """确定性评分器公开入口。golden 参数见 _core——出厂/打包环境恒不传（None），
    golden 参数仅供开发侧评分工具注入样例金标准回归锁，运行时恒不启用。"""
    c = _core(output_dir, input_dir, user_desc_path, golden=golden)
    issues = c["issues"]
    raw = c["raw"]
    return {
        "label": label or os.path.basename(os.path.normpath(output_dir)),
        "output_dir": output_dir,
        "mode": "full" if c["fact_on"] else "structure",
        "fact_layer": c["fact_on"],
        "user_desc": c["desc_name"],
        "budget": c["budget"],
        "unmeasured_dims": sorted(k for k, v in raw.items() if v is None),
        "scores": c["scores"],
        "total": c["total"],
        "issues": [repr(f) for f in sorted(issues, key=lambda x: -x.deduct)],
        "detail": {k: (round(v, 1) if isinstance(v, (int, float)) else v)
                   for k, v in sorted(raw.items())},
        "hard_rules": c["hard_rules"],
    }


# ---------------------------------------------------------------- 出厂质检门（run / atoms / 注入清单）

# 维度 → 产出文件（failed_atoms 的 doc 字段：颗粒度=单条可执行修正指令，指明出现在哪份文档）
_DIM_DOC = {}
for _d in ("U1", "U2", "U3", "U4"):
    _DIM_DOC[_d] = "user_profile.md"
for _d in ("P1", "P2", "P3", "P4", "P5", "P6"):
    _DIM_DOC[_d] = "product_list.md"
for _d in ("R1", "R2", "R3", "R4"):
    _DIM_DOC[_d] = "recommendation.md"

_HEDGE_VALUES = ("未提供", "待核验", "无", "暂无")

# 旧设备转折语（此后提及的品牌名 = 已被换掉的旧品牌 → 「现有主要设备」禁入）
_OLD_DEVICE_MARKER_RE = re.compile(
    r"(?:此前|之前|曾经|曾|原先|原来)(?:一直)?(?:使用|用)(?:的是|过的?)?")
# 旧品牌子句截断：句读或换机措辞（换成了/最近换成 等之后提及的是现用品牌，非禁入对象）
_CLAUSE_CUT_RE = re.compile(r"[，。；；！？、\n]|最近|后来|现在|换成|换用|换到|改用")
_CLAUSE_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9&\-_.]*|[\u4e00-\u9fff]{2,}")
_DEVICE_STOPWORDS = {"手机", "电话", "设备", "耳机", "电脑", "平板", "手表", "品牌",
                     "产品", "系统", "一部", "一台", "一直", "用的", "这个", "那个",
                     "阵营", "安卓", "苹果", "封闭", "体系"}
_DEVICE_FIELD_PAT = r"现有主要设备|现有设备|主要设备|^设备$"

# 关键词包含类检查统一套用两条防误报规则（历史误报回归锁）——
#   ① 否定表述豁免：分项取值以 无/没有/未/不 开头（如品牌偏好「无特别偏好」）不是
#      品牌/品类实体，直接通过；分项含拉丁字母品牌词时仍核对；
#   ② 词元交集：取值切词元（拉丁串整串、CJK 连串取全部二字滑窗，「便携蓝牙音箱」→
#      便携/携蓝/蓝牙/牙音/音箱），剔除通用停用词后 ≥1 个词元见于原文即 PASS，
#      全部词元都不在原文才 FAIL；剔除后无剩余词元（全是通用词）则跳过检查。
_NEGATION_PREFIX_RE = re.compile(r"^(?:无|没有|未|不)")
_KEYWORD_STOPWORDS = {
    "耳机", "产品", "商品", "这类", "这种", "该类", "之类", "同类", "东西",
    "品牌", "牌子", "设备", "型号", "功能", "类型", "种类", "用途",
    "偏好", "要求", "需求", "特别", "没有", "不是", "不能", "不要", "不想",
}


def _keyword_tokens(text):
    """取值分项 → 非通用词元列表（保序去重）：拉丁/数字串整串成词元（去首尾点号）；
    CJK 连串取全部二字滑窗（词元须 ≥2 字，与 _CLAUSE_TOKEN_RE 同口径）。
    例：「水下听歌耳机（游泳耳机）」→ 水下/下听/听歌/歌耳/游泳/泳耳（耳机为停用词）。"""
    tokens = []
    for raw in _CLAUSE_TOKEN_RE.findall(str(text or "")):
        tok = raw.strip("._-")
        if len(tok) < 2:
            continue
        if re.fullmatch(r"[\u4e00-\u9fff]{2,}", tok):
            candidates = [tok[i:i + 2] for i in range(len(tok) - 1)]
        else:
            candidates = [tok]
        for c in candidates:
            if c not in _KEYWORD_STOPWORDS and c not in tokens:
                tokens.append(c)
    return tokens


def _numbers(text):
    """提取全部数字串（整数/小数），用于数值归一比对。"""
    return re.findall(r"\d+(?:\.\d+)?", str(text or ""))


def _contains_number(desc, num):
    """desc 中按数字边界查找数值串（与 rule8 的数字定位同口径）。"""
    return re.search(r"(?<![\d.])" + re.escape(num) + r"(?![\d.])", desc) is not None


def _old_device_brands(desc):
    """用户描述中旧设备转折语（此前使用/之前用/之前一直用/曾经用…）之后提及的品牌名候选。

    「换成了/最近换成」之后提及的是换机后的现用品牌，不在提取范围——
    禁入的是被换掉的旧品牌。"""
    brands = []
    for m in _OLD_DEVICE_MARKER_RE.finditer(desc):
        rest = desc[m.end():]
        cut = _CLAUSE_CUT_RE.search(rest)
        clause = rest[:cut.start()] if cut else rest
        for tok in _CLAUSE_TOKEN_RE.findall(clause):
            tok = tok.strip("._-")
            if len(tok) >= 2 and tok not in _DEVICE_STOPWORDS and tok not in brands:
                brands.append(tok)
    return brands


def _generalized_fact_atoms(profile_rows, desc):
    """泛化事实层检查（判定依据全部在运行时从输入资料推导，任何用户的描述均可用）：
    年龄/预算数值归一比对、品类/品牌关键词包含（套用 否定表述豁免 + 词元交集
    两条防误报规则）、「现有主要设备」旧品牌禁入（同口径套用否定表述豁免）。
    返回 [Finding,…]（doc 固定为 user_profile.md）。描述未定位到时整体跳过；
    单项在画像或描述任一侧无数据时跳过（无参照不做断言）。"""
    out = []

    def get_value(pat):
        for r in profile_rows:
            if re.search(pat, r["field"]):
                return r["value"]
        return ""

    def add(rule, message):
        out.append(Finding(rule, "U1", 0, message))

    if not desc:
        return out

    # 年龄：提取数字比对（「30岁」≡「30」）
    age_v = get_value(r"年龄").strip()
    if age_v and age_v not in _HEDGE_VALUES:
        for num in _numbers(age_v):
            if not _contains_number(desc, num):
                add("自检画像数值归一",
                    f"年龄取值「{age_v}」中的数值 {num} 未见于用户描述"
                    f"（数值归一比对：30岁≡30；须与用户描述年龄一致或不写）")

    # 预算：描述含预算语境时，取值中全部数字须见于描述（「约3000元」≡3000）
    desc_has_budget = re.search(r"预算|\d+(?:\.\d+)?\s*(?:元|块)", desc) is not None
    if desc_has_budget:
        for r in profile_rows:
            if "预算" not in r["field"]:
                continue
            v = r["value"].strip()
            if not v or v in _HEDGE_VALUES:
                continue
            for num in _numbers(v):
                if not _contains_number(desc, num):
                    add("自检画像数值归一",
                        f"预算字段「{r['field']}」取值「{v}」中的数值 {num} 未见于用户描述"
                        f"（预算须取用户描述原文数值，如「约3000元」≡3000）")

    def keyword_check(pat, rule, label):
        # 否定表述豁免 + 词元交集（品类/品牌关键词检查统一口径；
        # 「品牌」模式同样覆盖 品牌偏好/品牌倾向 等偏好类字段）。
        for r in profile_rows:
            if not re.search(pat, r["field"]):
                continue
            v = r["value"].strip()
            if not v or v in _HEDGE_VALUES:
                continue
            tokens = []
            for atom in (a.strip() for a in re.split(r"[；;,、]", v)):
                if not atom or atom in _HEDGE_VALUES:
                    continue
                # ① 否定表述豁免：否定短语不是品牌/品类实体（「无特别偏好」≠品牌名）；
                #    分项含拉丁品牌词（如「排除 NovaPod」）时仍核对。
                if _NEGATION_PREFIX_RE.match(atom) and not re.search(r"[A-Za-z]", atom):
                    continue
                # ② 词元交集：非通用词元并入词元池
                for t in _keyword_tokens(atom):
                    if t not in tokens:
                        tokens.append(t)
            if not tokens:
                continue  # 词元全为通用词/否定表述：无实体可核对，跳过（不算缺陷）
            if not any(t.casefold() in desc.casefold() for t in tokens):
                add(rule, f"画像{label}「{r['field']}」取值「{v}」非通用词元 "
                          f"{'、'.join(tokens)} 均未见于用户描述"
                          f"（词元交集比对：≥1 个词元见于原文即通过，全部不在才计缺陷；"
                          f"不得写用户未提及的品牌/品类）")

    # 品类 / 品牌：关键词包含
    keyword_check(r"品类", "自检品类关键词", "品类")
    keyword_check(r"品牌", "自检品牌关键词", "品牌")

    # 「现有主要设备」通用禁入：不得含旧设备转折语之后提及的品牌名。
    # 否定表述豁免（「不是 NovaPod」是否定而非现用品牌）按分项套用；
    # 禁入是"不得出现"方向，词元交集（须在场才比对）不适用于本条。
    dev_v = get_value(_DEVICE_FIELD_PAT).strip()
    if dev_v and dev_v not in _HEDGE_VALUES:
        for brand in _old_device_brands(desc):
            cf = brand.casefold()
            flagged = any(cf in atom.casefold()
                          for atom in (a.strip() for a in re.split(r"[；;,、]", dev_v))
                          if atom and not _NEGATION_PREFIX_RE.match(atom))
            if flagged:
                add("自检设备旧品牌禁入",
                    f"「现有主要设备」含用户已换掉的旧品牌「{brand}」"
                    f"（用户描述中旧设备转折语之后提及的品牌不得再写为现有设备），取值「{dev_v}」")
    return out


def _coverage_atoms(prods, shelf):
    """P1 官方维度（产品识别覆盖货架全集）中可执行的自检 atoms：
    仅当货架参照在场（shelf 非空）时断言——漏报产品 / 多报产品 / 表头合并。"""
    out = []
    if not shelf:
        return out
    for pid in sorted(set(shelf) - set(prods)):
        out.append(Finding("自检产品覆盖", "P1", 0,
                           f"产品表缺少货架产品 {pid}（产品表须覆盖货架全集）"))
    for pid in sorted(set(prods) - set(shelf)):
        out.append(Finding("自检产品覆盖", "P1", 0,
                           f"产品表出现货架之外的产品 {pid}（不得多报）"))
    for pid in sorted(prods):
        if prods[pid].get("n_ids_in_header", 1) > 1:
            out.append(Finding("自检产品覆盖", "P1", 0,
                               f"{pid} 表头合并了多个产品ID（每款产品须独立成节）"))
    return out


def findings_to_atoms(issues):
    """Finding 列表 → failed_atoms（含 deduct=0 的缺陷项：如 推荐ID不存在/超预算）。
    按扣分降序排（与 judge issues 同序），保证重试注入清单顺序稳定。"""
    atoms = []
    for f in sorted(issues, key=lambda x: -x.deduct):
        atoms.append({"doc": _DIM_DOC.get(f.dimension, "输出目录"),
                      "rule": f.rule, "message": f.message})
    return atoms


def run(output_dir, input_dir):
    """出厂质检门：结构层 + 无网事实层（迁移规则 + 泛化检查），不联网、不拉模型。

    返回 {"passed": bool, "score_hint": 0-100, "failed_atoms": [{doc,rule,message},…]}。
    passed = failed_atoms 为空；score_hint = judge 确定性总分（0-100）。
    """
    c = _core(output_dir, input_dir, None)
    issues = list(c["issues"])
    issues.extend(_generalized_fact_atoms(c["profile_rows"], c["desc"]))
    issues.extend(_coverage_atoms(c["prods"], c["shelf"]))
    atoms = findings_to_atoms(issues)
    # 去重（同一缺陷只出一条修正指令）
    seen, uniq = set(), []
    for a in atoms:
        key = (a["doc"], a["rule"], a["message"])
        if key not in seen:
            seen.add(key)
            uniq.append(a)
    return {"passed": not uniq, "score_hint": c["total"], "failed_atoms": uniq}


def format_failure_notice(atoms):
    """重试注入的固定格式失败清单（advisor_pipeline 尾部追加用）。"""
    lines = ["上一轮产出未通过以下质量检查，本次输出必须逐项修正且不得复现："]
    for i, a in enumerate(atoms, 1):
        lines.append(f"{i}) {a['rule']}：{a['message']}（出现在 {a['doc']}）")
    lines.append("除修正上述问题外，其余内容与上一轮保持一致。")
    return "\n".join(lines)


# ---------------------------------------------------------------- selftest

def _selftest():
    """每条硬规则至少一个 0/1 断言（合成微型样例，不依赖外部文件）。"""
    mk = lambda g, f, v: {"group": g, "field": f, "value": v}

    # 规则3：缺 5 个字段组 → 各 -15；齐全 → 0
    fs = rule3_profile_groups([mk("标识", "画像编号", "U1"), mk("基本情况", "性别", "男")])
    assert len(fs) == 5 and all(f.dimension == "U4" and f.deduct == 15 for f in fs), fs
    assert not rule3_profile_groups([mk(g, "x", "y") for g in PROFILE_GROUPS])

    # 规则8：当前售价用优惠前价 → 扣分；用展示价 → 通过
    prods = {"P009": {"rows": [mk("购买与使用成本", "当前售价", "展示价1000元；另测800元")]}}
    shelf = {"P009": {"brand": "示例品牌", "model": "型号甲", "price": 800.0,
                      "orig_price": 1000.0, "sold": "x"}}
    fs = rule8_price_from_shelf(prods, shelf)
    assert any(f.rule == "规则8禁用优惠前价" for f in fs), fs
    prods["P009"]["rows"][0]["value"] = "800"
    assert not rule8_price_from_shelf(prods, shelf)

    # 规则7：2 个残留单元格 → -6；程序日志短语再 -10
    fs = rule7_residue([("a", "无主动降噪（多来源取值并列保留，未取平均）"), ("b", "该维…")], "P3", "t")
    assert len(fs) == 1 and fs[0].deduct == 6.0, fs
    fs = rule7_residue([("a", "（模型抽取缺失，程序按来源行补录）")], "P3", "t")
    assert len(fs) == 2 and sum(f.deduct for f in fs) == 13.0, fs

    # 规则10：R1 官方阶梯 100/67/34/0 + 预算容差
    prods = {f"P00{i}": {"rows": [mk("购买与使用成本", "当前售价", v)]}
             for i, v in [(1, "900"), (2, "2500"), (3, "1200"), (4, "1600")]}
    recs = lambda *pids: {"recs": [{"level": lv, "pid": p, "reason": "", "note": ""}
                                   for lv, p in zip(REC_LEVELS, pids)]}
    _, ladder, val = rule10_rec_ids_budget(recs("P001", "P002", "P003"), prods, 1500)
    assert ladder == 67 and val == {"P001": True, "P002": False, "P003": True}, (ladder, val)
    _, ladder, _ = rule10_rec_ids_budget(recs("P002", "P001", "P003"), prods, 1500)
    assert ladder == 0            # 首选超预算 → R1=0
    _, ladder, _ = rule10_rec_ids_budget(recs("P001", "P003", "P002"), prods, 1500)
    assert ladder == 67
    _, ladder, _ = rule10_rec_ids_budget(recs("P001", "P002", "P002"), prods, 1500)
    assert ladder == 34
    _, _, val = rule10_rec_ids_budget(recs("P004"), prods, 1500)
    assert val["P004"] is True    # 1600 ≤ 1500×1.1
    _, _, val = rule10_rec_ids_budget(recs("P002"), prods, 1500)
    assert val["P002"] is False   # 2500 > 1650

    # 规则5：行数异常 + 空结论
    fs = rule5_rec_shape({"recs": [{"level": "首选"}], "conclusion": ""})
    assert len(fs) == 2

    # 规则4：同组字段名集合不一致 → -5；组缺失 → -10
    prods2 = {"P001": {"rows": [mk(g, "f", "v") for g in PRODUCT_GROUPS]},
              "P002": {"rows": [mk("基础标识", "f", "v"), mk("产品力", "另一字段", "v"),
                                mk("品牌力", "f", "v"), mk("市场验证", "f", "v"),
                                mk("购买与使用成本", "f", "v")]}}
    fs = rule4_product_groups_consistency(prods2)
    assert any("结构不一致" in f.message and f.deduct == 5 for f in fs), fs

    # 规则6 逻辑：未选 = 全集 - 已选
    assert {"P002", "P003"} - {"P002"} == {"P003"}

    # P2：重量跨源污染（1200g）
    fs = p2_shelf_facts({"P009": {"rows": [mk("产品力", "重量", "1200g")]}},
                        {"P009": {"brand": "示例品牌", "model": "型号甲", "price": 800.0,
                                  "orig_price": 1000.0, "sold": ""}})
    assert any(f.dimension == "P2" and "重量" in f.message for f in fs), fs

    # P4：促销话术当事实 -15；放「渠道宣传（不作为事实）」字段 → 不扣
    fs = p4_promo_as_fact({"P009": {"rows": [mk("品牌力", "品牌名气口碑", "商家信誉太好了")]}})
    assert any(f.dimension == "P4" and f.deduct == 15 for f in fs), fs
    assert not p4_promo_as_fact({"P009": {"rows": [mk("市场验证", "渠道宣传（不作为事实）", "全球销量第一")]}})

    # P6：字段名覆盖（names-only，防值内「评分」误判）；样本纪律
    prods5 = {"P003": {"rows": [mk("市场验证", "共性好评点", "佩戴舒适稳固"),
                                mk("市场验证", "反馈样本与代表性说明", "未提供")]}}
    fs = p6_market_feedback(prods5, {"P003": 0})
    assert any(f.rule == "P6样本纪律" for f in fs) and any(f.rule == "P6字段覆盖" for f in fs), fs
    prods5["P003"]["rows"][1]["value"] = "（样本不足，不构成共性）"
    assert not [f for f in p6_market_feedback(prods5, {"P003": 0}) if f.rule == "P6样本纪律"]

    # 规则10「无」行：三行全「无」+ 错配声明在场 → 无缺陷、R1 不计测（None）
    abst = {"recs": [{"level": lv, "pid": "无", "reason": "r", "note": "n"} for lv in REC_LEVELS]}
    decl = "候选池与需求品类形态错配，当前资料无法支持直接购买建议，不产生有效推荐。"
    fs, ladder, val = rule10_rec_ids_budget(abst, prods, 1500, rec_text=decl)
    assert fs == [] and ladder is None and val == {}, (fs, ladder, val)
    # 三行全「无」且无声明 → 缺陷 + R1=0
    fs, ladder, val = rule10_rec_ids_budget(abst, prods, 1500, rec_text="推荐如下。")
    assert any(f.rule == "规则10拒推声明" for f in fs) and ladder == 0.0, (fs, ladder)
    # 混合：首选真实有效 + 备选「无」→ 无行不计为不存在，阶梯按真实行记 34
    mixed = {"recs": [{"level": "首选", "pid": "P001", "reason": "", "note": ""},
                      {"level": "备选1", "pid": "无", "reason": "", "note": ""},
                      {"level": "备选2", "pid": "无", "reason": "", "note": ""}]}
    fs, ladder, val = rule10_rec_ids_budget(mixed, prods, 1500, rec_text="")
    assert not [f for f in fs if "不存在" in f.message] and ladder == 34 \
        and val == {"P001": True}, (fs, ladder, val)

    # 权重重归一：未计测维度剔除权重（None≠0）
    assert weighted_avg([(None, 55), (None, 20), (100.0, 15), (100.0, 10)]) == 100.0
    assert weighted_avg([(67.0, 55), (100.0, 20), (100.0, 15), (100.0, 10)]) == \
        round((67.0 * 55 + 100.0 * 45) / 100.0, 1)
    assert weighted_avg([(None, 55)]) is None

    # 泛化检查：旧设备转折语后品牌 → 禁入；换成了之后（现用品牌）→ 不禁入
    brands = _old_device_brands("之前一直用 NovaPod 手机，那是一套自成体系的封闭系统；"
                                "最近换成了安卓阵营的星澜。")
    assert brands == ["NovaPod"], brands
    fs = _generalized_fact_atoms(
        [mk("设备", "现有主要设备", "星澜"), mk("设备", "生态备注", "此前使用 NovaPod 手机，已更换")],
        "之前一直用 NovaPod 手机，最近换成了安卓阵营的星澜。")
    assert fs == [], fs  # 生态备注引用旧品牌属诚实标注，不禁入
    fs = _generalized_fact_atoms([mk("设备", "现有主要设备", "NovaPod 手机；星澜")],
                                 "之前一直用 NovaPod，最近换成了星澜")
    assert any(f.rule == "自检设备旧品牌禁入" and "NovaPod" in f.message for f in fs), fs

    # 泛化检查：数值归一（30岁≡30、约3000元≡3000）+ 品类/品牌关键词包含
    rows = [mk("基本情况", "年龄", "30岁"), mk("预算", "预算上限", "约3000元"),
            mk("购买目标", "商品品类", "耳机")]
    assert not _generalized_fact_atoms(rows, "30岁的人预算约3000元想买耳机")
    fs = _generalized_fact_atoms(rows, "35岁，预算约3000元，想买耳机")
    assert any(f.rule == "自检画像数值归一" and "年龄" in f.message for f in fs), fs
    fs = _generalized_fact_atoms([mk("购买目标", "商品品类", "智能音箱")], "想买耳机")
    assert any(f.rule == "自检品类关键词" for f in fs), fs

    # 词元交集 + 否定表述豁免回归锁（防自检误伤诚实标注的两处）
    rows_tok = [mk("购买目标", "商品品类", "便携蓝牙音箱（户外款）"),
                mk("偏好", "品牌偏好", "无特别偏好")]
    desc_tok = ("他需要一个便携的蓝牙音箱，户外露营时用；预算500-800元，"
                "对品牌没有特别偏好，但对产品专业性要求高。")
    assert not _generalized_fact_atoms(rows_tok, desc_tok)          # 误报清零
    # 全词元都不在原文 → 不放过真缺陷（智能音箱 vs 只提"耳机"的原文）
    fs = _generalized_fact_atoms([mk("购买目标", "商品品类", "智能音箱")], "想买耳机")
    assert any(f.rule == "自检品类关键词" and "智能" in f.message for f in fs), fs
    # 否定开头但分项含具体拉丁品牌词 → 照常核对
    fs = _generalized_fact_atoms([mk("偏好", "品牌偏好", "无偏好，排除 NovaPod")], "想买耳机")
    assert any(f.rule == "自检品牌关键词" and "NovaPod" in f.message for f in fs), fs
    # 设备禁入套用否定豁免：「不是 NovaPod」不禁入；非否定分项含旧品牌仍禁入
    fs = _generalized_fact_atoms([mk("设备", "现有主要设备", "不是 NovaPod，是星澜")],
                                 "之前一直用 NovaPod，最近换成了星澜")
    assert fs == [], fs
    fs = _generalized_fact_atoms([mk("设备", "现有主要设备", "NovaPod 手机；不是 NovaPod 的款不考虑")],
                                 "之前一直用 NovaPod，最近换成了星澜")
    assert any(f.rule == "自检设备旧品牌禁入" for f in fs), fs

    # 覆盖 atoms：仅货架在场时断言；「无」行/缺参照不误报
    assert not _coverage_atoms({}, {})
    fs = _coverage_atoms({"P002": {"n_ids_in_header": 1}}, {"P001": {}, "P002": {}})
    assert [f.message for f in fs] == ["产品表缺少货架产品 P001（产品表须覆盖货架全集）"], fs
    fs = _coverage_atoms({"P001": {"n_ids_in_header": 2}}, {"P001": {}})
    assert any("表头合并" in f.message for f in fs), fs

    # 注入清单固定格式
    notice = format_failure_notice([{"doc": "user_profile.md", "rule": "R", "message": "M"}])
    assert notice.startswith("上一轮产出未通过以下质量检查，本次输出必须逐项修正且不得复现：\n")
    assert "1) R：M（出现在 user_profile.md）" in notice
    assert notice.endswith("除修正上述问题外，其余内容与上一轮保持一致。")

    print("selftest: 全部断言通过（规则1-8/10、P2/P4/P6 专项、"
          "「无」行异常路径、权重重归一、泛化禁入/数值归一/覆盖 atoms、注入清单格式、"
          "词元交集/否定豁免回归锁）")
