# -*- coding: utf-8 -*-
"""tools/e2e_real_case.py — 泛化证据包真链运行器（**本地工具，绝不打进提交包**）。

与 tools/e2e_real.py 的差异：
- 输入不是官方样例池 + 用户编号，而是 ``--case-dir`` 指向的完整五目录输入
  （00_User_Descriptions 恰 1 份 + 01–04 五源产品资料），原样复制到运行区；
- 验收尺：src.self_check 出厂质检门（goldenless，与包内同构）
  + tools/deterministic_judge.py 两层判定。

用途：对官方样例池之外的合成输入（未见画像 / 未见品类 / 矛盾需求 /
规模压力）跑真链，验证产物质量不依赖特定输入；产物与判定存
reports/generality_proof/runs/<case>/。
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.e2e_real import (  # noqa: E402
    OUTPUT_FILES,
    TOKEN_STOP_LIMIT,
    _reset_dir,
    parse_stage_timings,
    parse_usage,
    read_log,
    resolve_credentials,
    run_agent,
)

PROOF_ROOT = REPO_ROOT / "reports" / "generality_proof"
REQUIRED_DIRS = (
    "00_User_Descriptions",
    "01_Ecommerce_Listings",
    "02_Media_Coverage_and_Reviews",
    "03_User_Feedback_and_Complaints",
    "04_Brand_Official_Sites",
)


def stage_case_input(case_dir: Path, staging: Path) -> Path:
    """案例目录 → 运行输入目录（原样复制，恰 1 份用户描述）。"""
    for name in REQUIRED_DIRS:
        if not (case_dir / name).is_dir():
            raise SystemExit(f"[e2e-case] 案例缺目录 {name}：{case_dir}")
    descs = list((case_dir / "00_User_Descriptions").glob("*.txt"))
    if len(descs) != 1:
        raise SystemExit(f"[e2e-case] 用户描述须恰 1 份 .txt，实得 {len(descs)}：{case_dir}")
    # 主链只认 User_Description_ 前缀的文件名（src/advisor_pipeline.py 装配输入束的
    # 发现规则）；不符命名会被静默跳过，产物退化为「用户描述缺失」。
    if not descs[0].name.startswith("User_Description_"):
        raise SystemExit(f"[e2e-case] 用户描述文件名须以 User_Description_ 开头：{descs[0].name}")
    staging = _reset_dir(staging)
    for name in REQUIRED_DIRS:
        shutil.copytree(case_dir / name, staging / name)
    return staging


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    parser = argparse.ArgumentParser(description="泛化证据包真链运行器（本地工具）")
    parser.add_argument("--case-dir", required=True, help="案例五目录输入路径")
    parser.add_argument("--name", required=True, help="案例名（运行区子目录名）")
    args = parser.parse_args(argv)

    case_dir = Path(args.case_dir).resolve()
    if not case_dir.is_dir():
        print(f"[e2e-case] 案例目录不存在：{case_dir}")
        return 1

    run_started = time.monotonic()
    try:
        credentials = resolve_credentials()
    except SystemExit:
        return 1

    run_root = PROOF_ROOT / "runs" / args.name
    input_dir = stage_case_input(case_dir, run_root / "_input")
    output_dir = _reset_dir(run_root / "output")
    log_dir = _reset_dir(run_root / "logs")
    print(f"[e2e-case] 案例：{case_dir}")
    print(f"[e2e-case] 输入目录：{input_dir}")
    print(f"[e2e-case] 输出目录：{output_dir}")

    code, elapsed, stdout, stderr = run_agent(input_dir, output_dir, credentials, log_dir)
    print(f"[e2e-case] agent 退出码={code} 耗时={elapsed:.2f}s")
    if stderr.strip():
        print("[e2e-case] agent stderr 摘要：\n" + "\n".join(stderr.strip().splitlines()[-15:]))

    log_text = read_log(log_dir)
    for stage_name, seconds in parse_stage_timings(log_text):
        print(f"  - {stage_name}: {seconds}s")
    usage = parse_usage(log_text)
    usage_line = "未提取到"
    if usage:
        usage_line = (f"prompt={usage['prompt_tokens']} completion={usage['completion_tokens']} "
                      f"total={usage['total_tokens']} requests={usage['requests']}")
    print(f"[e2e-case] usage 汇总：{usage_line}")
    if usage and usage["total_tokens"] > TOKEN_STOP_LIMIT:
        print(f"[e2e-case] !! usage 超上限 {TOKEN_STOP_LIMIT}，停止")
        return 1

    files_ok = all((output_dir / name).is_file() for name in OUTPUT_FILES)

    from src.self_check import run as gate_run
    gate = gate_run(str(output_dir), str(input_dir))

    from tools.deterministic_judge import judge
    verdict = judge(str(output_dir), str(input_dir), label=f"case_{args.name}")

    report = {
        "case": args.name,
        "agent_rc": code,
        "elapsed_s": round(elapsed, 2),
        "usage": usage,
        "files_ok": files_ok,
        "gate": {"passed": gate["passed"], "failed_atoms": gate["failed_atoms"]},
        "judge": {"mode": verdict["mode"], "total": verdict["total"],
                  "scores": verdict["scores"], "issues": verdict["issues"]},
    }
    (run_root / "verdict.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    ok = (code == 0 and files_ok and gate["passed"] and not verdict["issues"])
    print("=" * 72)
    print(f"[e2e-case] gate passed={gate['passed']}（failed_atoms={len(gate['failed_atoms'])}）；"
          f"judge mode={verdict['mode']} total={verdict['total']} issues={len(verdict['issues'])}")
    print(f"[e2e-case] 结论：{'通过' if ok else '未通过'}（verdict.json 已写 {run_root}）")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
