# worklog — qw-arena2（append-only：每日做了什么/决策/下一步）

- 2026-10-01 迁移完成：windev-01 D:/workspace/qw-arena2 → anolis-gpu-01:/opt/gpumachine/projects/qw-arena2（线1-2B 迁移批；sha256 两端核对过=0cdcf961；验证：pytest **445 passed** + version 0.5.1）。排除未迁：`.secrets/`（cookie 解密产物）、node_modules/.mimosa/.zcode。接续卡：continue-cards/qw-arena2.md（⚠️ 最紧迫：今日三发配额未用）。
- 2026-10-01 worker-B（eval 扩充批·loop 第1轮）：eval 集 `tests/fixtures/eval/` 净增 13 案（=10 失分场景类型 + 3 条真实 badcase，全部与首批三案同构：自包含 input 拷贝 + valid 产物基线上的确定性变坏，期望检查项为评估器实跑观察值）。新增全集统一跑法 `tests/regression/test_eval_set.py`（24 tests：valid 全过 exit0；16 invalid 逐案 exit1 + 声明检查项被点名含 high；invalid 目录与清单双向盘点；同案双跑确定性一致；CLI 子进程 17 案全量实跑逐案断言退出码）——`pytest tests/regression/test_eval_set.py -q` **24 passed, exit 0**。三条 badcase 出处：①`badcase_degrade_comment_e10`=submission_log 行21/22（v0.4.11/12 降级注释毁结构，平台 scored=2；本地复现 E10/E8 与 09-28 记录完全一致，注释格式/落点=src/pipeline.py degrade 分支原样）；②`badcase_scene_priority_empty_draft`=行30+progress.md:47（v0.5.0 scene_priority 校验矛盾→修复环用尽→phoenix 空草稿；产物=agent.py `_fallback_templates` 原样）；③`badcase_officialsite_fields_dropped`=GROK_ASK5/answer_E+行25/30（官网五字段开关 11↔1；本地锁「列在格空，非删列」的 E4 结构不变量——直写字面扣分属平台 LLM 评分轴，本地评估器不可判，已在 README 如实注明）。场景 10 案覆盖检查项 E1-E10/OBJ/X2/X3（X1 仅输入侧可触发）。全仓 `pytest tests -q`：467 passed + 2 failed，2 失败均在 worker-A 本轮未提交策略改动面（agent.json 已升 0.5.2 vs phoenix 版本断言 0.5.1；schema_extractor 停采五字段行为变更 vs 旧断言），与 eval 集无关（本批 24 用例全绿）。边界遵守：未动策略源码/prompts/dist/raw；worker-A 在飞文件未纳入本提交。

- 2026-10-01 worker-A 夜班轮（策略迭代 v0.5.1→v0.5.2）：
  **得失定位**：昨日 v0.5.1 首发（CBS 42214338851619，01:50:48）实得 **1 分**（账号保留最高分仍为
  09-30 的 2），低于同包噪声地板（answer_E：±2）。定位两个失分源：①**主因=官网五字段停采**
  （v0.5.1 PRODUCT_SUPPRESSED_FIELDS 把 category/generation/core_functions/audio_formats/
  wearing_design 从 grounded Schema 剔除）→ 产物「产品类别/形态=未提供」→ constraint_engine
  ._judge_form 只能判 UNKNOWN → valid_product_ids（constraint_engine.py:847-864）对 HARD 需求
  的 UNKNOWN/FAIL 一律拦截 → 有效集空 → R1 阶梯记 0。v0.5.1 当时的 progress 注记「有效集=
  PASS∪UNKNOWN、形态未知不逐出、R1 归零不成立」系误读 selector.py:257（推荐卡文案的 PASS/
  UNKNOWN 并集），非有效集口径——本地存档 v051_final（真链 6 用户）实证：U3（明写「头戴式」）
  三槽全「无」、主要阻断 need_form_factor 证据不足×5 款；语料五款全部「无主动降噪」，U2/U4 的
  noise FAIL×5 属诚实拒答非缺陷。answer_E §1/§4 与此一致：v0.4.16/17 两连 1 分归因**正则管道
  直写**字面；grounded 写入链（v0.5.0 同包 8/6/2）从未崩；「删键」从未测过且方向有害。
  ②**降级诊断 HTML 注释破坏 E10**：pipeline.py 降级分支仍带 v0.4.11 引入（v0.4.13 删、v0.4.15
  重构误带回）的「<!-- degrade: … -->」写入；v0.4.12 平台 Score=2 即此因（submission_log 行21/22）；
  本地 v052_regex_check 复现：无 Key 降级链 6 用户结构 **0/6 fail**（parse_error=表格外非标题行）。
  **v0.5.2 改动**（均策略源码；worker-B 的 eval 集提交 14378e7 未触碰，其 badcase_degrade_comment_e10
  /badcase_officialsite_fields_dropped 夹具与本次改动同轴互补）：(a) 反向 patch v0.5.1 hunk 恢复五
  字段进 Schema+Prompt（36 键；正则链侧维持 v0.4.18 停写不变——answer_E 明示勿复活正则直写）；
  (b) 删除降级注释写入，诊断仍走 AGENT_LOG_DIR 日志；(c) 保留 v0.5.1 scene_priority 校验修复。
  **验证（全程零真实 LLM 调用）**：pytest **469 passed（退出码 0**，含翻新的
  test_official_model_fields_restored_everywhere 与 full-chain category 入草稿断言）；compileall OK；
  e2e_mock exit 0；e2e_mock_packaged（对 dist/agent.zip 实跑）exit 0；local_eval 正则链改前
  v052_regex_check 结构 0/6、覆盖率 0 → 改后 v052_regex_after **结构 6/6、画像 0.608、产品 0.5488
  （=历史 regex 基线）、grounding 570/570、幻觉 0、注释 grep=0**。真链「改后」本地不可跑：GPU 机
  无 .secrets/DASHSCOPE_API_KEY（迁移时按令排除）——改前真链证据=v051_final 存档（U3 有效集空），
  改后为单元级实证（stub 全链 category 观察携带逐字锚点入草稿 + _judge_form「骨传导」PASS /
  空类别 UNKNOWN 对照）。**交付**：dist/agent.zip 重建（lib/ 因旧 pip+aliyun 交叉下载确定性崩
  （urllib3 TypeError，重试 2 次同因）从上一包原样回收，requirements 未变；其余步骤全复用
  tools/package.py 函数与安全自检）=**1792668 B、308 文件、包内 version=0.5.2、smoke exit 0、
  sha256=0250a1eb9bea5fc3f2cce3c4cd242970a6faef2fc100cb9c705cadc91c1031f8**。明日主控提交建议：
  按 answer_E 规则首发 v0.5.2 本包，出分 ≤2 则后两发切 v0.4.18（源 e410946 在 git）。
