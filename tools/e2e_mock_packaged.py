# -*- coding: utf-8 -*-
"""tools/e2e_mock_packaged.py — 结构自检②：对**打包产物**跑 mock 端到端（本任务自检项）。

与 tools/e2e_mock.py（仓库布局验收）的差别：被测对象不是仓库 agent/agent.py，
而是 ``dist/agent/agent.py``（打包产物；``--agent`` 可改指 zip 解包目录的
agent.py，供「zip 解包后重跑」自检③复用）。流程：

1. 设 ``QW_FORCE_MOCK=1`` 强制 mock（SPEC §11；线上真实运行不依赖此变量）；
2. 以 ``raw/dataset_sample/Data_for_Users`` 为资料池构造单用户输入目录
   （复用 tools/e2e_mock.build_single_user_input，与仓库验收同一夹具口径）；
3. **子进程**运行打包产物 ``agent.py --prompt "…输入…输出…"``；
   - cwd 固定仓库根：mock 夹具按「相对 cwd → 相对包根」顺序解析
     （src/model_gateway.load_mock_fixture），tests/ 不进提交包（官方排除项），
     夹具属本地测试设施、由仓库侧提供——线上真实模式不读夹具，不受影响；
   - AGENT_LOG_DIR 指向本脚本输出目录下 logs/，避免产物日志写进 dist/；
4. 调 tools/evaluate.py 的 evaluate() 做全部确定性校验（与 e2e_mock 同口径）；
5. 打印摘要；agent 退出码 0 且 evaluate 退出码 0 且三份产物齐全 → 退出 0，否则 1。

只依赖标准库 + src/tools 确定性模块；不读写任何密钥。
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DEFAULT_AGENT = REPO_ROOT / "dist" / "agent" / "agent.py"
OUT_ROOT = REPO_ROOT / "reports" / "e2e_packaged_output"
OUTPUT_DIR_NAME = "User_1"  # 与夹具用户 User_Description_1 对应
SUBPROCESS_TIMEOUT_SECONDS = 600  # mock 全链为秒级；超时即视为失败（不掩盖）

EXPECTED_OUTPUTS = ("user_profile.md", "product_list.md", "recommendation.md")


def run_packaged_agent(agent_path: Path, input_dir: Path, output_dir: Path,
                       log_dir: Path) -> tuple[int, float, str, str]:
    """子进程运行打包产物 agent.py --prompt（与平台调用方式一致）。"""
    prompt = (f"请根据 {input_dir} 中的数据完成消费决策任务，"
              f"将结果输出到 {output_dir}")
    env = dict(os.environ)
    env["QW_FORCE_MOCK"] = "1"          # 强制 mock（SPEC §11）；提交包线上运行不依赖
    env["AGENT_LOG_DIR"] = str(log_dir)  # 产物日志独立存放，不污染 dist/
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(agent_path), "--prompt", prompt],
        cwd=str(REPO_ROOT),  # mock 夹具解析的第一候选是相对 cwd（见模块 docstring）
        env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - started
    return proc.returncode, elapsed, proc.stdout, proc.stderr


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="对打包产物跑 mock 端到端自检")
    parser.add_argument("--agent", type=Path, default=DEFAULT_AGENT,
                        help="被测 agent.py 路径（默认 dist/agent/agent.py；"
                             "zip 解包自检可指临时目录）")
    args = parser.parse_args(argv)
    agent_path = args.agent.resolve()

    if not agent_path.is_file():
        print(f"[e2e-packaged] 被测产物不存在：{agent_path}（先运行 tools/package.py）")
        return 1

    from tools.e2e_mock import build_single_user_input  # 同一夹具构造，避免两套口径

    OUT_ROOT.mkdir(parents=True, exist_ok=True)
    input_dir = build_single_user_input(OUT_ROOT / "_input_user1")
    output_dir = OUT_ROOT / OUTPUT_DIR_NAME
    if output_dir.exists():
        import shutil
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True)
    log_dir = OUT_ROOT / "logs"
    log_dir.mkdir(exist_ok=True)
    print(f"[e2e-packaged] 被测产物：{agent_path}")
    print(f"[e2e-packaged] 输入目录（单用户构造）：{input_dir}")
    print(f"[e2e-packaged] 输出目录：{output_dir}")

    code, elapsed, stdout, stderr = run_packaged_agent(
        agent_path, input_dir, output_dir, log_dir)
    print(f"[e2e-packaged] agent.py --prompt 退出码={code} 耗时={elapsed:.2f}s")
    if stdout.strip():
        print("[e2e-packaged] agent stdout 摘要：\n"
              + "\n".join(stdout.strip().splitlines()[-5:]))
    if stderr.strip():
        print("[e2e-packaged] agent stderr 摘要：\n"
              + "\n".join(stderr.strip().splitlines()[-15:]))

    from tools.evaluate import evaluate

    eval_code = evaluate(input_dir, output_dir, run_seconds=elapsed)

    ok = (code == 0 and eval_code == 0
          and all((output_dir / name).is_file() for name in EXPECTED_OUTPUTS))
    print("=" * 72)
    print(f"[e2e-packaged] 摘要：agent 退出码={code}；evaluate 退出码={eval_code}；"
          f"三份产物齐全={ok}；总耗时={elapsed:.2f}s")
    print(f"[e2e-packaged] 结论：{'通过（打包产物 mock 全链 + 全部确定性校验）' if ok else '未通过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
