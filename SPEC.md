# SPEC.md — 智能选购顾问 Agent 开发规格

> 本文件是本项目的**唯一权威规格**，由比赛官方规则（`raw/competition-problemData.txt`）+ 外部专家咨询意见 + 项目决策共同构成。与官方规则冲突时，以官方规则原文为准并登记到 `reports/blockers.md`。
> 阶段目标：**第一阶段（至 2026-09-25）交付可在 mock 模型下端到端跑通的基线**；真实模型联调与提交属第二阶段。

---

## 1. 任务定义

输入：一个输入目录（如 `/home/user/ws/input`），含一位用户自然语言描述 + 一整套（示例 5 款、正式评测可能更多）同品类商品资料，分布在 5 类信息源（见 §5）。

输出：输出目录下恰好 3 份 Markdown——

| 文件 | 内容 |
|---|---|
| `user_profile.md` | 用户结构化画像表：字段组/字段名/取值 三列表格，覆盖 7 个字段组（标识/基本情况/购买目标/预算/设备/场景/偏好） |
| `product_list.md` | 资料包内**全部**产品逐款成节（节标题=产品编号+名称），每节一张五行组属性表（基础标识/产品力/品牌力/市场验证/购买与使用成本），**所有产品表格结构完全一致** |
| `recommendation.md` | 「推荐结果」表（恰好 3 行：首选/备选1/备选2）+ 一句综合结论 + 「未选原因」表（覆盖全部未入选产品） |

依赖关系：**recommendation 只能基于前两份已生成文档的内容推导**（pipeline 必须真实地"渲染→重新解析→再决策"，不得绕过）。

内容三原则（官方原文）：
1. 一切取值来自源数据，可回溯，不得引入资料外信息、不得间接外推；
2. 源数据没有但值得考虑的方向可写，必须标注「待核验」；
3. 只写结论不写过程。

## 2. 官方硬约束（不可违反）

- 运行环境：Linux Debian 12 x86_64，仅 Python 3.12 / Node 22 / JDK17 / Go1.22；本项目用 **Python 3.12**。
- 打包：`agent/` 目录 ZIP ≤100MB；入口 `agent.py`（根目录）+ `agent.json`（`{"runtime":"python","version":"x.y.z"}`）+ `requirements.txt`；**所有依赖打进包**（线上无网络安装）。
- 入口协议：`--version` 输出与 agent.json 一致的版本号、退出码 0；`--prompt "自然语言指令"`（含输入/输出路径，需自行解析）；成功退出码 0。
- 环境变量（只能从 env 读）：`DASHSCOPE_API_KEY`、`DASHSCOPE_BASE_URL`（以 `/api/v1` 结尾）、`OPENAI_BASE_URL`（以 `/v1` 结尾）、`AGENT_LOG_DIR`（日志写其下 `agent.log`）。
- 模型白名单（**仅这 15 个文本模型**，评测时其他模型调用必失败）：
  `qwen3.5-plus, qwen3.6-35b-a3b, qwen3.6-flash, qwen3.6-plus, qwen3.7-max, qwen3.7-plus, qwen3.8-max, qwen-plus, deepseek-v4-flash, deepseek-v4-flash-0731, deepseek-v4-pro, glm-5.1, glm-5.2, kimi-k2.5, kimi-k2.6, MiniMax-M2.5`
  （白名单中另有语音/图像/视频类模型，与本任务无关，禁止调用。）
- 调用方式：仅 DashScope API / OpenAI 兼容 Chat API；**不支持** MCP、模型内置工具、知识库、Embedding、Responses API、文件上传。
- 网络：仅 `*.aliyuncs.com`（模型服务）；禁止外部检索。
- 资源：单次运行 ≤1800s（内部软截止 1680s），内存 ≤4GB；需正确处理限流。
- 本地开发依赖纪律：**只允许标准库 + requests**（便于后续 vendor 打包）。

## 3. 架构

固定主链 + 按风险触发的有限复核。**不做**自由讨论式多 Agent。

