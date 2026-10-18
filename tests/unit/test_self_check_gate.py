# -*- coding: utf-8 -*-
"""出厂质检闭环单元测试（不拉真模型）：

- src.self_check.run 的 atoms/passed/score_hint 接口与注入清单固定格式；
- 关键词检查纪律：词元交集 + 否定表述豁免（防自检误伤诚实标注）；
- advisor_pipeline.run() 的质检闭环：通过 → 镜像返回 0；不过 → 恰好 1 次重试
  （仅主选模型、预算门限、失败清单注入）；重试仍不过 → 交付历史最优版本 +
  返回 0 + stderr/agent.log 记「自检未全过已交付最优版」+ 失败清单（三份齐全
  永不 raise 回退降级链）；质检门不可用 → fail-open；
- 采样参数直传（temperature 0.05 / seed 42 / top_p 0.9）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src import advisor_pipeline as advisor  # noqa: E402
from src import self_check as sc  # noqa: E402


# ---------------------------------------------------------------- 可过检三份产物（共享 fixture）

def three_docs_passing() -> str:
    """能通过 src.self_check.run 结构层+无网事实层的最小三份产物（模型输出原文格式）。"""
    profile = (
        "=====FILE: user_profile.md=====\n"
        "# 用户画像\n\n"
        "| 字段组 | 字段名 | 取值 |\n"
        "| --- | --- | --- |\n"
        "| 标识 | 画像编号 | 1 |\n"
        "| 基本情况 | 称谓 | 用户一 |\n"
        "| 基本情况 | 年龄 | 未提供 |\n"
        "| 基本情况 | 所在城市/地区 | 未提供 |\n"
        "| 购买目标 | 商品品类 | 未提供 |\n"
        "| 购买目标 | 购买目的 | 未提供 |\n"
        "| 预算 | 预算上限 | 2000元 |\n"
        "| 设备 | 现有主要设备 | 未提供 |\n"
        "| 场景 | 使用场景 | 未提供 |\n"
        "| 偏好 | 品牌倾向 | 未提供 |\n"
    )
    products = ["=====FILE: product_list.md=====\n# 产品属性列表\n"]
    for pid, name, price in [("P001", "产品一", "1098"), ("P002", "产品二", "1138"),
                             ("P003", "产品三", "1498"), ("P004", "产品四", "456"),
                             ("P005", "产品五", "2948")]:
        products.append(
            f"\n## {pid} {name}\n\n"
            "| 字段组 | 字段名 | 取值 |\n"
            "| --- | --- | --- |\n"
            f"| 基础标识 | 产品编号 | {pid} |\n"
            f"| 基础标识 | 产品名称 | {name} |\n"
            "| 产品力 | 降噪能力 | 待核验 |\n"
            "| 产品力 | 续航 | 待核验 |\n"
            "| 品牌力 | 品牌名气口碑 | 待核验 |\n"
            "| 市场验证 | 市场热度（销量） | 未提供 |\n"
            "| 市场验证 | 用户综合评分 | 未提供 |\n"
            "| 市场验证 | 共性好评点 | 未提供 |\n"
            "| 市场验证 | 共性槽点 | 未提供 |\n"
            f"| 购买与使用成本 | 当前售价 | {price}元 |\n"
        )
    recommendation = (
        "=====FILE: recommendation.md=====\n"
        "# 消费推荐报告\n\n"
        "## 推荐结果\n\n"
        "| 推荐层级 | 产品ID | 产品 | 画像匹配与推荐理由 | 注意事项 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 首选 | P001 | 产品一 | 待核验 | 待核验 |\n"
        "| 备选1 | P002 | 产品二 | 待核验 | 待核验 |\n"
        "| 备选2 | P003 | 产品三 | 待核验 | 待核验 |\n\n"
        "## 未选原因\n\n"
        "| 产品ID | 未进入前三的主要原因 | 建议改变条件 |\n"
        "| --- | --- | --- |\n"
        "| P004、P005 | 待核验 | 待核验 |\n\n"
        "## 综合结论\n\n"
        "三款入选产品关键口径均待核验，建议按画像优先级核验后决策。\n"
    )
    return profile + "".join(products) + recommendation


def _docs_missing_scene_group(profile_no_value: str) -> str:
    """可过检产物去掉「场景」字段组（恰 1 个质检 atom），并用称谓值标记版本。"""
    text = three_docs_passing()
    text = text.replace("| 场景 | 使用场景 | 未提供 |\n", "")
    return text.replace("| 基本情况 | 称谓 | 用户一 |",
                        f"| 基本情况 | 称谓 | {profile_no_value} |")


_GATE_FAILING_DOCS = ("=====FILE: user_profile.md=====\n# A\n"
                      "=====FILE: product_list.md=====\n# B\n"
                      "=====FILE: recommendation.md=====\n# C\n")


def _make_input(tmp_path: Path) -> Path:
    input_dir = tmp_path / "input"
    (input_dir / "00_User_Descriptions").mkdir(parents=True)
    (input_dir / "00_User_Descriptions" / "User_Description_1.txt").write_text(
        "用户一", encoding="utf-8")
    return input_dir


def _del_log_env(monkeypatch) -> None:
    monkeypatch.delenv("AGENT_LOG_DIR", raising=False)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)  # run() 只在 _read_env 校验存在性


# ---------------------------------------------------------------- self_check.run 接口

def test_self_check_run_passes_on_good_docs(tmp_path):
    output_dir = tmp_path / "output"
    advisor.write_outputs(str(output_dir), advisor.parse_three_docs(three_docs_passing()),
                          profile_no="1")
    res = sc.run(str(output_dir), str(_make_input(tmp_path)))
    assert res["passed"] is True
    assert res["failed_atoms"] == []
    assert 0 <= res["score_hint"] <= 100


def test_self_check_run_atom_shape_and_doc_mapping(tmp_path):
    output_dir = tmp_path / "output"
    advisor.write_outputs(str(output_dir),
                          advisor.parse_three_docs(_GATE_FAILING_DOCS), profile_no=None)
    res = sc.run(str(output_dir), str(_make_input(tmp_path)))
    assert res["passed"] is False and res["failed_atoms"]
    for a in res["failed_atoms"]:
        assert set(a) == {"doc", "rule", "message"}
        assert a["doc"] in ("user_profile.md", "product_list.md", "recommendation.md", "输出目录")
    rules = {a["rule"] for a in res["failed_atoms"]}
    assert "规则3画像字段组" in rules       # 缺 7 组 → U4 → doc=user_profile.md
    assert "规则5推荐表" in rules           # R4 → doc=recommendation.md
    assert all(a["doc"] == "user_profile.md"
               for a in res["failed_atoms"] if a["rule"] == "规则3画像字段组")


def test_self_check_budget_missing_no_crash(tmp_path):
    """画像无任何含数字的预算行时，规则10不做超预算判定（无依据不指控），
    且不得因 None 参与格式化而崩溃——崩溃会被 fail-open 当通过，整道质检门静默失效。"""
    docs = three_docs_passing().replace("| 预算 | 预算上限 | 2000元 |\n",
                                        "| 预算 | 预算上限 | 未提供 |\n")
    output_dir = tmp_path / "output"
    advisor.write_outputs(str(output_dir), advisor.parse_three_docs(docs), profile_no="1")
    res = sc.run(str(output_dir), str(_make_input(tmp_path)))
    assert res["passed"] is True
    assert all(a["rule"] != "规则10超预算" for a in res["failed_atoms"])


def test_self_check_budget_line_order_independent(tmp_path):
    """预算行选取与行序无关：预算下限行在前时仍以预算上限为准——
    1845 元对下限 1500×1.1=1650 超、对上限 2000×1.1=2200 合法。"""
    docs = three_docs_passing().replace(
        "| 预算 | 预算上限 | 2000元 |\n",
        "| 预算 | 预算下限 | 1500元 |\n| 预算 | 预算上限 | 2000元 |\n")
    docs = docs.replace("| 购买与使用成本 | 当前售价 | 1498元 |",
                        "| 购买与使用成本 | 当前售价 | 1845元 |")
    output_dir = tmp_path / "output"
    advisor.write_outputs(str(output_dir), advisor.parse_three_docs(docs), profile_no="1")
    res = sc.run(str(output_dir), str(_make_input(tmp_path)))
    assert res["passed"] is True
    assert all(a["rule"] != "规则10超预算" for a in res["failed_atoms"])


def test_parse_recommendation_anchors_on_exclusion_heading(tmp_path):
    """推荐表与未选原因表的切分锚定「未选原因」标题行：
    需求判断正文提前提及该词时推荐表不错位；无标题时取最后一次出现。"""
    rec_md = tmp_path / "recommendation.md"
    rec_md.write_text(
        "# 消费推荐报告\n\n"
        "需求判断：预算内可凑齐三款，未选原因见文末说明。\n\n"
        "## 推荐结果\n\n"
        "| 推荐层级 | 产品ID | 产品 | 画像匹配与推荐理由 | 注意事项 |\n"
        "| --- | --- | --- | --- | --- |\n"
        "| 首选 | P001 | 产品一 | 待核验 | 待核验 |\n"
        "| 备选1 | P002 | 产品二 | 待核验 | 待核验 |\n"
        "| 备选2 | P003 | 产品三 | 待核验 | 待核验 |\n\n"
        "## 未选原因\n\n"
        "| 产品ID | 未进入前三的主要原因 | 建议改变条件 |\n"
        "| --- | --- | --- |\n"
        "| P004 | 待核验 | 待核验 |\n\n"
        "## 综合结论\n\n三款入选产品建议按画像优先级核验后决策。\n",
        encoding="utf-8")
    parsed = sc.parse_recommendation(str(rec_md))
    assert [r["pid"] for r in parsed["recs"]] == ["P001", "P002", "P003"]
    assert [r["pid"] for r in parsed["unselected"]] == ["P004"]


def test_self_check_generalized_device_and_numbers():
    # 数值归一：28岁≡28、2000元左右≡2000；关键词包含：品类
    rows = [{"group": "基本情况", "field": "年龄", "value": "28岁"},
            {"group": "预算", "field": "预算上限", "value": "2000元左右"},
            {"group": "购买目标", "field": "商品品类", "value": "耳机"}]
    assert sc._generalized_fact_atoms(rows, "28岁，预算2000元左右，想买耳机") == []
    fs = sc._generalized_fact_atoms(rows, "35岁，预算2000元左右，想买耳机")
    assert len(fs) == 1 and fs[0].rule == "自检画像数值归一" and "年龄" in fs[0].message
    # Velmora 泛化：旧设备转折语后品牌禁入「现有主要设备」；「换成了」后为现用品牌不禁入
    dev_bad = [{"group": "设备", "field": "现有主要设备", "value": "Velmora 手机；禾岚"}]
    desc = "之前一直用 Velmora 手机，最近换成了安卓阵营的禾岚。"
    fs = sc._generalized_fact_atoms(dev_bad, desc)
    assert any(f.rule == "自检设备旧品牌禁入" and "Velmora" in f.message for f in fs)
    dev_good = [{"group": "设备", "field": "现有主要设备", "value": "禾岚"},
                {"group": "设备", "field": "生态备注", "value": "此前使用 Velmora 手机，已更换"}]
    assert sc._generalized_fact_atoms(dev_good, desc) == []  # 现用品牌保留；备注引用旧品牌属诚实标注


def test_self_check_keyword_token_intersection_and_negation():
    """关键词检查的两处防误报纪律：
    ① 品类词元交集——「水下听歌耳机（游泳耳机）」原文「能在水下听歌的耳机」→
       ≥1 非通用词元在原文即 PASS；全词元都不在原文（「骨传导耳机」vs「想买耳机」）
       → 仍计缺陷，不放过真问题；
    ② 品牌否定表述豁免——「无特别偏好」原文「对品牌没有特别偏好」→ 直接 PASS；
       否定开头但分项含具体拉丁品牌词 → 照常核对。"""
    rows = [{"group": "购买目标", "field": "商品品类", "value": "水下听歌耳机（游泳耳机）"},
            {"group": "偏好", "field": "品牌偏好", "value": "无特别偏好"}]
    desc = ("他需要一个能在水下听歌的耳机，游泳时听歌；预算1500-2000元，"
            "对品牌没有特别偏好，但对产品专业性要求高。")
    assert sc._generalized_fact_atoms(rows, desc) == []
    # ① 全词元都不在原文 → 仍 FAIL
    fs = sc._generalized_fact_atoms(
        [{"group": "购买目标", "field": "商品品类", "value": "骨传导耳机"}], "想买耳机")
    assert any(f.rule == "自检品类关键词" and "骨传" in f.message for f in fs), fs
    # ② 否定开头但分项含拉丁品牌词 → 仍核对
    fs = sc._generalized_fact_atoms(
        [{"group": "偏好", "field": "品牌偏好", "value": "无偏好，排除 Velmora"}], "想买耳机")
    assert any(f.rule == "自检品牌关键词" and "Velmora" in f.message for f in fs), fs


def test_self_check_device_ban_negation_exempt():
    """设备旧品牌禁入套用否定表述豁免——「不是 X」是排除而非现用
    品牌，不禁入；非否定分项含旧品牌仍禁入。"""
    desc = "之前一直用 Velmora，最近换成了禾岚"
    fs = sc._generalized_fact_atoms(
        [{"group": "设备", "field": "现有主要设备", "value": "不是 Velmora，是禾岚"}], desc)
    assert fs == [], fs
    fs = sc._generalized_fact_atoms(
        [{"group": "设备", "field": "现有主要设备",
          "value": "Velmora 手机；不是 Velmora 的款不考虑"}], desc)
    assert any(f.rule == "自检设备旧品牌禁入" for f in fs), fs


def test_self_check_run_score_hint_matches_judge_total(tmp_path):
    output_dir = tmp_path / "output"
    input_dir = _make_input(tmp_path)
    advisor.write_outputs(str(output_dir),
                          advisor.parse_three_docs(_GATE_FAILING_DOCS), profile_no=None)
    res = sc.run(str(output_dir), str(input_dir))
    assert res["score_hint"] == sc.judge(str(output_dir), str(input_dir))["total"]


# ---------------------------------------------------------------- run() 质检闭环

def test_run_gate_pass_first_try(tmp_path, monkeypatch):
    """质检通过 → 恰 1 次模型调用，镜像被调、返回 0（fallback 不触发）。"""
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    _del_log_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls: list[tuple[str, str]] = []

    def fake_chat(key, base, model, user_msg, **kwargs):
        calls.append((model, user_msg))
        return three_docs_passing(), None

    monkeypatch.setattr(advisor, "_chat", fake_chat)
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: calls.append(("mirror", "")))
    rc = advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    assert rc == 0
    assert calls[0][0] == advisor.GEN_MODEL
    assert ("mirror", "") in calls  # 通过质检后才镜像


def test_run_gate_retry_once_with_gen_model_and_injection(tmp_path, monkeypatch):
    """首轮质检不过 → 恰好 1 次重试：仅 gen_model（fallback 不许重跑）、prompt 尾部
    注入固定格式失败清单；重试通过 → 返回 0。"""
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    _del_log_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(advisor, "_advisor_models", lambda: ("model-x", ("model-y",)))
    calls: list[tuple[str, str]] = []

    def fake_chat(key, base, model, user_msg, **kwargs):
        calls.append((model, user_msg))
        if len(calls) == 1:
            return _GATE_FAILING_DOCS, None  # 首轮质检不过
        return three_docs_passing(), None    # 重试通过

    monkeypatch.setattr(advisor, "_chat", fake_chat)
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: None)
    rc = advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    assert rc == 0
    assert [m for m, _ in calls] == ["model-x", "model-x"]  # 恰 2 次调用，且重试=gen_model；model-y 未用
    first_msg, retry_msg = calls[0][1], calls[1][1]
    assert retry_msg.startswith(first_msg)                  # 原 user_msg + 尾部追加
    assert "上一轮产出未通过以下质量检查，本次输出必须逐项修正且不得复现：" in retry_msg
    assert "除修正上述问题外，其余内容与上一轮保持一致。" in retry_msg
    assert "规则5推荐表：推荐表层级/行数异常: []（应恰为 首选/备选1/备选2）（出现在 recommendation.md）" in retry_msg
    assert "规则3画像字段组：画像缺少字段组「标识」（出现在 user_profile.md）" in retry_msg
    assert "\n1) " in retry_msg and "\n2) " in retry_msg    # 编号清单


def test_run_gate_retry_still_fails_keeps_best_and_returns_zero(tmp_path, monkeypatch, capsys):
    """重试仍不过 → 交付 failed_atoms 最少的历史最优版本并返回 0（三份文档
    齐全时永不 raise、永不回退降级链），stderr 记「自检未全过已交付
    最优版」+ 失败清单。首轮恰 1 atom（更优），重试更差（9 atoms）——历史最优是
    首轮版本，落盘的必须是它而非最后一轮。"""
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    _del_log_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setattr(advisor, "_advisor_models", lambda: ("model-x", ("model-y", "model-z")))
    calls: list[str] = []

    def fake_chat(key, base, model, user_msg, **kwargs):
        calls.append(model)
        if len(calls) == 1:
            return _docs_missing_scene_group("第一版称谓"), None  # 恰 1 atom（历史最优）
        return _GATE_FAILING_DOCS, None                          # 9 atoms（更差）

    monkeypatch.setattr(advisor, "_chat", fake_chat)
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: None)
    rc = advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    assert rc == 0                                    # 不 raise、不回退降级链
    # 恰 2 次调用（首轮 + 1 次重试），fallback 序列未因质检不过而轮换
    assert calls == ["model-x", "model-x"]
    # 历史最优（atoms 最少=第一版）在盘，最后一轮的更差版本未落盘
    profile_text = (output_dir / "user_profile.md").read_text(encoding="utf-8")
    assert "第一版称谓" in profile_text
    assert "# A" not in profile_text
    # stderr/日志含「自检未全过已交付最优版」+ 失败清单
    err = capsys.readouterr().err
    assert "自检未全过已交付最优版" in err
    assert "失败清单 1) " in err
    assert "规则3画像字段组" in err                    # 清单含具体失败规则（场景组缺失）


def test_run_gate_retry_skipped_when_budget_gate_blocks(tmp_path, monkeypatch, capsys):
    """预算门限不允许 → 零重试，交付历史最优版本并返回 0。"""
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    _del_log_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    calls: list[str] = []

    def fake_chat(key, base, model, user_msg, **kwargs):
        calls.append(model)
        return _GATE_FAILING_DOCS, None

    monkeypatch.setattr(advisor, "_chat", fake_chat)
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: None)
    monkeypatch.setattr(advisor, "_retry_allowed", lambda *a, **k: False)
    rc = advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    assert rc == 0
    assert calls == [advisor.GEN_MODEL]  # 预算门限：零重试
    assert (output_dir / "user_profile.md").is_file()  # 历史最优版已交付写盘
    err = capsys.readouterr().err
    assert "自检未全过已交付最优版" in err and "失败清单 1) " in err


def test_run_gate_delivered_best_logs_failure_list_to_agent_log(tmp_path, monkeypatch):
    """质检未全过交付最优版时，AGENT_LOG_DIR/agent.log 记
    「自检未全过已交付最优版」汇总行（含失败清单）。"""
    import logging
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    log_dir = tmp_path / "logs"
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setenv("AGENT_LOG_DIR", str(log_dir))
    monkeypatch.setattr(advisor, "_advisor_models", lambda: ("model-x", ()))
    monkeypatch.setattr(advisor, "_retry_allowed", lambda *a, **k: False)
    monkeypatch.setattr(advisor, "_chat", lambda *a, **k: (_GATE_FAILING_DOCS, None))
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: None)
    try:
        rc = advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    finally:
        target = str(log_dir / "agent.log")
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, "baseFilename", None) == target:
                root.removeHandler(handler)
                handler.close()
    assert rc == 0
    text = (log_dir / "agent.log").read_text(encoding="utf-8")
    assert "自检未全过已交付最优版" in text and "失败清单=[" in text
    assert "规则5推荐表" in text


def test_retry_allowed_budget_formula():
    # remaining − 上次调用×1.2 ≥ RETRY_FINISH_RESERVE_S（90s；锚定 timekeeper
    # 全局剩余，重试路径历史最优版已落盘，只需预留收尾开销）
    assert advisor._retry_allowed(1000.0, 0.0) is True
    assert advisor._retry_allowed(1000.0, 700.0) is True    # 1000−840=160 ≥ 90
    assert advisor._retry_allowed(90.0, 0.0) is True        # 边界 90 ≥ 90
    assert advisor._retry_allowed(89.9, 0.0) is False
    assert advisor._retry_allowed(150.0, 50.0) is True      # 150−60=90 ≥ 90
    assert advisor._retry_allowed(149.9, 50.0) is False     # 149.9−60=89.9 < 90
    assert advisor._retry_allowed(500.0, 400.0) is False    # 500−480=20 < 90


def test_run_aborts_when_global_budget_exhausted(tmp_path, monkeypatch):
    """死线锚定 timekeeper 全局时钟：全局预算不足「降级链预留+单次调用下限」
    时，一次调用都不发起，抛错移交回退链（0.7.6 起不再只看本地 DEADLINE_S）。"""
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    _del_log_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    from src import timekeeper
    timekeeper.init(total_s=120.0)  # < FALLBACK_RESERVE_S(150)+MIN_ATTEMPT_BUDGET_S(120)
    called: list[str] = []
    monkeypatch.setattr(advisor, "_chat",
                        lambda *a, **k: called.append("x") or ("", None))
    with pytest.raises(RuntimeError, match="死线临近|全部候选模型失败"):
        advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    assert not called


def test_run_gate_fail_open_when_self_check_unavailable(tmp_path, monkeypatch):
    """质检门不可用（模块缺失）→ fail-open：按通过处理，镜像返回 0。"""
    input_dir = _make_input(tmp_path)
    output_dir = tmp_path / "output"
    _del_log_env(monkeypatch)
    monkeypatch.setenv("DASHSCOPE_API_KEY", "test-key")
    monkeypatch.setitem(sys.modules, "src.self_check", None)  # import 触发 ImportError
    mirrored: list[bool] = []
    monkeypatch.setattr(advisor, "_chat",
                        lambda *a, **k: (_GATE_FAILING_DOCS, None))
    monkeypatch.setattr(advisor, "_mirror", lambda *a, **k: mirrored.append(True))
    rc = advisor.run(f"请根据 {input_dir} 中的数据完成任务，输出到 {output_dir}")
    assert rc == 0 and mirrored == [True]


def test_self_check_wrapper_tolerates_gate_crash(tmp_path, monkeypatch):
    """质检自身异常 → _self_check 返回 None（fail-open），不向主链抛错。"""
    def _boom(*a, **k):
        raise ValueError("质检内部错误")
    monkeypatch.setattr(sc, "run", _boom)
    assert advisor._self_check(str(tmp_path), str(tmp_path)) is None


# ---------------------------------------------------------------- 采样参数直传

def test_chat_request_body_sampling_params(monkeypatch):
    """请求体：temperature=0.05、seed=42、top_p=0.9（OpenAI 兼容字段直传）。"""
    import types
    captured: dict = {}

    class _Resp:
        status_code = 200

        def json(self):
            return {"choices": [{"message": {"content": "x"}}]}

    def _post(url, headers=None, json=None, timeout=None):
        captured.update(json)
        return _Resp()

    monkeypatch.setitem(sys.modules, "requests", types.SimpleNamespace(post=_post))
    content, usage = advisor._chat("k", "http://example/v1", "model-x", "u")
    assert content == "x" and usage is None
    assert captured["temperature"] == 0.05
    assert captured["seed"] == 42
    assert captured["top_p"] == 0.9
    assert "max_tokens" not in captured  # 不发送（显式限制会致思考耗尽、空 content）
