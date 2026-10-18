# -*- coding: utf-8 -*-
"""src/advisor_pipeline.py — 顾问直出路径：强模型单次调用直出三份产物。

设计取向：任务输入（用户描述 + 产品资料包）体量小，强模型一次调用即可通读
全部产品与全部来源，跨产品比较与来源间矛盾识别在同一个注意力上下文内完成；
相比多阶段拼接式抽取，直出少一道「过程性文本 + 字段遗漏」的引入点。本路径 =
单次 LLM 直出 + 严格后处理（去过程措辞/模板残留），产物结构严格对齐官方题面
三段。

模型选型（configurable）：GEN_MODEL / FALLBACK_MODELS 仅作为兜底默认；运行时经
src.pipeline.resolve_config 读取 config.example.json 的「advisor」段
（gen_model / fallback_models），配置缺失或不可用时回退默认值。

出厂质检：write_outputs 后、镜像前跑 src.self_check.run（规格符合性质检，规则
单一事实源与 tools/deterministic_judge 一致）；passed=True 才镜像并返回 0；
不过则在预算门限内恰好重试 1 次（仅主选模型，prompt 尾部注入失败清单）。
重试仍不过时交付 failed_atoms 最少的那一版产物（写盘+镜像），stderr 与
agent.log 记录失败清单，仍返回 0——质检不过不构成回退理由，多阶段降级链的
产物质量系统性低于直出链。

失败语义：仅当 1) 模型调用全挂（连一次有效响应都没有）或 2) parse_three_docs
抛错（三份不齐）时才抛出，由 agent.py 入口回退到 src.pipeline.main（多阶段
确定性链），不死鸟防线不变。

遥测纪律：成功路径把 usage（模型/耗时/token/请求次数）写 AGENT_LOG_DIR/agent.log
（目录仅认该 env，未设置则跳过；token 仅在响应携带 usage 块时记录）。遥测任何
失败都静默——绝不影响主结果。

密钥纪律：仅环境变量 DASHSCOPE_API_KEY / OPENAI_BASE_URL（以 /v1 结尾）。
"""
from __future__ import annotations

import logging
import os
import re
import sys
import time
from pathlib import Path

GEN_MODEL = "qwen3.8-max"
FALLBACK_MODELS = ("qwen3.7-max", "deepseek-v4-pro", "kimi-k2.6", "qwen3.7-plus")
"""兜底默认选型；正常路径以 config.example.json「advisor」段为准（_advisor_models）。"""
TIMEOUT_S = 600  # 单次调用墙钟上限（timekeeper.call_with_wallclock 硬封顶）；
# requests 的 socket timeout 是逐次读口径，慢流式响应可无限续命越过平台 1800s
# 击杀线（2026-10-18「结果测评失败=agent运行超过限定时间」实证），故单调用一律
# 改为墙钟口径；逐次再被进程死线钳制（见 run）。真链单次成功调用常见数十至
# 数百秒，600s 上限只截断「注定拖死整场」的挂起调用。
DEADLINE_S = 1500.0
# 进程级死线：与 src.timekeeper 全局死线（平台 1800s 硬限 − 300s 安全垫）取 min。
# 若候选链轮换不看总墙钟，平台外部击杀时将无任何产物落盘、防线层也来不及执行；
# 每次发起调用前检查剩余预算，不足则直接移交多阶段降级链（其预算同样锚定
# timekeeper 进程起点，见 src.pipeline.run）。
MIN_ATTEMPT_BUDGET_S = 120.0  # 剩余不足 FALLBACK_RESERVE_S+此值 时不再发起新调用
FALLBACK_RESERVE_S = 150.0    # 移交接替降级链的最短余量（确定性链+写盘+镜像）

# 限流退避：候选模型共享同一账号限流池，「429 即换模型」只会连环 429 烧尽
# 候选链；对限流类状态码原地指数退避后再换。
_CHAT_RETRY_STATUS = (429, 500, 502, 503, 504)
_CHAT_RETRY_MAX = 2           # 同一模型对限流类状态至多原地退避重试 2 次
_CHAT_BACKOFF_BASE_S = 5.0    # 5s → 10s（加亚秒级抖动）

