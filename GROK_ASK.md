# 任务（第6弹）：产品侧官网字段补齐——默认/型式/核心功能/音频格式/佩戴条件

背景：平台实测证实了"补字段=直接加分"（画像闭集补齐后 6~7 → 11 分）。外部盲评指出：两套产物的产品表**一起缺失**官网 txt 里明明有的字段：品类（如"骨传导游泳耳机"）、代际（如"2024款 / Ralun Pro" / "Sorbik Pro第5代"）、核心功能（如"Vondir 2.0+骨传导；游泳EQ"）、音频格式（MP3/FLAC/WAV/AAC）、佩戴/形态条件（"颈后式开放佩戴"）。这些在 04_Brand_Official_Sites/P***.txt 的【型号】块和【品牌概况】块里。

## 任务（只改 src/deterministic_extractor.py 与 tests/unit/test_deterministic_extractor.py）

1. 读 raw/dataset_sample/Data_for_Users/04_Brand_Official_Sites/ 全部 5 份官网文件，摸清【品牌概况】块和【型号】块的真实格式（键值行样式、键名写法）。
2. **品类/形态**：从官网块提取产品类别（如"骨传导游泳耳机"）→ category 键；
3. **代际**：提取代际表述（如"2024款"、"Sorbik Pro第5代"、"Kandro 2 Pro / 2024款"）→ generation 键（原文子串）；
4. **核心功能**：提取核心功能行（分号分隔的多功能列表）→ core_functions（数组，逐条原文）；
5. **音频格式**：提取支持格式列表（MP3/FLAC/WAV/AAC/Larvo 等）→ audio_formats（数组）；
6. **佩戴方式与结构**：提取佩戴相关（"颈后式开放佩戴"、游泳耳塞等）→ wearing_design；【品牌概况】块的"归属市场/品类积累" → brand_origin_market / brand_category_focus（grok 早期已实现部分——只补缺口，勿重复）。
7. **冲突并列**：P001 官网 vs 第三方实测的蓝牙水下能力（官网"5.4 可在水下2米串流" vs 实测"水下只能MP3"）——若两侧都抽到，蓝牙水下可用性字段应**并列两条观察**并标"存在冲突/待核验"（按 rules.json 冲突三步法：并列保留条件，不强行择一）。

## 边界
exact_quote 必须逐字回溯；提取不到保持空；不改其他文件；测试：对 5 款产品逐款断言（P001 品类=骨传导游泳耳机、代际含"2024"、格式含 MP3 等——以官网原文为准）；pytest 全量必须过（当前 419）。

完成后输出：修改点、5 款产品各补齐字段数、本地降级产物中这些字段的实际渲染行（从 user_profile/product_list 抽 3 行示例）。