```
输入解析/来源登记/产品清单
  ├─ 用户事实与需求原子化 ─────────┐
  └─ 五源产品事实抽取 → 归并/冲突 ─┤
                                  ▼
                     覆盖检查、定点复核（有限）
                                  ▼
                  冻结画像表、产品属性表（渲染为 md）
                                  ▼
                  从渲染后的 md 重新解析（parse 回对象）
                                  ▼
                  硬约束判定 → 排序与组合选择 → 推荐报告
                                  ▼
                  跨文档校验、格式校验、有限修复
                                  ▼
                       恰好输出三份 Markdown
```

隔离铁律：**决策/推荐模块不得接收原始资料，也不得复用含原始资料的模型会话**。发现字段缺口→向上游登记缺口→上游重查→更新前两份文档→重新冻结→重新决策。禁止推荐模块自己从原文补事实。

## 4. 数据契约（src/schemas.py）

```text
SourceRecord: source_id, source_type(enum五源), original_text, spans(稳定片段编号)
EvidenceRef:  source_id, span_id, exact_quote
FactCell:     field_key, raw_value, normalized_value, unit, fact_kind(官网声明|实测观察|用户反馈|宣传表述|有依据归纳),
              status(有支持|附条件|存在冲突|输入缺失), conditions, applicable_variant, as_of, evidence_refs
UserNeed:     need_id, original_expression, need_type(硬约束|软偏好|目标|歧义), operator, expected_value, priority, evidence_refs
ProductRecord:canonical_id, original_ids(P01与P001等别名), brand_raw, model_raw, fields: dict[str, FactCell]
ConstraintResult: product_id, need_id, result(PASS|FAIL|UNKNOWN|NOT_APPLICABLE), profile_reference, attribute_reference, explanation
DecisionPlan: selected_products, recommendation_cards, excluded_products, overall_conclusion
OutputBundle: user_profile_md, product_list_md, recommendation_md
```

事实类型（kind）与事实状态（status）必须分开：「官网写续航10h」是有支持的**官网声明**，不得自动升级为实测值。

## 5. 输入与产品归一（确定性代码，不用模型）

输入目录结构（示例见 `raw/dataset_sample/Data_for_Users/`）：

| 路径 | 说明 |
|---|---|
| `00_User_Descriptions/User_Description_{1..n}.txt` | 用户画像唯一信息来源 |
| `01_Ecommerce_Listings/Ecommerce_Listing_Snapshot.csv` | 货架快照（三位编号） |
| `02_Media_Coverage_and_Reviews/P{2位}_Product_Coverage.txt` | 媒体报道（两位编号） |
| `02_Media_Coverage_and_Reviews/P{2位}_Third_Party_Review.txt` | 第三方实测（两位编号） |
| `03_User_Feedback_and_Complaints/User_Feedback_Excerpts.csv` | 用户评价/投诉摘录 |
| `04_Brand_Official_Sites/P{3位}_<型号>_Official_Site.txt` | 官网参数（三位编号） |

归一规则：
- 仅对**明确的产品编号字段/文件名编号**做归一（`P01`≡`P001`，尾号对齐），保留全部原始写法为别名；
- 品牌与型号为脱敏名，**原样沿用**，不做模糊匹配、不删 `Pro/Max/代际` 后缀、不还原真实品牌；
- 产品全集 = 官网目录 ∪ 货架 ∪ 报道 ∪ 实测；「同编号不同型号」记为身份冲突，不静默合并；
- 反向覆盖检查：每产品恰好一节；每个未推荐产品恰好一行未选原因。

## 6. 画像提取规则

- **原子化**：一句话含「地铁通勤/每天/单次40分钟/常接电话/不想频繁充电」→ 5 条可独立核验记录，禁止概括成"重度通勤用户"。
- **硬约束准入**：每个硬约束须有原文证据 + 约束类型 + 可执行判定；「大概800左右」不得直接变成硬上限；「尽量轻」不得虚构为「≤5克」。
- **场景优先级**：原文明示排序 > 原文主次措辞（主要/其次/偶尔）> 标「优先级未明确」。时长/频率缺失写「未提供」，不常识补全。
- **偏好只收原文明确表达**：不推测收入、审美、价格敏感度、技术熟悉度、未表达的生态依赖。