# 官方 OpenAI 兼容端点兜底（仅本地开发缺省；平台评测环境必注入 OPENAI_BASE_URL）。
# 公开服务地址，非密钥。
DEFAULT_OPENAI_BASE_URL = "https://dashscope.aliyuncs.com/compatible-mode/v1"

# 出厂质检重试的预算门限（锚定 timekeeper 全局剩余，见 _retry_allowed）：
# 重试路径上「历史最优版已落盘」，只需预留质检+写盘+镜像+交接开销（60s）与
# 少量余量（30s）；剩余预算不足「上次调用耗时×放大系数+预留」时不重试，
# 避免把整场拖过全局死线。
RETRY_FINISH_RESERVE_S = 90.0
RETRY_CALL_HEADROOM = 1.2    # 上次模型调用耗时的放大系数（估计下一次同量级）
RETRY_RESERVE_S = 60.0       # 固定预留（质检+写盘+镜像+交接开销）

FILES = ("user_profile.md", "product_list.md", "recommendation.md")
# Prompt 模板按包内资源定位：仓库布局与提交包双布局下 prompts/ 均与 src/ 互为
# 兄弟目录，REPO_ROOT = src/ 的父目录恒命中。
REPO_ROOT = Path(__file__).resolve().parent.parent
_PROMPT_FILE = REPO_ROOT / "prompts" / "advisor_prompt.txt"

# 全局时钟（进程级死线 + 调用墙钟）；不可用时退化为各调用点本地计时（旧行为），
# 绝不让时间源故障阻断直出链。
try:
    from src import timekeeper
except Exception:  # noqa: BLE001
    timekeeper = None  # type: ignore[assignment]

# 过程性措辞/模板残留 → 结论化表达（后处理硬卡；这类串会显著损伤产物可读性）
_REWRITES = {
    "（多来源取值并列保留，未取平均、未择一（取值存在差异，待核验））": "（待核验）",
    "（多来源取值并列保留，未取平均、未择一）": "（待核验）",
    "多来源取值并列保留，未取平均、未择一（取值存在差异，待核验）": "待核验",
    "多来源取值并列保留，未取平均、未择一": "待核验",
    "资料未见直接记载（未证实）": "待核验",
    "资料未见直接记载": "待核验",
    "未证实": "待核验",
    # 程序日志式短语（确定性链的程序口吻，不得出现在面向用户的产物中）
    "展示价(元)列直接读取（模型抽取缺失，程序按来源行补录）": "待核验",
    "（模型抽取缺失，程序按来源行补录）": "（待核验）",
    "模型抽取缺失，程序按来源行补录": "待核验",
    "（必须单列一条观察）": "（待核验）",
    "必须单列一条观察": "待核验",
    "不直接当硬上限": "（参考值，非硬上限）",
}
_TEMPLATE_RESIDUES = (
    re.compile(r"<\s*UNDEFINED\s*!?\s*>", re.IGNORECASE),
    re.compile(r"<图片[:：]?\s*\d+\s*>"),
    re.compile(r"\[图片[:：]?\s*\d+\s*\]"),
    re.compile(r"<\s*/?\s*[a-zA-Z][^>\n]{0,40}>"),  # 残留 HTML/XML 角标
)
# 画像编号行：| 标识 | 画像编号 | <取值> |（仅当取值为空/未提供时回填文件名编号）
_PROFILE_NO_ROW_RE = re.compile(
    r"(?m)^(\|[^|\n]*\|[ \t]*画像编号[ \t]*\|)([^|\n]*)(\|)")


