# tests/fixtures/eval/ — 本地评估工具（tools/evaluate.py）夹具

- `valid/input/`：mini 输入目录（2 款产品 P001/P002，五源齐全，编号两位/三位混用：
  报道/实测用 P01/P02，官网/货架用 P001/P002）。产品全集 = {P001, P002}。
- `valid/output/`：合法三份文档（手工构造数据、按 src/renderer 契约模板渲染生成）。
  recommendation.md 由真实决策链生成（parse 回读 → constraint_engine → selector →
  render_recommendation）：画像含游泳场景 → P002 水下硬约束证据不足 → 有效集
  E={P001}，仅 P001 入选，备选1/备选2 为 SPEC §9 异常路径空位行，P002 进未选原因表。
  本目录必须**恰好** 3 份文件（E1）。
  （2026-09 审计修复说明：评估器现以 constraint_engine 独立重建 E 并校验「推荐∈E」，
  旧基线推荐 P002 与 E 相违且不可由管线产出，已按本文件约定的再生成方式重生成。）
- `invalid/<case>/{input,output}`：以 valid 输出为基线的三种坏法，各自带同一份 mini
  input（自包含）；期望失败检查项：
  - `missing_product`：product_list.md 漏 P002 节 → E2（X2 同步点名）
  - `bad_table_structure`：P001 表少「编号别名」行 → E4
  - `wrong_recommendation_count`：推荐结果表剩 2 行 → E5
- 注意：输出 md 为 field_catalog/renderer 契约的静态快照；若契约、渲染模板或
  决策/校验语义变更，需同步重新生成本目录（生成方式：对 valid/output 的前两份
  文档执行 renderer.parse_* → constraint_engine.constraint_matrix/valid_product_ids
  → selector.select_recommendation → renderer.render_recommendation）。

## 2026-10-01 扩充批（worker-B：eval 扩 10 题 + 3 条真实 badcase）

新增 13 个 invalid 案（净增 13 ≥ 10；其中 3 条为出分日志/answer_E 提炼的真实
badcase），全部与首批同构：`invalid/<case>/input` = valid mini 输入全拷贝，
`output` = 以 valid 三文档为基线的确定性变坏。全集统一跑法 =
`tests/regression/test_eval_set.py`（逐案断言退出码 1 + 期望检查项被点名；
CLI 子进程全量实跑；集合清单双向盘点；确定性双跑一致）。期望检查项为实跑
观察值（评估器确定性，重复运行结果一致）。

### 失分场景类型 10 案（策略回归锁）

| case | 期望点名 | 失分场景 / 出处 |
|---|---|---|
| degrade_empty_product_table | E10、E8、X2 | 降级空产品表（v0.4.5 前降级链产物形态；submission_log 行13/14 确定性抽取取代空表的失分形态） |
| recommendation_outside_valid_set | E5 | 推荐∉有效集 E（预算收紧→P001 出 E 仍被推荐；审计修复1；R1 硬约束纪律=answer_E 判定的期望增量最大方向） |
| brand_model_renamed | E3 | 品牌型号改写（「品牌型号原样沿用」纪律） |
| broken_table_row_parse | E10、E8 | 表格坏行（行多一列→文档不可解析；平台结构检查轴） |
| citation_out_of_bounds | E8 | 引用越界（推荐理由引全集外产品/不存在字段；answer_E「证据纪律扣分」关切面） |
| card_notes_empty | E7 | 推荐卡注意事项留空（官方要求不得留空） |
| scene_priority_enum_violation | OBJ | 优先级判定依据枚举违规（scene_priority 契约轴枚举面） |
| exclusion_gap | E6、X3 | 未选原因漏覆盖（E6 逐款覆盖 + X3 3+其余全集口径） |
| missing_output_file | E1（X3 skipped） | 产物文件数量不符（submission_log 行6 平台 failed 实录） |
| bom_encoding_e9 | E9、E10 | UTF-8 BOM（平台要求无 BOM/`\n`；windev→GPU 迁移实测编码风险面） |

### 真实 badcase 3 案（出分日志/answer_E 提炼，出处到行）

| case | 期望点名 | badcase 出处（reports/submission_log.md 等） |
|---|---|---|
| badcase_degrade_comment_e10 | E10、E8 | v0.4.11/12 降级诊断 HTML 注释入 user_profile.md 顶部→结构崩：行21（平台 scored=2「注释bug实锤」）、行22（删特性恢复）。注释格式/落点=src/pipeline.py degrade 分支原样 |
| badcase_scene_priority_empty_draft | E10、E5、X2、X3 | v0.5.0 scene_priority/priority_basis 校验矛盾→修复环用尽→phoenix 空草稿：行30（平台 2 分疑似根因）、progress.md:47、v0.5.1 修复后结构 3/6→6/6。产物形态=agent/agent.py `_fallback_templates` 原样 |
| badcase_officialsite_fields_dropped | E4 | 官网五字段开关（GROK_ASK5.md/answer_E 主议题）：行25 v0.4.16 直写官网字段平台 11→1；行30 v0.5.1 停采=「列在格空，非删列」。本地锁定同一开关轴不变量：契约行只可空（未提供）不可删，删行=E4 表结构不一致。注：直写未对齐字面导致平台扣分属 LLM 评分轴，本地评估器不可判；本锁锁的是该开关的结构不变量 |

覆盖面说明：14 项检查（E1..E10/OBJ/X1..X3）中除 X1（仅输入侧可触发，输出侧
坏法不涉及）外 13 项均有对应用例。扩充批只动 tests/fixtures/eval/ 与
tests/regression/test_eval_set.py，未触碰策略源码与 dist。