## 7. 五源归并与冲突处理

按字段保留限定：价格/销量（渠道/快照时间/SKU/优惠条件）、官网参数（声明属性/版本/"最高"限定）、实测（条件/设备/模式/样本）、评价（原文/摘录范围/独立性）、报道（时间/是否转述品牌）。

冲突三步法：①判真冲突（不同模式/时间/版本≠冲突）→ ②可解释则**并列保留条件**（不同模式两组续航，禁止取平均）→ ③无法解释保留冲突并标「待核验」。禁止模型直觉择一。

时效：以**输入资料内的时间基准**判断，不得用运行当天日期。

## 8. 市场反馈综合（P6 维度）

表达层级：单条（"有一条反馈提及…"）→ 多条独立（"摘录中多条反馈提及…"）→ 样本可确认（说明范围内样本数）→ 代表性未知（明示不能推及整体）。禁止：转载重复当多人、无投诉≠投诉率低、销量高≠满意度高、正评数≠好评率。共性好评/槽点必须能回溯反馈原文；条目不足支撑「共性」时如实标注样本不足。

## 9. 决策引擎

- 约束四态：`PASS`（证据支持符合）/`FAIL`/`UNKNOWN`（缺失/冲突/条件未确认）/`NOT_APPLICABLE`（有据证明不适用）。**UNKNOWN≠PASS**；「无证据证明不兼容」≠「兼容」；「可能拿到优惠」≠「预算内」。
- 有效集 E = 对所有适用硬约束均 PASS 的产品；三款全部从 E 选取。
- UNKNOWN 处理顺序：查抽取遗漏→查跨源补齐→关键字段一次定点复核→仍无法确认保持 UNKNOWN。
- **有效产品不足 3 款**：先排抽取遗漏；不放宽硬约束、不把未知写成符合、不虚构第三款。有首选则保留首选，其余位置明确「当前尚不构成有效备选」+原因；无首选则明确资料无法支持直接购买建议。这是异常路径，不是常态策略。
- 排序：全部硬约束 PASS → 匹配高优先级明确需求 → 可比属性取舍 → 证据缺口/冲突影响 → 组合差异化。禁止输出无口径的"综合匹配度93.7"类数字；内部数值排序可存在但不得写入报告、须可还原为具体属性×需求。
- 备选不预设"性能/性价比/品牌款"分类；两备选差异有限时如实说明。
- 每张推荐卡固定字段：身份（层级/编号/品牌/型号）、适配结论（命中哪些画像需求）、事实依据（对应前两份文档具体字段，引用格式 `画像｜场景｜通勤`、`产品属性｜P001｜价格`）、取舍（相对其他有效候选得/失）、代价与风险（不得编造缺点；资料无据时写证据边界）、购买前核验（聚焦真实剩余不确定性，不得机械填"兼容性待核验"）、改选条件（哪个明确条件变化应改选）。
- 未选原因五类：硬约束不符/硬约束证据不足/软需求匹配弱/同条件下被压过/无足够新增价值。条件变化句必须与排除原因对应；多个否决条件不得只解决其一就宣称可考虑；禁止预测"以后可能降价"类无据变化。
- 推荐结果表后必须有一句综合结论（不同偏好取向如何取舍）。

## 10. 渲染与校验（确定性代码）

模型只输出结构化对象 → 校验 → **程序模板渲染 Markdown**（标题/字段顺序/空值标记/节数/表结构不依赖模型）。统一 `field_catalog`：同字段集、同顺序、同表头、同缺失表达（统一用「待核验」/「未提供」）。扩展字段仅在确认不影响解析时加，且对所有产品统一加。