def _advisor_models() -> tuple[str, tuple[str, ...]]:
    """模型选型：config「advisor」段（经 src.pipeline.resolve_config 读取）优先。

    配置段缺失/配置不可用（resolve_config 抛错）时回退模块默认值
    （GEN_MODEL / FALLBACK_MODELS），行为与配置化之前完全等价。
    """
    try:
        from src.pipeline import resolve_config  # 复用既有配置加载，不另造轮子
        section = resolve_config().get("advisor") or {}
    except Exception:  # noqa: BLE001  配置不可用不阻断直出路径
        section = {}
    gen = str(section.get("gen_model") or "").strip() or GEN_MODEL
    raw_fallbacks = section.get("fallback_models")
    fallbacks = FALLBACK_MODELS
    if isinstance(raw_fallbacks, (list, tuple)):
        cleaned = tuple(str(m).strip() for m in raw_fallbacks if str(m).strip())
        if cleaned:
            fallbacks = cleaned
    # 白名单校验：advisor 段不经网关，配置里的白名单外模型只会在运行期逐个
    # 404；装载时即拦截——gen 违规降回模块默认，fallbacks 违规剔除；白名单
    # 本身不可读时不增设拦截（运行期错误照旧冒头）。
    try:
        from src.model_gateway import load_model_whitelist
        whitelist = set(load_model_whitelist())
    except Exception:  # noqa: BLE001
        return gen, fallbacks
    if gen not in whitelist:
        print(f"[advisor] 配置 gen_model={gen} 不在平台白名单，改用默认 {GEN_MODEL}",
              file=sys.stderr)
        gen = GEN_MODEL
    kept = tuple(m for m in fallbacks if m in whitelist)
    dropped = sorted(set(fallbacks) - set(kept))
    if dropped:
        print(f"[advisor] 配置含白名单外模型，已剔除：{dropped}", file=sys.stderr)
    return gen, (kept or FALLBACK_MODELS)


def _read_env() -> tuple[str, str]:
    key = os.environ.get("DASHSCOPE_API_KEY", "").strip()
    if not key:
        raise RuntimeError("DASHSCOPE_API_KEY 未设置")
    base = os.environ.get("OPENAI_BASE_URL", "").strip().rstrip("/")
    if not base:
        base = DEFAULT_OPENAI_BASE_URL
    return key, base


def collect_input(input_dir: str) -> str:
    """输入目录拼成单块文本。00_User_Descriptions 只取文件名排序第一份。"""
    root = Path(input_dir)
    if not root.is_dir():
        raise RuntimeError(f"输入目录不存在：{input_dir}")
    parts: list[str] = []
    user_dir = root / "00_User_Descriptions"
    if user_dir.is_dir():
        users = sorted(
            (p for p in user_dir.iterdir()
             if p.is_file() and p.name.startswith("User_Description_")),
            key=lambda p: p.name)
        for p in users[:1]:
            parts.append(f"\n===== 00_User_Descriptions/{p.name} =====\n"
                         f"{p.read_text(encoding='utf-8', errors='replace')}\n")
    for sub in sorted(root.iterdir()):
        if sub.name == "00_User_Descriptions" or not sub.is_dir():
            continue
        for p in sorted(sub.rglob("*")):
            if not p.is_file():
                continue
            rel = p.relative_to(root).as_posix()
            try:
                data = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            parts.append(f"\n===== {rel} =====\n{data}\n")
    return "\n".join(parts)


