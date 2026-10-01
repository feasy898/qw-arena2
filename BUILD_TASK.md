# 构建任务：Schema+Grounding 抽取架构（借开源 instructor/Outlines 思想重构主抽取层）

## 背景与目标

平台评测已实证：画像闭集字段补齐 = +4~5 分（11 分历史最高）；确定性正则覆盖 52-54% 后乏力；flash 模型自由生成中位 2 分（格式漂移+幻觉）。结论：**模型的高覆盖 + 程序的零幻觉保证**才是正解——这正是开源 instructor/outlines 库的核心思想：模型输出必须过程序校验（结构 + 证据锚定），不过就丢弃/重试。

目标：重构主抽取层为「schema 约束抽取 + span grounding 校验」架构，本地量化指标达标（覆盖率>85%、幻觉率=0、quote 通过率 100%），正则层降级为字段级兜底。

## 架构规格（接口定死，不许偏离）

### 新模块 `src/schema_extractor.py`

```python
def extract_profile_grounded(user_text: str, gateway, config) -> tuple[dict, list[str]]:
    """画像抽取（模型+校验）。
    返回 (profile_dict, rejected_fields)；rejected_fields 记录被丢弃的字段名与原因（供日志/评估）。
    """

def extract_product_grounded(record, source_records, gateway, config) -> tuple[dict, list[str]]:
    """单产品抽取（模型+校验）。同上返回。"""

def _validate_with_grounding(model_output: dict, source_text: str,
                             field_schema: dict) -> tuple[dict, list[str]]:
    """核心校验（纯函数，必须独立可测）：
    1. 结构校验：键完整性/类型（复用 src/schemas.py 与 src/profile_extractor.py 既有校验逻辑，不重写）
    2. grounding：每个字段的 exact_quote 必须 source_text 的逐字子串（精确 in 判断，不做模糊匹配）
    3. 校验失败的字段从结果中剔除并记入 rejected；结构整体非法 → 触发一次带错误反馈的重试
    """
```

### 提示词（新文件 `prompts/grounded_extraction.md` 与 `prompts/grounded_product.md`）

- 要求模型输出 JSON：每个字段 `{value, exact_quote}`，exact_quote 必须从输入原文逐字复制
- **禁止在提示词中放任何真实示例值**（历史教训：示例值污染——用明显虚构占位，如"示例称谓X"、"8888"）
- 强调：不确定的字段输出 null，禁止编造 quote
- 失败重试时把校验错误清单（哪个字段、什么原因）附回给模型修正，最多 1 次重试

### 管线改造 `src/pipeline.py`

- 画像/产品抽取主路径改调 schema_extractor（模型可用时）
- **字段级融合**：模型通过校验的字段优先；缺失/被拒字段用 `deterministic_extractor` 的正则结果兜底填充；仍无 → 现有空值路径（"未提供/待核验"）
- 模型路径整体失败（网关不可达等）→ 现有确定性降级链不变
- 修复环：对 rejected 字段可触发定点重查（复用现有 repair loop，目标=通过校验的字段数提升）

### 本地评估器 `tools/local_eval.py`

对 6 个示例用户（`--user all` 或缺省全跑）跑完整 agent（mock 关闭、真实 Key 若在则用真实链，否则正则链），输出量化报告：
- 字段覆盖率：画像（各字段组）、产品（各字段组）非空比例
- grounding 通过率：写入字段中 exact_quote 能回溯的比例（目标 100%，程序保证）
- 幻觉率：字段值在原文找不到依据的比例（目标 0）
- 结构校验：复用 tools/evaluate.py 的确定性检查
- 逐用户输出 + 汇总，JSON 报告

### 测试要求

- `_validate_with_grounding` 单测：合法/缺 quote/quote 非子串/结构缺键/重试成功/重试仍失败 六类
- 子进程级：mock 网关（现有夹具机制）跑通画像+产品 grounded 抽取全链
- 既有 423 测试全过（不许删改）；新增测试全部通过
- `python tools/local_eval.py` 实际运行输出报告（网关不可达时跑正则链也行，报告标注用的链路）

## 纪律

- 只改/建上述文件；不动 renderer/reconciler/validators/agent.py 的既有逻辑（管线调用点除外）
- 线上依赖仅标准库+requests：校验自己写（schemas.py 已有基础），禁止引入 pydantic 等第三方库
- exact_quote 校验用精确子串判断；任何"宽松匹配"都算违规
- 在 reports/progress.md 追加登记；不 git commit（组织者统一推）
- 完成后返回：文件清单、local_eval 实测报告摘要（各用户覆盖率）、测试结果、遇到的设计取舍
