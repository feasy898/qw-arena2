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
