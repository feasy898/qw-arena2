# qw-arena2 —— 千问 AI Arena「智能选购顾问」参赛 Agent

> 接手文档 ｜ 任务卡见 [TASK.md](TASK.md) ｜ 项目原始自述见 [README-upstream.md](README-upstream.md)（原仓 README，含参赛背景与更多细节）

## 项目是什么

阿里云「千问 AI Arena」平台赛 **消费决策好帮手：智能选购顾问** 的完整参赛仓（CompetitionId `37399010866182`）。agent 读入用户描述 + 商品资料包，产出三份 Markdown：`user_profile`（用户画像）/ `product_list`（产品清单）/ `recommendation`（推荐结论）。赛制：**2026-10-23 截止、每日 ≤3 发、平台保留历史最高分、机测 Top 30 进专家评审**。

仓内包含：agent 本体与提示词、469 个测试、提交包打包链、提交协议逆向报告、本地评估器、判官咨询记录与全部迭代台账。

## 架构一句话

确定性管线：输入解析 → JSON Schema 结构化约束 → 逐字回溯 grounding（推荐结论必须引用产品清单原文）→ 零幻觉输出；配套「本地评估器当确定性裁判 + 平台出分回写台账」的双层验收。

## 构建与运行

- 环境：Python 3.11+，测试依赖 pytest（其余依赖见原仓 README 与 `config.example.json` 说明）。
- 版本自检：`python agent/agent.py --version` → `0.5.2`
- 全量测试：`python -m pytest tests -q -p no:cacheprovider`
- e2e 双门：`python tools/e2e_mock.py`；`python tools/e2e_mock_packaged.py`（对 `dist/agent.zip` 实跑）
- 本地评估器（真链需自备 `DASHSCOPE_API_KEY` 环境变量；离线指标不依赖它）：`python tools/local_eval.py` → 产出 `reports/local_eval/<run>/report.json`
- 提交包：按仓内打包链生成 `dist/agent.zip`（赛规：ZIP ≤100MB、入口 `agent.py`+`agent.json`、必须支持 `--version`）

## 验收基线（2026-10-01 实测）

| 门 | 命令 | 基线 |
|---|---|---|
| G0-1 全量测试 | `python -m pytest tests -q` | **469 passed / 0 failed（约 30s）** |
| G0-2 版本一致 | `python agent/agent.py --version` == dist 包内版本 | 0.5.2 == 0.5.2 |
| G0-3 e2e | 两个 e2e 脚本 | exit 0 |
| G0-4 本地评估三硬指标 | `tools/local_eval.py` 的 report.json | 结构 **6/6**、grounding **100%（570/570）**、幻觉 **0**（画像覆盖 0.6082 / 产品覆盖 0.5488 为诊断指标，不设阈值） |

平台出分台账：`reports/submission_log.md`（每发提交一行：时间戳/包 sha256/流水号/状态/分数）。

## 已知问题

1. **无人值守提交通道未建**：唯一验证过的提交方式是平台页面人工提交（依赖人工登录态）；每日三发的人工依赖如何解除待决断（Temporal worker 方案或正式放弃无人值守，二选一）。
2. **评分器自身随机（已实锤）**：同一包三发可得 8/6/2 不同分——单发分数无决策价值，唯一有效策略 = 当日最强包 ×3 采样，只信「历史最高分变化」这个外部真值。
3. **真链本地评估不可跑**：需要 `DASHSCOPE_API_KEY`，该凭据不随仓分发，接手者需自备；无 key 时四项离线指标照常可跑。
4. **分数台账矛盾待实查**：`submission_log` 一行记「历史最高 11 分」，另一处记「保留最高分为 2」——下次提交会话用平台 API（`ListMySubmissions`/`GetMyParticipation`）实查勘正。

---

## 仓库来源

本仓库自 agentic-factory-projects monorepo 拆分而来（一个项目一个仓库）；monorepo 内历史快照见原仓 feasy898/agentic-factory-projects。
