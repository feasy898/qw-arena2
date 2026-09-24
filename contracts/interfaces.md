# contracts/interfaces.md — 模块签名与数据流契约

> 版本 0.1.0（2026-09-23，契约阶段产出）。**模块签名以本文件为准**（SPEC §13）。改动本文件须同步全部实现模块并在 `reports/progress.md` 登记。
> 所有模块只依赖标准库 + requests；一律 UTF-8；不打印完整用户资料与密钥。

## 0. 公共约定

- **画像对象**：`dict`，key = `contracts/field_catalog.json` 的 `profile.groups[].fields[].key`（扁平，如 `profile_id`、`gender`、`budget_max`）。结构化列表字段（`scenes`）为 `list[dict]`，item key 见 `item_fields[].key`。
- **产品对象**：`dict[str, dict]`，外层 key = canonical_id（如 `P001`）；内层 key = `field_catalog.product.groups[].fields[].key`；结构化列表（`battery_by_mode`）为 `list[dict]`，item key 见 `item_fields[].key`。**未含 key ≠ 缺失表达**：渲染时缺失一律按 field_catalog 的 `missing`/`missing_selection_rule` 落成「未提供/待核验」文本。
- 渲染↔解析往返不变式：`parse_X(render_X(obj))` 与 `obj` 在**取值语义**上等价（缺失表达文本解析回 `None` + 标记，单测保证）。
- 异常类型（定义于所属模块，供跨模块捕获）：
  - `src/model_gateway.py`：`GatewayError(Exception)`（基类）、`MockResponseMissingError`、`ModelNotWhitelistedError`、`UpstreamFatalError`（鉴权失败/模型未授权/预算耗尽）。
  - `src/pipeline.py`：`PromptParseError`（退出码 2）、`InputError`（退出码 3）。
- 路径参数一律接受 `str | os.PathLike`。

## 1. src/schemas.py

SPEC §4 数据契约的 dataclass 完整定义（唯一权威为 SPEC §4，本文件只列用途）：

```python
SourceType(StrEnum)        # 五个产品信息源 + USER_DESCRIPTION（用户描述单独承载）
FactKind(StrEnum)          # 官网声明|实测观察|用户反馈|宣传表述|有依据归纳
FactStatus(StrEnum)        # 有支持|附条件|存在冲突|输入缺失
NeedType(StrEnum)          # 硬约束|软偏好|目标|歧义
ConstraintOutcome(StrEnum) # PASS|FAIL|UNKNOWN|NOT_APPLICABLE

@dataclass EvidenceRef       # source_id, span_id, exact_quote
@dataclass SourceRecord      # source_id, source_type, original_text, spans, path
@dataclass FactCell          # field_key, raw_value, normalized_value, unit, fact_kind, status,
                             # conditions, applicable_variant, as_of, evidence_refs
@dataclass UserNeed          # need_id, original_expression, need_type, operator, expected_value,
                             # priority, evidence_refs
@dataclass ProductRecord     # canonical_id, original_ids, brand_raw, model_raw, fields: dict[str, FactCell]
@dataclass ConstraintResult  # product_id, need_id, result, profile_reference, attribute_reference, explanation
@dataclass RecommendationCard  # DecisionPlan.recommendation_cards 的元素（SPEC §9 固定字段）
@dataclass ExclusionRecord     # DecisionPlan.excluded_products 的元素（五类原因）
@dataclass DecisionPlan      # selected_products, recommendation_cards, excluded_products, overall_conclusion
@dataclass OutputBundle      # user_profile_md, product_list_md, recommendation_md
```

每个 dataclass 提供 `to_dict()`；枚举为 `str` 子类可直接 JSON 序列化。

## 2. src/input_adapter.py — 输入解析（确定性，不用模型）

```python
@dataclass PromptPaths        # input_dir: str; output_dir: str
@dataclass InputBundle        # input_dir: str; user_description_path: str; user_text: str;
                              # source_records: list[SourceRecord]; warnings: list[str]

def parse_prompt(prompt: str) -> PromptPaths
    # 按 platform_contract.json cli.--prompt.parsing_rules 从自然语言指令提取输入/输出路径。
    # 解析失败 raise PromptParseError。

def load_input(input_dir: str | os.PathLike) -> InputBundle
    # 扫描输入目录：00 用户描述（恰取 1 份；0 或多份 → InputError）、01 货架 CSV、
    # 02 报道+实测、03 反馈 CSV、04 官网。逐文件生成 SourceRecord（含稳定 spans）。
    # 目录不合法/缺必需子目录 raise InputError。

def find_user_description(input_dir: str | os.PathLike) -> str
    # 返回唯一用户描述文件路径；0 或多份 raise InputError。
```

