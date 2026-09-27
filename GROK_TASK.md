# 任务（第二弹）：确定性抽取器扩展——媒体报道/第三方实测/用户反馈三源

你在千问AI Arena参赛Agent项目工作区（当前目录）。上一任务你完成的 `src/deterministic_extractor.py` 已集成并实测提分（平台评测 7→8 分）。现在扩展覆盖：当前产品字段覆盖 ~48%（货架 CSV + 官网两源），还有三个源未利用。

## 交付物（只允许修改/新建以下文件）

1. **扩展 `src/deterministic_extractor.py`**（在既有函数内增强，不改既有函数签名）：
   `deterministic_product_draft(record, source_records_for_product)` 增加对以下三源的规则提取（每源按 source_type 区分，evidence 纪律不变：exact_quote 逐字可回溯、提取不到保持空、不编造）：
   - **媒体报道**（`02_Media_Coverage_and_Reviews/P**_Product_Coverage.txt`）：提取渠道报价区间（正则：数字-数字+元/报价/价格…语境词）、卖点宣称句（含"主打/采用/支持/搭载"的句子，fact_kind 用宣传类——查 schemas 的枚举选合法值并登记进 marketing_claims）、测试条件提及（"在…条件下"句式）。
   - **第三方实测**（`P**_Third_Party_Review.txt`）：实测续航（"实测/测得"+时长）、实测降噪/音质描述句（含"实测/测试中"的句子，落到对应产品力字段的附条件观察——conditions 注明"第三方实测"）。
   - **用户反馈 CSV**（`03_User_Feedback_and_Complaints/User_Feedback_Excerpts.csv`，按行记录）：逐行读评价原文列，关键词桶聚合到 common_praise（好/满意/不错/舒服/清晰…）与 common_complaints（差/断/坏/失望/闷/疼…），每条带 evidence_ref 指向该 CSV 行；条数少于 2 条时 feedback_sample_note 注明"样本不足，不构成共性"。
2. **扩展 `tests/unit/test_deterministic_extractor.py`**：对示例数据断言新增覆盖至少各 2 例（如 P002 渠道报价区间非空、某产品实测续航非空、P00x 的 common_praise/complaints 含原文可回溯条目），并保持全部既有测试通过。
3. reports/progress.md 追加一行登记。

## 边界（严格）

- 不改 src/ 其他文件、agent/、config、prompts、contracts、既有测试。
- 事实类型/状态枚举必须用 schemas.py 既有合法值。
- 完成后运行：`python -m compileall -q src`、`python -m pytest tests -q`（405 既有全过）。
- 完成后输出：修改文件、测试结果、产品字段覆盖率变化（对比 48% 基线）。
