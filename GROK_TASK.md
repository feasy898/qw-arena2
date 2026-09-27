# 任务：确定性基线抽取器（零模型依赖）

你在千问AI Arena参赛Agent项目工作区（当前目录 D:\workspace\qw-arena2）。请通读 AGENTS.md（开发纪律）与 SPEC.md（§4 数据契约、§5 输入结构）后完成本任务。

## 背景

平台评测环境下模型网关调用失败（原因未定位），当前降级路径输出"未提供"空表，内容得分极低。需要一个**纯确定性（规则/正则，零模型调用）**的抽取器作为降级基线：能从输入资料中确凿提取的字段就填真值，提取不到的留空（渲染层自然落「未提供」），绝不编造。

## 交付物（只允许创建/修改以下文件）

1. **`src/deterministic_extractor.py`**（新建，唯一新模块）：
   - `deterministic_profile(user_text: str) -> dict`：从用户描述原文保守提取——预算数字（正则：数字+元/块，含区间/约/以内语义标注 budget_raw 与数值）、设备与生态（手机/电脑品牌词匹配原文提及）、场景关键词（通勤/办公/运动/游泳/游戏等，仅在原文出现时列出场景名，时长频率不推断）、偏好（仅原文明确词）。返回与 `src/profile_extractor.py` 产出同构的 profile dict（键对齐 `extract_profile_bundle` 的返回 profile 部分；不产生 needs 也行，渲染层可空）。**只提取有原文依据的**。
   - `deterministic_product_draft(record, source_records_for_product) -> dict`：从该产品的五源记录提取——①货架 CSV 行（source_type 为 ECOMMERCE_LISTING 的记录，original_text 含列名）：品牌/型号/颜色套餐/展示价/优惠前价/已售量/商品标题/平台店铺类型直接映射到 fields 的对应键（FactCell 形态：raw_value 原文、evidence_refs 指向该 CSV 行、fact_kind="官网声明"不对——用"有依据的归纳"或按 contracts/rules.json 允许的类型，价格类标"实测观察"也不对——查看 src/schemas.py 的 fact_kind 枚举选合适的，证据 refs 必须逐字可回溯）；②官网 txt：【品牌概况】块→brand_origin_market/brand_category_focus；【型号】块→model/protection_rating/waterproof_conditions 等以"键：值"行式出现的字段（正则 `(.{2,12})[:：]\s*(.+)` 白名单匹配已知字段关键词：防水/续航/重量/电池/充电/降噪/蓝牙/存储/防护/保修）。返回与 `src/product_extractor.py` 的 `empty_draft(record)` 同构（填充字段版）——先读 empty_draft 实现确保键集一致。
   - 铁律：evidence_refs 的 exact_quote 必须是 original_text 的逐字子串；提取不到就保持 empty_draft 的空形态；不猜数值单位；品牌型号原样。
2. **`tests/unit/test_deterministic_extractor.py`**（新建）：以 `raw/dataset_sample/Data_for_Users` 为夹具，断言至少：P001 的品牌=Zurmek、展示价含 456、已售量含 2000+；P005 官网品牌概况字段非空；用户描述1的预算上限数值正确、场景含"通勤"；所有 evidence_refs 逐字回溯成立（写一个通用校验函数遍历断言）。

## 边界（严格遵守）

- **不要修改**以下文件：agent/ 下一切、src/model_gateway.py、src/pipeline.py、src/profile_extractor.py、src/product_extractor.py、config.example.json、contracts/、prompts/、任何既有测试。集成由组织者完成。
- 模块只依赖标准库 + src.schemas / src.input_adapter 的既有类型。
- 完成后运行：`python -m compileall -q src` 与 `python -m pytest tests/unit/test_deterministic_extractor.py -q`，全过后再跑 `python -m pytest tests -q`（390+5 既有测试必须全过，不许动既有测试）。
- 在 reports/progress.md 追加一行登记（只追加，不改既有行）。

完成后输出：修改文件清单、测试运行结果摘要、对示例数据的提取覆盖率粗估（产品字段填充比例）。
