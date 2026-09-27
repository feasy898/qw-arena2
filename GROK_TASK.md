# 任务（第五弹）：推荐环节的确定性增强

你在千问AI Arena参赛项目工作区。当前降级链（网关失败时）产出的 recommendation.md 是"无产品入选"的空位行——因为决策引擎消费的是全空字段。现在确定性抽取已填充 ~52% 产品字段与 ~54% 画像字段，但降级链的决策环节没有利用它们。

## 任务
1. **调研**（只读）：读 `src/pipeline.py` 的降级链如何进入决策（`_freeze_and_render` → `parse_profile/parse_products` → `constraint_matrix` → `selector`）；读 `src/constraint_engine.py` 的四态判定需要哪些字段（预算上限/价格、品类等）；读 `src/selector.py` 的入选与未选原因生成对字段的要求。
2. **增强 `src/deterministic_extractor.py`**：确保降级链的确定性产出能驱动决策引擎给出**非空的推荐结果**：
   - 画像的 budget_max/currency 已有；产品侧确保 current_price（展示价）有 normalized_value（数值）——决策的价格硬约束需要数值可比；
   - 若决策仍因关键字段空而全 FAIL/UNKNOWN：在 deterministic_product_draft 中把 listing 的展示价也写入 normalized_value（当前可能只有 raw_value）；品牌/型号照旧。
   - 目标：降级链跑示例数据时，recommendation.md 能给出真实的三款推荐（基于价格预算匹配等确凿维度），未选原因落到具体字段；不虚构任何属性。
3. **测试**：新增子进程级测试——构造网关不可达环境（env DASHSCOPE_API_KEY=sk-invalid + OPENAI_BASE_URL=https://10.255.255.1.invalid/v1）跑 agent，断言 recommendation.md 的推荐结果表 3 行均为真实产品ID（P00x）且理由引用具体字段；pytest 全量过（当前 411）。
4. reports/progress.md 登记。

## 边界
只改 src/deterministic_extractor.py、tests/unit/test_deterministic_extractor.py（或新建 tests/unit/test_degrade_decision.py）；不改 pipeline/constraint_engine/selector/gateway；证据纪律不变。完成后输出：修改文件、测试结果、降级链 recommendation.md 的推荐三款与理由摘要。