def _chat(key: str, base: str, model: str, user_msg: str,
          timeout_s: float | None = None, deadline: float | None = None
          ) -> tuple[str, dict | None]:
    """一次补全请求。返回 (正文, usage)：usage 为响应携带的 token 用量
    （prompt/completion/total），响应未携带 usage 块或缺数值键时为 None
    （遥测「如响应携带」口径，不影响正文语义）。

    timeout_s：本次调用的 **墙钟** 上限（run 按进程死线逐次钳制后传入；
    缺省 TIMEOUT_S）。实现经 src.timekeeper.call_with_wallclock：requests 的
    socket timeout 是逐次读口径，慢流式响应可无限续命；墙钟到点即抛错换
    下一候选，绝不让单次调用越过平台击杀线。deadline：进程死线
    （time.monotonic() 口径），退避等待若越过死线则放弃重试直接抛错。

    采样参数：低 temperature + 固定 seed / top_p（降低逐次波动；OpenAI 兼容
    字段直传，网关不支持时按其兼容层语义忽略）。不发送 max_tokens——显式
    限制会致思考预算耗尽、content 为空；空 content 视为模型失败抛错换下一个。
    限流类状态码（429/5xx 部分）原地指数退避至多 _CHAT_RETRY_MAX 次，再抛错
    换模型。"""
    import requests  # vendored lib；仅本函数按需引入
    body = {
        "model": model,
        "messages": [
            {"role": "system",
             "content": "你是严谨的消费决策分析师。仅产出三份 markdown，不要任何解释。"},
            {"role": "user", "content": user_msg},
        ],
        "temperature": 0.05,
        "seed": 42,
        "top_p": 0.9,
    }
    timeout_s = TIMEOUT_S if timeout_s is None else timeout_s
    last_status: int | None = None
    for attempt in range(_CHAT_RETRY_MAX + 1):
        if timekeeper is not None:
            try:
                r = timekeeper.call_with_wallclock(
                    requests.post, timeout_s,
                    f"{base}/chat/completions",
                    headers={"Authorization": f"Bearer {key}",
                             "Content-Type": "application/json"},
                    json=body, timeout=timeout_s)
            except timekeeper.WallClockTimeout as exc:
                raise RuntimeError(f"{model} {exc}") from exc
        else:  # 时间源不可用：退化为纯 socket 超时（旧行为）
            r = requests.post(f"{base}/chat/completions",
                              headers={"Authorization": f"Bearer {key}",
                                       "Content-Type": "application/json"},
                              json=body, timeout=timeout_s)
        if r.status_code == 200:
            break
        last_status = r.status_code
        if r.status_code not in _CHAT_RETRY_STATUS or attempt >= _CHAT_RETRY_MAX:
            raise RuntimeError(f"{model} HTTP {r.status_code}: {r.text[:200]}")
        wait = _CHAT_BACKOFF_BASE_S * (2 ** attempt) + (time.monotonic_ns() % 1000) / 1000.0
        if deadline is not None and time.monotonic() + wait + RETRY_RESERVE_S > deadline:
            raise RuntimeError(f"{model} HTTP {r.status_code}（退避等待将越过死线，放弃重试）")
        print(f"[advisor] {model} HTTP {r.status_code}，{wait:.1f}s 后原地重试 "
              f"({attempt + 1}/{_CHAT_RETRY_MAX})", file=sys.stderr)
        time.sleep(wait)
    payload = r.json()
    content = payload["choices"][0]["message"].get("content")
    if not content or not content.strip():
        raise RuntimeError(f"{model} 返回空 content（模型异常或内容拦截），换下一候选")
    usage = payload.get("usage")
    if not isinstance(usage, dict) or not any(
            usage.get(k) is not None
            for k in ("prompt_tokens", "completion_tokens", "total_tokens")):
        return content, None
    return content, {
        k: int(usage.get(k) or 0)
        for k in ("prompt_tokens", "completion_tokens", "total_tokens")}


def parse_three_docs(text: str) -> dict[str, str]:
    """按 '=====FILE: 名称=====' 分隔解析三份文档；缺失时抛错走回退。"""
    pattern = re.compile(r"^=====FILE:\s*(\S+?)\s*=====\s*$", re.MULTILINE)
    chunks = pattern.split(text)
    docs: dict[str, str] = {}
    for i in range(1, len(chunks) - 1, 2):
        name = chunks[i].strip()
        if name in FILES:
            docs[name] = chunks[i + 1].strip() + "\n"
    missing = [f for f in FILES if f not in docs]
    if missing:
        raise RuntimeError(f"模型输出未解析到全部三份文档，缺：{missing}")
    return docs


def profile_no_from_bundle(bundle: str) -> str | None:
    """输入束 00_User_Descriptions 文件名（User_Description_N.txt）→ 画像编号 N。

    官方字段目录 contracts/field_catalog.json profile.profile_id：取值=输入文件名
    编号。只认第一份用户描述的文件名（collect_input 同口径），取不到返回 None。
    """
    m = re.search(r"User_Description_(\d+)\.txt", bundle or "")
    return m.group(1) if m else None


def postprocess(content: str, profile_no: str | None = None) -> str:
    """去过程措辞 + 去模板残留；不改 Markdown 表格结构（分隔符必须保留半角 |）。

    profile_no 给定时，把 user_profile.md「标识|画像编号」行的"未提供"/空取值
    回填为输入文件名编号（纯输入元数据，非编造）；模型已写出取值则不覆盖。
    """
    out = content
    for k, v in _REWRITES.items():
        out = out.replace(k, v)
    for rx in _TEMPLATE_RESIDUES:
        out = rx.sub("未提供", out)
    if profile_no:
        def _fill(m: re.Match[str]) -> str:
            if m.group(2).strip() not in ("", "未提供"):
                return m.group(0)
            return f"{m.group(1)} {profile_no} {m.group(3)}"
        out = _PROFILE_NO_ROW_RE.sub(_fill, out, count=1)
    return out