## 3. src/source_index.py — 来源登记与检索

```python
class SourceIndex:
    def __init__(self, records: list[SourceRecord]) -> None
    def by_type(self, source_type: SourceType) -> list[SourceRecord]
    def by_product(self, product_ref: str) -> list[SourceRecord]
        # product_ref 接受 P01/P001 等任意别名（按 rules.json 归一匹配）
    def user_description(self) -> SourceRecord | None
```

## 4. src/product_registry.py — 产品清单与编号归一（确定性，不用模型）

```python
def normalize_product_id(raw: str) -> str | None
    # 仅接受明确的产品编号写法（P12/P012 等），尾号去前导零补足 3 位 → P###；非编号返回 None。

def build_registry(records: list[SourceRecord]) -> ProductRegistry
    # 产品全集 = 官网 ∪ 货架 ∪ 报道 ∪ 实测；同编号不同型号 → 身份冲突登记，不合并。
    # 返回的 ProductRecord 仅填充基础标识（brand_raw/model_raw/aliases），事实字段留空。

class ProductRegistry:
    def __init__(self, products: list[ProductRecord], identity_conflicts: list[dict]) -> None
    def get(self, canonical_id: str) -> ProductRecord          # 缺失 raise KeyError
    def all(self) -> list[ProductRecord]                        # canonical_id 升序
    def ids(self) -> list[str]
```

## 5. src/model_gateway.py — 模型网关

```python
def is_mock_mode(config: dict) -> bool
    # True 条件：config mock.enabled 或 QW_FORCE_MOCK=1 或 DASHSCOPE_API_KEY 缺失/=="mock"（SPEC §11）。

class ModelGateway:
    def __init__(self, config: dict) -> None
    def chat(self, messages: list[dict], model: str, *, mock_key: str | None = None) -> dict
        # messages: [{"role": "system"|"user"|"assistant", "content": str}, ...]
        # model 必须在 platform_contract.model_whitelist_text 内，否则 raise ModelNotWhitelistedError。
        # 返回 dict 固定键：
        #   {"role": "assistant", "content": str, "model": str, "mock": bool, "usage": dict}
        # content 为模型原文（约定内含一个 JSON 对象，由调用方 json.loads，本网关不解析业务 JSON）。
```

**mock 模式（必须确定性）**：`mock_key` 必填；从 `tests/fixtures/gateway/responses.json` 顶层对象按 `mock_key` 取
`{"role": "assistant", "content": "<JSON字符串>"}`；`mock_key` 缺失或夹具文件缺失 → raise `MockResponseMissingError`，
**禁止任何静默兜底**（不得按消息内容猜测、不得返回占位响应）。

**real 模式**：仅 `requests` 调 `{OPENAI_BASE_URL}/chat/completions`（OpenAI 兼容；DashScope 原生路径可选）。
429/部分 5xx 指数退避+抖动重试 ≤ `config.max_retries_per_request`；鉴权失败/模型未授权/预算耗尽 → `UpstreamFatalError`；
上下文超限 → 抛 `GatewayError` 子类由调用方缩小分块；超时遵守 `budget_manager` 软截止。

## 6. src/budget_manager.py — 时间/并发/Token 预算

```python
class BudgetManager:
    def __init__(self, config: dict, start_monotonic: float | None = None) -> None
    def soft_remaining_seconds(self) -> float        # 距软截止（默认 1680s）
    def hard_expired(self) -> bool                   # 超硬截止（1800s）
    def acquire_slot(self) -> AbstractContextManager # 并发槽，初始 config.concurrency（默认 2）
    def register_tokens(self, prompt_tokens: int, completion_tokens: int) -> None
    def degradation_stage(self) -> str
        # 返回当前应处降级阶段（SPEC §11 顺序）：
        # "normal" → "no_polish" → "no_review" → "core_only" → "template_only"
        # 禁止用「丢部分产品」降级。
```

## 7. src/profile_extractor.py — 画像抽取（模型）

