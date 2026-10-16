# tests/fixtures/eval/ — 本地评估工具（tools/evaluate.py）夹具

- `valid/input/`：mini 输入目录（2 款产品 P001/P002，五源齐全，编号两位/三位混用：
  报道/实测用 P01/P02，官网/货架用 P001/P002）。产品全集 = {P001, P002}。
- `valid/output/`：合法三份文档（手工构造数据、按 src/renderer 契约模板渲染生成）。
  recommendation.md 由真实决策链生成（parse 回读 → constraint_engine → selector →
  render_recommendation）：画像含游泳场景 → P002 水下硬约束证据不足 → 有效集
  E={P001}，仅 P001 入选，备选1/备选2 为 SPEC §9 异常路径空位行，P002 进未选原因表。
  本目录必须**恰好** 3 份文件（E1）。评估器以 constraint_engine 独立重建有效集，
  并校验「推荐 ∈ 有效集」。
- `invalid/<case>/{input,output}`：以 valid 输出为基线的确定性变坏案，各自带同一份
  mini input（自包含）；期望失败检查项见下表。输出 md 为 field_catalog/renderer
  契约的静态快照；若契约、渲染模板或决策/校验语义变更，需同步重新生成本目录
  （生成方式：对 valid/output 的前两份文档执行 renderer.parse_* →
  constraint_engine.constraint_matrix / valid_product_ids →
  selector.select_recommendation → renderer.render_recommendation）。

## 失分场景类型 10 案（策略回归锁）

全集统一跑法 = `tests/regression/test_eval_set.py`（逐案断言退出码 1 + 期望检查项
被点名；CLI 子进程全量实跑；集合清单双向盘点；确定性双跑一致）。

| case | 期望点名 | 失分场景 |
|---|---|---|
| degrade_empty_product_table | E10、E8、X2 | 降级空产品表（product_list 只剩标题） |
| recommendation_outside_valid_set | E5 | 推荐∉有效集 E（预算收紧→P001 出 E 仍被推荐；R1 硬约束纪律） |
| brand_model_renamed | E3 | 品牌型号改写（「品牌型号原样沿用」纪律） |
| broken_table_row_parse | E10、E8 | 表格坏行（行多一列→文档不可解析） |
| citation_out_of_bounds | E8 | 引用越界（推荐理由引全集外产品/不存在字段） |
| card_notes_empty | E7 | 推荐卡注意事项留空（官方要求不得留空） |
| scene_priority_enum_violation | OBJ | 优先级判定依据枚举违规（scene_priority 契约轴枚举面） |
| exclusion_gap | E6、X3 | 未选原因漏覆盖（E6 逐款覆盖 + X3 3+其余全集口径） |
| missing_output_file | E1（X3 skipped） | 产物文件数量不符 |
| bom_encoding_e9 | E9、E10 | UTF-8 BOM（平台要求无 BOM/`\n`） |

## 防线/降级路径事故形态 3 案

| case | 期望点名 | 缺陷形态 |
|---|---|---|
| badcase_degrade_comment_e10 | E10、E8 | 降级诊断 HTML 注释混入 user_profile.md 顶部→结构崩（注释格式/落点=src/pipeline.py degrade 分支的禁入形态原样） |
| badcase_scene_priority_empty_draft | E10、E5、X2、X3 | scene_priority/priority_basis 校验矛盾→修复环用尽→不死鸟防线空草稿（产物形态=agent/agent.py `_fallback_templates` 原样） |
| badcase_officialsite_fields_dropped | E4 | 官网字段整行删除（契约行只可空「未提供」不可删，删行=E4 表结构不一致；锁定该开关轴的结构不变量） |

覆盖面说明：14 项检查（E1..E10/OBJ/X1..X3）中除 X1（仅输入侧可触发，输出侧
坏法不涉及）外 13 项均有对应用例。
