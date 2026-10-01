# CONTEXT — qw-arena2（千问 AI Arena 智能选购顾问）

> 建卡：编排批-线3 2026-10-01（docs/project-orchestration.md §1.1）；素材=continue-cards/qw-arena2.md（接续卡，迁移验证 2026-10-01 实跑）+ 仓内 AGENTS.md/SPEC.md/reports/。事实数值沿接续卡口径。

## 背景
- 千问 AI Arena 比赛项目「智能选购顾问」：阿里云任务台机测，**2026-10-23 截止，每天 ≤3 次提交**，机测 Top30 进专家评审；两账号 CBS、newlai。
- git 仓 HEAD 2c605e9 = **v0.5.1**（2026-09-30），remote=cnb Cloudbird-Software/qw-arena-agent；dist/agent.zip 已就绪（1791976B/308 文件自检过）。
- 已实锤机制（submission_log 09-29/30 判别实验）：**评分器自身随机，同字节包三发三个分（8/6/2）**；平台保留历史最高分 → 唯一有效策略 = 最强包 ×3 采样。

## 目标
1. 用满每日三发配额：当日最强包全发采样（出分 ≤2 → 后两发切 v0.4.18 历史最高分形态包）。
2. 机测名次进入 Top30 或更高；结构性改动只在没有更强包时做（改动必须 pytest 全绿再打包）。

## 验收标准
- 打包前置：`python -m pytest tests -q` 全绿（当前基线 **445 passed**/27.92s）；`python agent/agent.py --version` 版本自检。
- 每发提交后结果追加 `reports/submission_log.md`（序号/账号/时间/版本/改动/大小/FormatCheck/EvalStatus/Score/备注）。
- 提交协议：`reports/submission_protocol.md`（阿里云任务台**页面上下文** fetch，CompetitionId=37399010866182，GetCompetitionFileUploadUrl→上传→Complete；requests 直调会 ConsoleNeedLogin，已验证不可用）。
- 端到端 mock：`python tools/e2e_mock.py`。

## 干系人
- owner：策略裁决（版本切换/账号分配）、账号凭据面；比赛平台（提交/评测）。

## 当前里程碑
- **即刻**：10-01 三发配额未用——全发 v0.5.1，首发≤2 分切 v0.4.18（grok answer_E 决策已记录 submission_log 尾）。
- 每日提交流：Temporal 模板 workflows/qw-arena2-daily.yaml（company-ops 仓，status=shadow，workers [待-bootstrap]；过渡=GPU 机 cron）。
- **2026-10-23 比赛截止**。

## 风险
- 提交协议硬依赖浏览器页面登录态：`.secrets/` cookie 解密产物**未随迁**（留 windev-01，该机约 10-07 销毁）——无人值守提交需在 GPU 端重建登录态 [待 owner 凭据面]。
- ⚠️ `.git/config` remote URL 内嵌 cnb token（本机原样随迁）：接手后改 credential 方式，勿外传该 URL、勿复制进任何文档。
- 评分器随机性：单发分数无意义，比较只看历史最高分变化。