```python
PROFILE_PROMPT = "prompts/profile_extraction.md"

def extract_profile(user_text: str, gateway: ModelGateway, config: dict) -> dict
    # 用户描述原文 → 画像对象（key 见 field_catalog.profile）。
    # 原子化；硬约束准入与场景优先级按 rules.json。
    # 模型输出不合法时内部有限重试（≤ config.max_retries_per_request），仍失败 raise GatewayError。
    # mock 模式经 gateway.chat(mock_key="profile_extraction") 取夹具。
```

## 8. src/product_extractor.py — 五源产品事实抽取（模型）

```python
def extract_products(products: list[ProductRecord], source_index: SourceIndex,
                     gateway: ModelGateway, config: dict) -> dict[str, dict]
    # 逐产品（canonical_id 升序）汇集五源 SourceRecord → 产品对象草稿
    # （key 见 field_catalog.product，FactCell 带 fact_kind/status/evidence_refs）。
    # 返回 {canonical_id: 产品对象草稿}；冲突判定留给 reconciler。
    # mock 模式 mock_key = f"product_extraction_{canonical_id}"。
```

## 9. src/reconciler.py — 归并与冲突处理（确定性规则 + 有限模型复核）

```python
def merge_fact_cells(cells: list[FactCell]) -> FactCell
    # 同字段多来源 FactCell → 单 FactCell：按 rules.json field_keep_limits 保留限定；
    # 冲突三步法（判真冲突→并列保留条件→保留冲突标待核验）；禁止取平均/直觉择一。

def reconcile_product(product: dict, source_records: list[SourceRecord], config: dict) -> dict
    # 产品对象草稿 → 终稿。source_records 仅用于登记缺口与定点复核（结果仍须回写产品对象，
    # 供下游覆盖检查），不得向决策层泄露原文。

def find_coverage_gaps(products: dict[str, dict], registry) -> list[dict]
    # 反向覆盖检查：字段缺口清单（产品ID/field_key/缺口类型），供上游定点复核。
```

## 10. src/renderer.py — 程序模板渲染与回读（确定性，不用模型）

```python
def render_fact_value(cell: FactCell | dict) -> str
    # FactCell → 表格取值文本；缺失按 field_catalog 缺失表达；条件并列用「；」分隔。

def render_profile(profile: dict) -> str               # 画像对象 → user_profile.md 全文
def render_products(products: list[dict]) -> str       # {cid: 产品对象} → product_list.md 全文
def render_recommendation(plan: DecisionPlan) -> str   # DecisionPlan → recommendation.md 全文
def parse_profile(md: str) -> dict                     # user_profile.md → 画像对象
def parse_products(md: str) -> dict[str, dict]         # product_list.md → {canonical_id: 产品对象}
```

表头/字段顺序/空值标记/节数/表结构全部由模板固定（field_catalog 顺序），不依赖模型。
`render_recommendation` 在有效产品不足 3 款时按 SPEC §9 异常路径渲染（不虚构第三款）。

## 11. src/validators.py — 确定性校验（全程序判定，SPEC §10 十项）

```python
@dataclass ValidationIssue    # code: str; severity: "high"|"medium"|"low"; message: str

def validate_profile_object(obj: dict) -> list[ValidationIssue]
def validate_product_object(obj: dict) -> list[ValidationIssue]
def validate_plan(plan: DecisionPlan, products: dict[str, dict]) -> list[ValidationIssue]

def validate_outputs(output_dir: str | os.PathLike, input_dir: str | os.PathLike) -> list[ValidationIssue]
    # SPEC §10 清单十项：1 恰好3文件且命名精确；2 产品全集=输入全集；3 品牌型号原样；
    # 4 所有产品表结构一致；5 推荐=3个不同产品∈E；6 未选表覆盖其余全集；
    # 7 推荐卡风险/核验字段非空；8 引用存在且指向前两份文档字段；9 退出码/运行时间/编码；
    # 10 表格可被 Markdown 解析（列数一致、无坏行）。
    # 返回空列表 = 通过；high 级 issue = 失败。
```

## 12. src/constraint_engine.py — 硬约束判定（隔离：只读前两份文档的解析对象）