def write_outputs(output_dir: str, docs: dict[str, str],
                  profile_no: str | None = None) -> None:
    root = Path(output_dir)
    root.mkdir(parents=True, exist_ok=True)
    for name in FILES:
        no = profile_no if name == "user_profile.md" else None
        (root / name).write_text(postprocess(docs[name], no), encoding="utf-8")


def _mirror(output_dir: str, input_dir: str, prompt_text: str) -> None:
    """镜像：同三份产物写入全部候选输出位置（平台检查的目录不止一个）。"""
    try:
        from src.pipeline import _mirror_output_dirs  # 延迟导入，避免循环依赖
        import shutil
        for cand in _mirror_output_dirs(prompt_text, input_dir, output_dir):
            if os.path.abspath(cand) == os.path.abspath(output_dir):
                continue
            try:
                os.makedirs(cand, exist_ok=True)
                for name in FILES:
                    shutil.copy2(os.path.join(output_dir, name),
                                 os.path.join(cand, name))
            except OSError:
                continue
    except Exception:  # noqa: BLE001  镜像绝不影响主结果
        pass


def _log_usage(model: str, elapsed_s: float, tokens: dict[str, int] | None,
               request_count: int) -> None:
    """成功路径遥测：AGENT_LOG_DIR/agent.log 记「运行完成」+「usage 汇总」两行。

    行风格与多阶段链（src.pipeline run()）一致：经同一 _ensure_logging 取
    logger/handler/格式串。AGENT_LOG_DIR 未设置 → 直接跳过；token 仅在响应携带
    usage 块时记录（tokens=None 记「token=响应未携带」）。任何失败静默。
    """
    log_dir = os.environ.get("AGENT_LOG_DIR", "").strip()
    if not log_dir:
        return  # 无 env 时跳过（直出路径不落缺省目录）
    try:
        from src.pipeline import _ensure_logging, resolve_config
        logger = _ensure_logging(resolve_config())
        logger.info("运行完成 耗时=%.2fs 模型=%s 输出=%s",
                    elapsed_s, model, list(FILES))
        if tokens is not None:
            logger.info("usage 汇总 model=%s prompt=%d completion=%d total=%d requests=%d",
                        model, tokens["prompt_tokens"], tokens["completion_tokens"],
                        tokens["total_tokens"], request_count)
        else:
            logger.info("usage 汇总 model=%s token=响应未携带 requests=%d",
                        model, request_count)
    except Exception:  # noqa: BLE001  遥测绝不影响主结果
        pass


def _log_gate_delivered(model: str, check: dict) -> None:
    """质检未全过仍交付历史最优版的收尾记录——stderr 逐条失败清单，
    agent.log 一条「自检未全过已交付最优版」汇总行（含失败清单，便于追查）。
    AGENT_LOG_DIR 未设置时跳过文件日志（与 _log_usage 同口径）；任何失败静默，
    绝不影响交付。"""
    atoms = list(check.get("failed_atoms") or [])
    head = (f"[advisor] 自检未全过已交付最优版 model={model} "
            f"atoms={len(atoms)} score_hint={check.get('score_hint')}")
    print(head, file=sys.stderr)
    for i, a in enumerate(atoms, 1):
        print(f"[advisor] 失败清单 {i}) {a['rule']}：{a['message']}（出现在 {a['doc']}）",
              file=sys.stderr)
    log_dir = os.environ.get("AGENT_LOG_DIR", "").strip()
    if not log_dir:
        return
    try:
        from src.pipeline import _ensure_logging, resolve_config
        logger = _ensure_logging(resolve_config())
        logger.info("自检未全过已交付最优版 model=%s atoms=%d score_hint=%s 失败清单=[%s]",
                    model, len(atoms), check.get("score_hint"),
                    "；".join(f"{a['rule']}：{a['message']}（{a['doc']}）" for a in atoms))
    except Exception:  # noqa: BLE001  遥测绝不影响主结果
        pass


def _retry_allowed(remaining_s: float, last_call_s: float) -> bool:
    """质检重试的预算门限：剩余预算须容下「上一次同量级调用 + 收尾预留」。

    口径（0.7.6 起锚定 timekeeper 全局剩余，不再是绝对已耗时公式）：
    remaining_s − 上次模型调用耗时×RETRY_CALL_HEADROOM ≥ RETRY_FINISH_RESERVE_S
    才允许重试。重试路径上历史最优版已落盘，故只需预留收尾开销（质检+写盘+
    镜像），无需为降级链再预留——重试本身永不 raise。"""
    return (remaining_s - last_call_s * RETRY_CALL_HEADROOM) >= RETRY_FINISH_RESERVE_S


