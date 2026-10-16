#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deterministic_judge.py — 千问 AI Arena「机器评测（公开排名）」确定性评分器（两层）。

评分细则依据（原文直读，非猜测）：
  raw/competition-problemData.txt 「四、具体评测维度参考 → 1. 机器评测（公开排名）」
  （同一文本亦见 qw-arena2/SPEC.md §9）

  机器评测总分 = 三份产出物得分算术平均（U/P/R 三环节等权），满分 100。
  环节一 user_profile.md：U1 55% / U2 20% / U3 15% / U4 10%
  环节二 product_list.md：P1 10% / P2 25% / P3 20% / P4 20% / P5 10% / P6 15%
  环节三 recommendation.md：R1 55% / R2 20% / R3 15% / R4 10%

纯规则、无 LLM、无网络。每条规则 = 独立纯函数，0/1 判定 + 扣分，可单元测试
（python3 deterministic_judge.py --selftest 内置断言）。

单一事实源：全部共享纯规则函数（结构层）与 judge 组装内核在 src/self_check.py；
本文件保留 CLI（argparse/JSON 输出格式不变）、re-export 全部公开名，并独有
「样例金标准回归锁」（事实层，规则9/U2）——开发期专用，经 src.self_check.judge
的 golden 参数注入。该层只对官方公开样例数据集的用户 1 生效，属开发侧回归资产，
不随提交包分发（打包清单不含 tools/），出厂质检门 src.self_check.run 不调用它。

两层结构：
  A) 结构层（默认开启，跨用户安全）：规则1 命名 / 规则2 表格可解析 / 规则3 画像 7 组 /
     规则4 产品 5 组+结构一致 / 规则5 推荐 3 行+结论 / 规则6 未选覆盖 / 规则7 残留 /
     规则8 售价取展示价 / 规则10 推荐 ID∈产品表+预算×1.1（含「无」行与错配声明豁免）
  B) 事实层（用户 1 回归锁，本文件独有）：规则9 画像事实逐字交叉核对 + U2 偏好金标准；
     启用条件：--user-desc 显式指定，或自动定位到的用户描述命中用户 1 金标准锚点
     ≥ 8/9（28岁/男/上海/产品经理/耳机/播客/音乐/2000/禾岚）。

用法：
  python3 deterministic_judge.py --output-dir DIR --input-dir DIR [--user-desc FILE]
                                 [--label NAME] [--json OUT.json] [--selftest]
输出 JSON：{"mode":"full|structure", "scores":{"U":..,"P":..,"R":..}, "total":0-100,
            "issues":[...], "detail":{U1..R4（未计测=null）}, "hard_rules":{规则1..规则10}, ...}