```python
def build_needs(profile: dict) -> list[UserNeed]
    # 从画像对象派生 UserNeed（硬约束准入按 rules.json：约(单值)预算不得直接成硬上限等）。

def constraint_matrix(profile: dict, products: dict[str, dict]) -> list[ConstraintResult]
    # 所有 UserNeed × 所有产品 → 四态矩阵（PASS/FAIL/UNKNOWN/NOT_APPLICABLE），
    # profile_reference/attribute_reference 用 citation_format，UNKNOWN≠PASS。

def valid_product_ids(constraints: list[ConstraintResult]) -> set[str]
    # 有效集 E：对所有适用硬约束均 PASS。
```

## 13. src/selector.py — 排序与组合选择（隔离同上）

```python
def select_recommendation(profile: dict, products: dict[str, dict],
                          constraints: list[ConstraintResult]) -> DecisionPlan
    # E 内排序（rules.json ranking_basis）→ 首选/备选1/备选2 + 推荐卡 + 未选原因（五类）
    # + 一句综合结论。有效产品不足 3 款走 SPEC §9 异常路径。
```

## 14. src/report_writer.py — 输出与日志

```python
def setup_logging(log_dir: str | os.PathLike) -> logging.Logger
    # AGENT_LOG_DIR 下 agent.log；本地缺省 reports/logs/；关键阶段耗时与错误；不打密钥与完整用户资料。

def write_outputs(bundle: OutputBundle, output_dir: str | os.PathLike) -> list[str]
    # 恰好写出 3 份 UTF-8 Markdown（无 BOM），返回写出路径；输出目录不存在则创建。
```

## 15. src/pipeline.py — 主链编排与 CLI

```python
def resolve_config(config_path: str | os.PathLike | None = None) -> dict
    # config.example.json 默认值 + 可选覆盖文件合并；未知键报错。

def run(input_dir: str | os.PathLike, output_dir: str | os.PathLike, config: dict) -> OutputBundle
    # 一次调用完成（不请求补充信息、不访问外部商品信息）：
    # load_input → registry → extract_profile/extract_products → reconciler
    # → render_profile/render_products（冻结）→ parse_profile/parse_products（真实回读）
    # → build_needs/constraint_matrix → select_recommendation → render_recommendation
    # → write_outputs → validate_outputs；失败进入有限修复循环（≤ config.max_repair_loops：
    #   缺口登记→上游重查→更新前两份文档→重新冻结→重新决策）。
    # 全程受 BudgetManager 软截止与降级约束。

def main(argv: list[str] | None = None) -> int
    # --version：stdout 输出版本号（与 agent.json 一致），退出码 0。
    # --prompt <指令>：parse_prompt → run → 校验；退出码按 platform_contract.exit_codes。
```

## 16. tools/evaluate.py（本地评估，签名在此固定，实现归评估负责人）

```python
def evaluate(input_dir: str | os.PathLike, output_dir: str | os.PathLike) -> int
    # 结构失败退出码非 0；0 = 通过。内部调用 validators.validate_outputs + 资源限制检查。
```

## 17. mock 网关夹具（固定格式）

`tests/fixtures/gateway/responses.json`：

```json
{
  "profile_extraction": {"role": "assistant", "content": "{\"profile_id\": \"User_Description_1\", ...}"},
  "product_extraction_P001": {"role": "assistant", "content": "{...}"},
  "定点复核类自定义键": {"role": "assistant", "content": "{...}"}
}
```

- 顶层 key = `mock_key`；value 固定 `{"role": "assistant", "content": "<JSON字符串>"}`；
- `content` 必须是**一个 JSON 对象的字符串**（模型应答的业务负载），网关原样透传不解析；
- `mock_key` 缺失/夹具文件缺失 → `MockResponseMissingError`，禁止静默兜底；
- 夹具必须能覆盖 `tools/e2e_mock.py` 全链路所需 key（fixtures 归测试负责人维护，格式不得偏离本节）。

## 18. 数据流与隔离铁律

```
input_adapter → product_registry ─┬─ profile_extractor ──┐
                                  └─ product_extractor → reconciler
                                                          │ 渲染冻结
                                          renderer.render_profile / render_products
                                                          │ 真实回读（不许绕过）
                                          renderer.parse_profile / parse_products
                                                          ▼
                          constraint_engine → selector → renderer.render_recommendation
                                                          ▼
                                  report_writer → validators（有限修复循环 ≤ 2）
```

隔离铁律（SPEC §3）：`constraint_engine`/`selector` 的入参只能是 parse 后的画像/产品对象；
不得接收 `SourceRecord` 原文、不得复用抽取阶段含原文的模型会话、不得自行从原文补事实。
