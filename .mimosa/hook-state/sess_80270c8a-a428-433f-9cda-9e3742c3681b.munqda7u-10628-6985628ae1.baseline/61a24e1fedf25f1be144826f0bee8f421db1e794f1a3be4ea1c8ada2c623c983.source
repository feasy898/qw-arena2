# -*- coding: utf-8 -*-
"""tools/e2e_mock.py — mock 网关端到端验收（SPEC §14.4）。

流程：
1. 设 ``QW_FORCE_MOCK=1``（强制 mock，SPEC §11）；
2. 以 ``raw/dataset_sample/Data_for_Users`` 为资料池构造**单用户**输入目录
   （官方输入模型 = 恰 1 份用户描述；样例池含 6 份描述、且 mock 夹具
   ``profile_extraction`` 对应 ``User_Description_1``，故本脚本对用户 1 跑全链：
   复制 01–04 五源产品资料 + 仅 User_Description_1.txt）；
3. **子进程**运行 ``python agent/agent.py --prompt "…输入目录…输出目录…"``
   （与平台调用方式一致，同时覆盖 CLI/--prompt 解析/日志链路）；
4. 调 ``tools/evaluate.py`` 的 evaluate() 做全部确定性校验（JSON 报告打印到 stdout）；
5. 打印摘要；按校验结果退出 0/1。

只依赖标准库 + src/tools 确定性模块；不读写任何密钥。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

DATA_POOL = REPO_ROOT / "raw" / "dataset_sample" / "Data_for_Users"
OUT_ROOT = REPO_ROOT / "reports" / "e2e_sample_output"
USER_FILE = "User_Description_1.txt"  # mock 夹具 profile_extraction 对应的用户
SOURCE_DIRS = (
    "01_Ecommerce_Listings",
    "02_Media_Coverage_and_Reviews",
    "03_User_Feedback_and_Complaints",
    "04_Brand_Official_Sites",
)
SUBPROCESS_TIMEOUT_SECONDS = 600  # mock 全链为秒级；超时即视为失败（不掩盖）


def _reset_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def build_single_user_input(staging: Path) -> Path:
    """资料池 → 单用户输入目录（恰 1 份用户描述 + 全部五源产品资料）。"""
    staging = _reset_dir(staging)
    desc_dir = staging / "00_User_Descriptions"
    desc_dir.mkdir()
    shutil.copy2(DATA_POOL / "00_User_Descriptions" / USER_FILE, desc_dir / USER_FILE)
    for dirname in SOURCE_DIRS:
        shutil.copytree(DATA_POOL / dirname, staging / dirname)
    return staging


def run_agent(input_dir: Path, output_dir: Path) -> tuple[int, float, str, str]:
    """子进程运行 agent/agent.py --prompt（与平台调用方式一致）。"""
    prompt = (f"请根据 {input_dir} 中的数据完成消费决策任务，"
              f"将结果输出到 {output_dir}")
    env = dict(os.environ)
    env["QW_FORCE_MOCK"] = "1"  # 强制 mock（SPEC §11）；提交包内不依赖
    started = time.monotonic()
    proc = subprocess.run(
        [sys.executable, str(REPO_ROOT / "agent" / "agent.py"), "--prompt", prompt],
        cwd=str(REPO_ROOT), env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )
    elapsed = time.monotonic() - started
    return proc.returncode, elapsed, proc.stdout, proc.stderr


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    if not DATA_POOL.is_dir():
        print(f"[e2e] 样例资料池不存在：{DATA_POOL}")
        return 1

    input_dir = build_single_user_input(OUT_ROOT / "_input_user1")
    output_dir = _reset_dir(OUT_ROOT / USER_FILE.removesuffix(".txt"))
    print(f"[e2e] 输入目录（单用户构造）：{input_dir}")
    print(f"[e2e] 输出目录：{output_dir}")

    code, elapsed, stdout, stderr = run_agent(input_dir, output_dir)
    print(f"[e2e] agent/agent.py --prompt 退出码={code} 耗时={elapsed:.2f}s")
    if stdout.strip():
        print("[e2e] agent stdout 摘要：\n" + "\n".join(stdout.strip().splitlines()[-5:]))
    if stderr.strip():
        print("[e2e] agent stderr 摘要：\n" + "\n".join(stderr.strip().splitlines()[-15:]))

    from tools.evaluate import evaluate

    eval_code = evaluate(input_dir, output_dir, run_seconds=elapsed)

    ok = (code == 0 and eval_code == 0
          and all((output_dir / name).is_file()
                  for name in ("user_profile.md", "product_list.md", "recommendation.md")))
    print("=" * 72)
    print(f"[e2e] 摘要：agent 退出码={code}；evaluate 退出码={eval_code}；"
          f"三份产物齐全={ok}；总耗时={elapsed:.2f}s")
    print(f"[e2e] 结论：{'通过（mock 全链 + 全部确定性校验）' if ok else '未通过'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