"""

import argparse
import json
import os
import re
import sys

# 单一事实源：全部共享纯规则函数与 judge 组装内核在 src/self_check.py。
# CLI 脚本自举仓库根到 sys.path，保证任意 cwd 可导入。
_HERE = os.path.dirname(os.path.abspath(__file__))
_ROOT = os.path.dirname(_HERE)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

from src.self_check import (  # noqa: E402,F401  re-export 公开 API
    ABSTAIN_PID,
    BUDGET_TOLERANCE,
    MISMATCH_DECL_PATTERNS,
    P6_FIELD_PATTERNS,
    PROCESS_LOG_PATTERNS,
    PRODUCT_GROUPS,
    PROFILE_GROUPS,
    PROMO_PATTERNS,
    REC_LEVELS,
    REQUIRED_FILES,
    RESIDUE_PATTERNS,
    SAMPLE_NOTE_PATTERNS,
    TRUNCATION_MARK,
    WEIGHTS,
    Finding,
    _num,
    findings_to_atoms,
    format_failure_notice,
    p1_score,
    p2_shelf_facts,
    p4_promo_as_fact,
    p6_market_feedback,
    parse_md_table,
    parse_products,
    parse_profile,
    parse_recommendation,
    rule10_rec_ids_budget,
    rule1_file_naming,
    rule2_table_parse,
    rule3_profile_groups,
    rule4_product_groups_consistency,
    rule5_rec_shape,
    rule7_residue,
    rule8_price_from_shelf,
    rule_rec_price_consistency,
    weighted_avg,
)
from src.self_check import judge as _sc_judge        # noqa: E402
from src.self_check import _selftest as _sc_selftest  # noqa: E402


# ---------------------------------------------------------------- 事实层（用户 1 金标准回归锁，开发侧独有）
#
# 设计意图：官方样例数据集公开可得，以其用户 1 的标准答案作回归锁，能在每次改动后
# 立刻发现「画像事实提取退化」。该层只对输入确为用户 1 时生效，不泛化、不随包。

U1_GOLD_ANCHORS = ["28岁", "男", "上海", "产品经理", "耳机", "播客", "音乐", "2000", "禾岚"]
U1_GOLD_ANCHOR_MIN = 8    # 事实层自动启用门槛：描述文本命中用户 1 锚点 ≥ 8/9
                          # （用户 1 原文 9/9；u3=2、u5/u6=3，余量充足）
U1_ATOMS = 14             # U1 计分原子：9 个事实字段 + 5 个场景原子，等权
U1_MISS_DEDUCT = round(100.0 / U1_ATOMS, 2)


def fact_layer_active(desc, explicit=False):
    """事实层（用户 1 回归锁）开关。--user-desc 显式指定 → 启用（操作者自担口径）；
    否则自动定位成功且描述文本命中用户 1 金标准锚点 ≥ U1_GOLD_ANCHOR_MIN 才启用。
    对其他用户的描述一律返回 False（结构层照跑，不拿用户 1 事实比对）。"""
    if explicit:
        return True
    if not desc:
        return False
    return sum(1 for a in U1_GOLD_ANCHORS if a in desc) >= U1_GOLD_ANCHOR_MIN


def rule9_profile_crosscheck(profile_rows, desc):
    """规则9：画像事实与用户描述原文逐字比对（用户 1 金标准口径）。
    U1（9 字段等权 + 5 场景原子）：字段关键值须逐字出现在原文（设备字段为例外硬规则：
    含「禾岚」且不得含已换掉的「Velmora」）。
    U3：冲突——「现有主要设备」含 Velmora；年龄与原文数字不符。"""
    out = []

    def get_value(pat):
        for r in profile_rows:
            if re.search(pat, r["field"]):
                return r["value"]
        return ""

    def verbatim(pat):
        """字段取值的每个原子片段（按 ；;,、 切分，滤掉 未提供/待核验）均逐字见于原文。"""
        v = get_value(pat).strip()
        if not v:
            return False
        atoms = [a.strip() for a in re.split(r"[；;,、]", v)
                 if a.strip() and a.strip() not in ("未提供", "待核验")]
        return bool(atoms) and all(a in desc for a in atoms)

    facts = [
        (r"性别", lambda: "男" in get_value(r"性别"), "性别=男"),
        (r"年龄", lambda: "28岁" in get_value(r"年龄") or ("28" in get_value(r"年龄") and "28岁" in desc),
         "年龄=28岁"),
        (r"职业", lambda: verbatim(r"职业"), "职业=产品经理（逐字）"),
        (r"城市|地区", lambda: verbatim(r"城市|地区"), "城市=上海（逐字）"),
        (r"品类", lambda: verbatim(r"品类"), "品类=耳机（逐字）"),
        # 购买目的按细则 U1「逐字段与标准答案比对」：实质内容（播客+音乐）双侧在场即可，
        # 逐字比对按 ask 规则9 仅要求 设备/年龄/性别/职业。
        (r"购买目的", lambda: ("播客" in get_value(r"购买目的") and "音乐" in get_value(r"购买目的")
                               and "播客" in desc and "音乐" in desc),
         "购买目的（播客+音乐）"),
        (r"预算", lambda: budget_ok(), "预算=2000 元"),
        (r"币种", lambda: verbatim(r"币种"), "币种=元（逐字）"),
        (r"现有主要设备|现有设备|主要设备|^设备$",
         lambda: ("禾岚" in get_value(r"现有主要设备|现有设备|主要设备|^设备$")
                  and "Velmora" not in get_value(r"现有主要设备|现有设备|主要设备|^设备$")),
         "现有主要设备=禾岚（不得含已换掉的 Velmora）"),
    ]

    def budget_ok():
        for r in profile_rows:
            if "预算" in r["field"] and _num(r["value"]) is not None:
                return str(int(_num(r["value"]))) in desc
        return False

    hits = 0
    for pat, fn, name in facts:
        if fn():
            hits += 1
        else:
            out.append(Finding("规则9画像核对", "U1", U1_MISS_DEDUCT,
                               f"画像字段未准确命中：{name}（取值「{get_value(pat) or '缺失'}」）"))
    blob = "".join(r["group"] + r["field"] + r["value"] for r in profile_rows)
    scene_hits = 0
    for name, atom in [("地铁通勤场景", "通勤"), ("时长40分钟", "40分钟"), ("频率每天", "每天"),
                       ("咖啡馆办公", "咖啡馆"), ("周末", "周末")]:
        if atom in desc and atom in blob:
            scene_hits += 1
        elif atom in desc:
            out.append(Finding("规则9场景原子", "U1", U1_MISS_DEDUCT, f"场景原子项未命中：{name}"))
    # U3 冲突
    dev = get_value(r"现有主要设备|现有设备|主要设备")
    if "Velmora" in dev:
        out.append(Finding("规则9设备冲突", "U3", 60,
                           f"「现有主要设备」含用户已明确换掉的品牌 Velmora"
                           f"（原文：之前一直用 Velmora…最近换成了…禾岚），取值「{dev}」"))
    age_v = get_value(r"年龄")
    if age_v and "28" not in age_v:
        out.append(Finding("规则9年龄冲突", "U3", 40, f"年龄「{age_v}」与原文 28岁 冲突"))
    return out, hits, scene_hits


def u2_preference_items(profile_rows):
    """U2 专项：定性偏好逐项命中——「不喜欢太轰头的低音」听感偏好、「和安卓手机配合」兼容偏好，
    各 50 分。"""
    out = []
    blob = " ".join(r["value"] for r in profile_rows if "偏好" in r["group"] or "诉求" in r["group"])
    if not ("轰头" in blob and ("低音" in blob or "音质" in blob)):
        out.append(Finding("U2偏好命中", "U2", 50, "缺「不喜欢太轰头的低音」音质听感偏好"))
    if not ("安卓" in blob and "配合" in blob):
        out.append(Finding("U2偏好命中", "U2", 50, "缺「和安卓手机配合得好」兼容性偏好"))
    return out


class _User1GoldenLayer:
    """src.self_check.judge/_core 的 golden 注入：用户 1 金标准回归锁（开发侧）。"""

    u1_atoms = U1_ATOMS

    @staticmethod
    def active(desc, explicit=False):
        return fact_layer_active(desc, explicit=explicit)

    @staticmethod
    def profile_crosscheck(profile_rows, desc):
        return rule9_profile_crosscheck(profile_rows, desc)

    @staticmethod
    def preference_items(profile_rows):
        return u2_preference_items(profile_rows)


def judge(output_dir, input_dir, user_desc_path=None, label=None):
    """确定性评分器公开入口：共享组装内核 + 本文件的金标准回归锁注入。"""
    return _sc_judge(output_dir, input_dir, user_desc_path, label,
                     golden=_User1GoldenLayer)


# ---------------------------------------------------------------- selftest

def _golden_selftest():
    """事实层（用户 1 回归锁）断言：规则9 / U2 / 两层开关。"""
    mk = lambda g, f, v: {"group": g, "field": f, "value": v}

    # 规则9：设备含 Velmora → U3 -60 且 U1 设备字段不命中
    fs, hits, sc = rule9_profile_crosscheck([mk("设备", "现有主要设备", "Velmora 手机；禾岚")], "换成了禾岚")
    assert any(f.dimension == "U3" and f.deduct == 60 for f in fs) and hits == 0, (fs, hits)

    # 规则9：正常画像 → 9/9 字段 + 5/5 场景，无 U3 冲突
    good = [mk("基本情况", "性别", "男"), mk("基本情况", "年龄", "28岁"),
            mk("基本情况", "职业", "产品经理"), mk("基本情况", "城市", "上海"),
            mk("购买目标", "商品品类", "耳机"), mk("购买目标", "购买目的", "听播客和音乐"),
            mk("预算", "预算上限", "2000元"), mk("预算", "币种", "元"),
            mk("设备", "设备", "禾岚手机"),
            mk("场景", "场景1", "地铁通勤；时长：单程40分钟；频率：每天；优先级：高"),
            mk("场景", "场景2", "咖啡馆办公；频率：周末")]
    desc = "28岁 男 产品经理 上海 耳机 听播客和音乐 2000元 元 禾岚 通勤 40分钟 每天 咖啡馆 周末"
    fs, hits, sc = rule9_profile_crosscheck(good, desc)
    assert hits == 9 and sc == 5 and not [f for f in fs if f.dimension == "U3"], (hits, sc, fs)

    # U2：两项偏好命中 → 0；缺失 → 各 -50
    assert not u2_preference_items([mk("偏好", "音质偏好", "不喜欢太轰头的低音"),
                                    mk("偏好", "兼容性偏好", "和安卓手机配合得好")])
    assert len(u2_preference_items([mk("偏好", "音质偏好", "重视音质")])) == 2

    # 两层开关：用户 1 描述 → 事实层开；其他用户描述/空 → 仅结构层；显式指定 → 强制开
    u1_desc = ("小王，28岁，男，在上海一家互联网公司做产品经理。每天地铁通勤单程40分钟，"
               "想买个耳机听听播客和音乐。预算2000元左右，最近换成了安卓阵营的禾岚。")
    assert fact_layer_active(u1_desc) is True
    assert fact_layer_active("小张，22岁，男，大学生，FPS游戏，头戴式游戏耳机，预算300元左右。") is False
    assert fact_layer_active("赵先生，30岁，男，游泳爱好者，预算1500-2000元，要能在水下听歌的耳机。") is False
    assert fact_layer_active("") is False
    assert fact_layer_active("任意文本", explicit="/x/User_Description_1.txt") is True

    print("golden selftest: 全部断言通过（规则9 画像核对 / U2 偏好 / 两层开关）")


def _selftest():
    """完整自测：共享规则（src.self_check）+ 本文件的金标准回归锁。"""
    _sc_selftest()
    _golden_selftest()


# ---------------------------------------------------------------- main


def main(argv=None):
    ap = argparse.ArgumentParser(description="机器评测确定性评分器（纯规则，无 LLM/无网络）")
    ap.add_argument("--output-dir", help="待评分产物目录（含三份 md）")
    ap.add_argument("--input-dir", help="任务输入目录（含 00_/01_/03_ 子目录）")
    ap.add_argument("--user-desc",
                    help="显式指定用户描述原文文件，强制启用事实层（用户 1 回归锁：金标准口径为"
                         "用户 1，仅应对用户 1 产物回归；不指定时仅当自动定位到用户 1 描述才启用，"
                         "否则只跑结构层）")
    ap.add_argument("--label", help="报告标签")
    ap.add_argument("--json", help="结果 JSON 输出路径")
    ap.add_argument("--selftest", action="store_true", help="运行内置单元断言后继续")
    args = ap.parse_args(argv)

    if args.selftest:
        _selftest()
        if not args.output_dir:
            return 0
    if not args.output_dir or not args.input_dir:
        ap.error("--output-dir 与 --input-dir 必填（或仅用 --selftest）")

    result = judge(args.output_dir, args.input_dir, args.user_desc, args.label)
    js = json.dumps(result, ensure_ascii=False, indent=2)
    if args.json:
        with open(args.json, "w", encoding="utf-8") as f:
            f.write(js + "\n")
    print(js)
    return 0


if __name__ == "__main__":
    sys.exit(main())