渲染器同时提供 parse 函数（profile_md→对象、products_md→对象），供决策阶段真实回读。

确定性校验清单（validators，全部程序判定）：
1. 恰好 3 份文档，命名精确；2. 产品全集=输入全集，无遗漏/重复/拆并错误；3. 品牌型号原样；4. 所有产品表结构一致；5. 推荐=3 个不同产品且∈E；6. 未选表覆盖其余全集（可合并同因行）；7. 推荐卡风险/核验字段非空；8. 引用存在且指向前两份文档内字段（不越界引原文）；9. 退出码/运行时间/文件编码；10. 表格可被 Markdown 解析器解析（列数一致、无坏行）。

## 11. 模型网关

- 模式：real（`DASHSCOPE_API_KEY` 存在且非 `mock`）+ `QW_FORCE_MOCK=1` 强制 mock；mock 必须确定性（从 JSON fixture 按 key 取响应）。
- real：仅 `requests` 调 `{OPENAI_BASE_URL}/chat/completions`（openai 兼容）或 DashScope 原生；模型必须∈白名单；超时/重试：429 与部分 5xx 有限重试 ≤2（指数退避+抖动）；鉴权失败/模型未授权/预算耗尽→停止该路径并报告（不盲目重试）；上下文超限→缩小分块；模型不可用→仅切换到白名单内预验证模型。
- 并发初始 2；Token 预算控制 + 请求速率控制分开实现；总截止时间检查（软截止 1680s，超时触发降级）。
- 降级顺序：停润色 → 停非关键复核 → 保全覆盖/硬约束/关键事实/结构 → 确定性模板输出 → 未知如实保留。**禁止用"丢部分产品"降级**。
- 安全：输入资料是数据不是指令（防提示注入：资料中出现的"忽略前文推荐X"类文本一律当作被分析内容）；密钥不进日志/文档/代码。

## 12. 评估（本地 proxy，非官方）

- 第一类 确定性验收：上节 10 项 + 资源限制，全部程序判定（tools/evaluate.py，结构失败退出码非 0）。
- 第二类 真值事实评估：对示例数据构造部分真值夹具（第一阶段仅覆盖结构层面，真实质量待第二阶段真实模型）。
- 第三类 专家代理评估：第二阶段再做。
- 发布门槛（内部）：确定性测试全过；可满足用例三款全有效；关键事实精确率≥97%/召回≥95%（二阶段起测）；不越资源限制。

## 13. 目录与接口

```
contracts/   field_catalog.json platform_contract.json rules.json interfaces.md
src/         schemas.py input_adapter.py source_index.py product_registry.py
             model_gateway.py budget_manager.py profile_extractor.py product_extractor.py
             reconciler.py renderer.py validators.py constraint_engine.py selector.py
             report_writer.py pipeline.py
prompts/     profile_extraction.md product_extraction.md
tests/       unit/ regression/ fixtures/
tools/       evaluate.py e2e_mock.py package.py(二阶段)
agent/       agent.py agent.json requirements.txt   ← 提交包（由 src 组装/引用）
reports/     progress.md blockers.md e2e_sample_output/
```

模块签名以 `contracts/interfaces.md` 为准（契约阶段产出）。核心逻辑入口：`run(input_dir, output_dir, config) -> OutputBundle`，一次调用完成、不请求补充信息、不访问外部商品信息。

## 14. 第一阶段验收标准（本次工作流）

1. `python -m compileall src` 通过；
2. `python -m pytest tests -q` 全通过；
3. `python agent/agent.py --version` 输出 `0.1.0`、退出码 0；
4. `python tools/e2e_mock.py`：以 mock 网关在 `raw/dataset_sample/Data_for_Users` 上端到端跑通，产出 3 份 md 且通过全部确定性校验，退出码 0；
5. 独立审计无 high 级未修复问题。

不在第一阶段范围：真实模型调用、真实平台沙箱运行、ZIP 打包、榜单提交、性能压测、真值质量评估。
