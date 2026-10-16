# 智能选购顾问 — 千问 AI Arena「消费决策好帮手」参赛 Agent

端到端消费决策 Agent：读入 1 份用户描述 + 五源商品资料包（货架快照 / 媒体报道 /
第三方实测 / 用户反馈 / 品牌官网），一次运行产出三份 Markdown——`user_profile.md`
（用户画像）、`product_list.md`（产品属性）、`recommendation.md`（消费推荐报告）。
任务书与赛制见 [比赛完整资料.md](./比赛完整资料.md)。

## 架构一句话

强模型单次直出主链（`src/advisor_pipeline.py`）→ 出厂质检门（`src/self_check.py`，
不过则定点重试 1 次）→ 失败回退多阶段确定性降级链（`src/pipeline.py`）→
入口「不死鸟」防线（`agent/agent.py`，任何启动期故障都保证「三份产物 + 退出码 0」）。

内容纪律：一切取值可回溯到输入文件；输入未载明的一律写「未提供 / 待核验」，
不编造、不外推。

## 仓库布局

| 路径 | 内容 |
|---|---|
| `agent/` | 提交包入口（agent.py / agent.json / requirements.txt）与 vendored 依赖 |
| `src/` | 直出主链、多阶段确定性链、质检门、模型网关、抽取/约束/渲染各层 |
| `prompts/` | 直出主链 prompt 模板与多阶段链抽取模板 |
| `contracts/` | 平台契约、字段目录、判定规则（单一事实源） |
| `tools/` | 打包链、mock e2e 双门、真实案例运行器、确定性评分器、本地评估工具 |
| `tests/` | 单元 + 回归测试与夹具 |
| `raw/dataset_sample/` | 官方示例数据集（已解压） |
| `比赛完整资料.md` | 赛程 / 规范 / 评分细则 / FAQ |

## 运行与验证

- 环境：Python 3.11+（打包目标 Debian 12 / Python 3.12）；测试依赖 pytest。
- 版本自检：`python agent/agent.py --version`
- 全量测试：`python -m pytest tests -q`
- mock e2e 双门（零真实调用）：`python tools/e2e_mock.py`；对打包产物实跑
  `python tools/e2e_mock_packaged.py`
- 提交包：`python tools/package.py` → `dist/agent.zip`（双布局，ZIP ≤100MB，
  入口 `agent.py` + `agent.json`，支持 `--version` 与 `--prompt`）
- 真实网关案例运行器：`python tools/e2e_real_case.py --case-dir <五目录输入> --name <运行名>`

## 配置与密钥

- 密钥只从环境变量读：`DASHSCOPE_API_KEY`；网关 `OPENAI_BASE_URL`（以 /v1 结尾）。
- 模型选型：`config.example.json` 的 `advisor` 段（gen_model / fallback_models），
  仅允许平台白名单内模型（`contracts/platform_contract.json`）。
- 运行遥测写入 `AGENT_LOG_DIR/agent.log`。

详细架构说明见 `README-package.md`（打包时更名为 README.md 随包分发）。