def _self_check(output_dir: str, input_dir: str) -> dict | None:
    """出厂质检门（src.self_check.run）。返回 None = 质检门自身不可用/异常——
    fail-open：质检自身故障不阻断主链（可用性优先，与 _mirror「绝不影响主结果」
    同一纪律），仅 stderr 提示；正常路径返回 {"passed","score_hint","failed_atoms"}。"""
    try:
        from src.self_check import run as _sc_run
    except Exception as exc:  # noqa: BLE001  模块缺失（如残缺打包）不阻断主链
        print(f"[advisor] self_check 不可用（{type(exc).__name__}），质检跳过",
              file=sys.stderr)
        return None
    try:
        return _sc_run(output_dir, input_dir)
    except Exception as exc:  # noqa: BLE001  质检自身异常不阻断主链
        print(f"[advisor] self_check 异常（{type(exc).__name__}: {str(exc)[:200]}），"
              f"质检跳过", file=sys.stderr)
        return None


def run(prompt_text: str) -> int:
    """主入口：直出三份产物到 prompt 指定输出目录，出厂质检通过后镜像。成功返回 0。

    流程：生成+解析+postprocess+write_outputs 后、镜像前跑 src.self_check.run
    （结构层+无网事实层）：
    - passed=True → 照常镜像、返回 0；
    - passed=False → 恰好 1 次重试：预算门限内（_retry_allowed）、仅用主选模型
      （不重跑 fallback_models）、prompt 尾部注入固定格式失败清单；
    - 重试后仍 passed=False → 交付 failed_atoms 最少的历史最优版本（写盘+镜像），
      stderr/agent.log 记录失败清单，返回 0——parse_three_docs 成功即三份齐全，
      永不 raise、永不回退降级链（直出最优版质量系统性更高）。
    仅当模型调用全挂（连一次有效响应都没有；parse_three_docs 三份不齐同归此径）
    时才按既有阶梯抛 RuntimeError，由 agent.py 回退多阶段链，语义不变。
    """
    from src.input_adapter import parse_prompt  # 复用既有路径解析
    from src.pipeline import _fallback_paths
    try:  # 质检模块缺失时主链照走（_self_check 同口径 fail-open）
        from src.self_check import format_failure_notice
    except Exception:  # noqa: BLE001
        format_failure_notice = None
    started = time.monotonic()
    # 进程死线：本地常量与 timekeeper 全局死线取 min（全局时钟 = 平台 1800s 硬限
    # − 安全垫，自进程起点计时；timekeeper 不可用时退化为本地口径）。
    deadline = started + DEADLINE_S
    if timekeeper is not None:
        deadline = min(deadline, timekeeper.deadline())
    request_count = 0
    tokens: dict[str, int] | None = None
    try:
        paths = parse_prompt(prompt_text)
    except Exception:
        paths = _fallback_paths(prompt_text)
        if paths is None:
            raise RuntimeError("无法解析输入/输出路径")
    key, base = _read_env()
    gen_model, fallback_models = _advisor_models()
    bundle = collect_input(str(paths.input_dir))
    profile_no = profile_no_from_bundle(bundle)
    template = _PROMPT_FILE.read_text(encoding="utf-8")
    user_msg = template.replace("{INPUT_BUNDLE}", bundle)
    last_err: Exception | None = None

    def _accumulate(usage: dict | None) -> None:
        nonlocal tokens
        if usage is None:
            return
        if tokens is None:
            tokens = {"prompt_tokens": 0, "completion_tokens": 0,
                      "total_tokens": 0}
        for k in tokens:
            tokens[k] += usage[k]

    def _finish(model: str) -> int:
        _mirror(str(paths.output_dir), str(paths.input_dir), prompt_text)
        _log_usage(model, time.monotonic() - started, tokens, request_count)
        print(f"[advisor] OK model={model} out={paths.output_dir}", file=sys.stderr)
        return 0

    gate_failed: list[dict] = []  # 质检不过的候选版本（历史最优保留用）
    for model in (gen_model, *fallback_models):
        remaining = deadline - time.monotonic()
        if remaining < FALLBACK_RESERVE_S + MIN_ATTEMPT_BUDGET_S:
            last_err = RuntimeError(
                f"进程死线临近（剩余 {remaining:.0f}s，需为降级链预留 "
                f"{FALLBACK_RESERVE_S:.0f}s），不再发起调用，移交回退链")
            print(f"[advisor] {last_err}", file=sys.stderr)
            break
        try:
            request_count += 1  # 计 attempts：失败请求同样是一次真实调用
            t0 = time.monotonic()
            text, usage = _chat(key, base, model, user_msg,
                                timeout_s=min(TIMEOUT_S, remaining - FALLBACK_RESERVE_S),
                                deadline=deadline)
            call_s = time.monotonic() - t0
            _accumulate(usage)
            docs = parse_three_docs(text)
            write_outputs(str(paths.output_dir), docs, profile_no)
        except Exception as exc:  # noqa: BLE001  单模型失败切下一个
            last_err = exc
            print(f"[advisor] FAIL model={model} err={type(exc).__name__}: {str(exc)[:200]}",
                  file=sys.stderr)
            continue
        check = _self_check(str(paths.output_dir), str(paths.input_dir))
        if check is None or check.get("passed"):
            return _finish(model)
        print(f"[advisor] self_check FAIL model={model} "
              f"atoms={len(check['failed_atoms'])} score_hint={check.get('score_hint')}",
              file=sys.stderr)
        gate_failed.append({"model": model, "docs": docs,
                            "n_atoms": len(check["failed_atoms"]),
                            "check": check, "call_s": call_s})
        break  # 质检不过 → 恰好 1 次重试，不再轮换 fallback 模型
    if not gate_failed:
        raise RuntimeError(f"全部候选模型失败：{last_err}")

    # ---- 质检重试：恰好 1 次，仅 gen_model，预算门限内 ----
    best = min(gate_failed, key=lambda a: a["n_atoms"])
    remaining = deadline - time.monotonic()
    if _retry_allowed(remaining, best["call_s"]):
        notice = (format_failure_notice(best["check"]["failed_atoms"])
                  if format_failure_notice is not None else "")
        retry_msg = user_msg + "\n\n" + notice
        if remaining < RETRY_FINISH_RESERVE_S + MIN_ATTEMPT_BUDGET_S:
            print(f"[advisor] retry 跳过（死线临近，剩余 {remaining:.0f}s），"
                  f"保留历史最优版本", file=sys.stderr)
        else:
            try:
                request_count += 1
                text, usage = _chat(key, base, gen_model, retry_msg,
                                    timeout_s=min(TIMEOUT_S,
                                                  remaining - RETRY_RESERVE_S),
                                    deadline=deadline)
                _accumulate(usage)
                docs = parse_three_docs(text)
                write_outputs(str(paths.output_dir), docs, profile_no)
                check = _self_check(str(paths.output_dir), str(paths.input_dir))
                if check is None or check.get("passed"):
                    return _finish(gen_model)
                print(f"[advisor] self_check FAIL(重试后) model={gen_model} "
                      f"atoms={len(check['failed_atoms'])} score_hint={check.get('score_hint')}",
                      file=sys.stderr)
                gate_failed.append({"model": gen_model, "docs": docs,
                                    "n_atoms": len(check["failed_atoms"]),
                                    "check": check, "call_s": 0.0})
            except Exception as exc:  # noqa: BLE001  重试请求失败：按质检不过收尾
                print(f"[advisor] retry FAIL model={gen_model} "
                      f"err={type(exc).__name__}: {str(exc)[:200]}", file=sys.stderr)
    else:
        print("[advisor] retry 跳过（预算门限内不允许），保留历史最优版本", file=sys.stderr)

    # ---- 仍不过：交付 failed_atoms 最少的历史最优版本，返回 0 ----
    # 走到这里说明 parse_three_docs 已成功（三份文档齐全）→ 永不 raise、永不
    # 回退降级链。失败清单进 stderr/agent.log 供追查。
    best = min(gate_failed, key=lambda a: a["n_atoms"])
    write_outputs(str(paths.output_dir), best["docs"], profile_no)
    _log_gate_delivered(best["model"], best["check"])
    return _finish(best["model"])
