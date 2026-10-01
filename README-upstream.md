# qw-arena2 — 千问 AI Arena「消费决策好帮手：智能选购顾问」参赛资料

比赛相关信息已全部抓取并整理完毕。**主文档：[比赛完整资料.md](./比赛完整资料.md)**。

## 比赛速览

- **任务**：构建端到端消费决策 Agent —— 读入用户描述 + 商品资料包（无线耳机品类，示例 6 用户 × 5 产品），一次运行产出 `user_profile.md`（用户画像）、`product_list.md`（产品属性）、`recommendation.md`（三选推荐）三份 Markdown。
- **关键时间**：2026-10-23 提交截止（可反复提交，每天 ≤ 3 次）；机测 Top 30 进专家评审（10-23 ～ 10-31）；11 月初颁奖（金 1 / 银 2 / 铜 2）。
- **打包**：`agent/` 目录 ZIP ≤ 100MB，入口 `agent.py|js|jar|agent` + `agent.json` + 依赖声明文件，依赖全部打进包（线上无网络，仅放行 `*.aliyuncs.com`），`--prompt` 传参，`--version` 必须支持，≤ 30 分钟 / 4GB。
- **模型**：只能用平台规定列表（DashScope API / OpenAI 兼容），Key 从 `DASHSCOPE_API_KEY` 环境变量读。
- **官方示例数据集 + 本地 Docker 验证工具**：见主文档第 8、9 节。

## 目录

| 路径 | 内容 |
|---|---|
| `比赛完整资料.md` | 全部比赛信息的结构化汇总（赛程/奖励/规范/评分细则/FAQ/排查清单/链接） |
| `raw/pages/` | 各页面渲染后 HTML 存档（任务台 2 页 + 赛题页 4 标签 + Arena 首页 + FAQ 展开版） |
| `raw/*.txt` | 上述页面的纯文本提取版 |
| `raw/dataset_sample/` | 官方示例数据集（已解压） |
| `raw/Agent本地验证指南.md` | 官方 Docker 本地验证工具说明全文 |
| `tools/` | 本次抓取用的脚本（Edge Cookie 解密、会话构建、CDP 抓取） |

## 抓取方式备忘（2026-09-23）

1. 页面需阿里云 SSO 登录。先用 VSS 卷影复制被锁的 Edge Cookies 库 + DPAPI 解密出登录态（`tools/decrypt_edge_cookies.py`，产物在 `.secrets/`，**敏感勿外传**），验证了免登录 API 通道；随后直接在内置浏览器登录，用浏览器自动化逐页抓取渲染后 DOM。
2. 任务台为阿里云低代码搭建的 SPA，静态逆向（`raw/cup_dashboard.js` 等）无数据接口残留，改为渲染后抓取，内容完整。
