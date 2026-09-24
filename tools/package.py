# -*- coding: utf-8 -*-
"""tools/package.py — 组装提交包 dist/agent/ 并打包为 dist/agent.zip（二阶段，SPEC §2）。

产物布局（raw/Agent本地验证指南.md「目录结构示例」；contracts/platform_contract.json
runtime.package：dir=agent、format=zip、max_size_mb=100、所有依赖打进包）：

    agent/
    ├── agent.py              # 入口（仓库 agent/agent.py 原样；自带 lib/ sys.path 自举）
    ├── agent.json            # {"runtime":"python","version":"0.1.0"}（与 --version 一致）
    ├── requirements.txt      # 依赖声明（requests）
    ├── config.example.json   # 运行所需：resolve_config 读取包根该文件（校准后 default_model）
    ├── contracts/            # 运行所需：platform_contract.json（白名单）、field_catalog.json
    │                         #   （抽取器/网关运行期读取）；rules.json/interfaces.md 为契约文档随包
    ├── prompts/              # 运行所需：profile_extraction.md、product_extraction.md
    ├── src/                  # 业务代码（管线全部 16 模块）
    └── lib/                  # vendored 依赖：pip install --platform manylinux2014_x86_64
                             #   --python-version 3.12 --target lib --only-binary=:all: requests

排除（官方规则 + 本任务打包要求）：.secrets/、reports/、raw/、tests/、tools/、
__pycache__/*.pyc、lib 的 bin/ 脚本（含解释器绝对路径，运行期不需要）。

安全自检（本脚本内程序判定，任一命中即失败退出 1）：
- 逐文件扫描密钥：.secrets/ 下全部文件内容（含 API Key 原文）不得出现在包内任何文件；
  另扫「sk-」形token样、「edge_cookies」「dashscope_api_key」等敏感标记；
- 逐文件扫描绝对路径泄漏：仓库根的 Windows/POSIX 两种写法、lib/bin 类 shebang 残留；
- 包内不得出现 __pycache__、*.pyc、.pyo；agent.json version 必须与 src/pipeline.py
  的 VERSION 常量一致。

构建后冒烟：子进程在「中立 cwd」（dist/ 目录，非仓库根）运行包内 agent.py --version，
要求输出 0.1.0 且退出码 0（证明包内布局自洽、不依赖仓库根 cwd）。
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DIST = REPO_ROOT / "dist"
PKG = DIST / "agent"
ZIP_PATH = DIST / "agent.zip"
MAX_ZIP_BYTES = 100 * 1024 * 1024  # ≤100MB（SPEC §2 / platform_contract.runtime.package）

# 顶层复制清单：仓库相对路径 → 包内相对路径（文件或目录）
COPY_ITEMS: list[tuple[str, str]] = [
    ("agent/agent.py", "agent.py"),
    ("agent/agent.json", "agent.json"),
    ("agent/requirements.txt", "requirements.txt"),
    ("config.example.json", "config.example.json"),
    ("src", "src"),
    ("prompts", "prompts"),
    ("contracts", "contracts"),
]
# 目录复制时跳过的条目名 / 文件后缀
EXCLUDE_DIR_NAMES = {"__pycache__", ".pytest_cache", ".git"}
EXCLUDE_SUFFIXES = (".pyc", ".pyo", ".pyd")
# 平台参数（raw/Agent本地验证指南.md「跨平台依赖打包」；线上 linux/amd64 Debian 12 Python 3.12）
LIB_PLATFORM = "manylinux2014_x86_64"
LIB_PYTHON_VERSION = "3.12"
# 固定 zip 内时间戳（可复现构建：同内容 → 同字节）
ZIP_DATE_TIME = (2026, 9, 24, 0, 0, 0)


def _reset_dir(path: Path) -> Path:
    if path.exists():
        shutil.rmtree(path)
    path.mkdir(parents=True)
    return path


def _copy_tree(src: Path, dst: Path) -> int:
    """递归复制目录，跳过排除项；返回复制的文件数。"""
    count = 0
    for item in sorted(src.iterdir()):
        if item.is_dir():
            if item.name in EXCLUDE_DIR_NAMES:
                continue
            count += _copy_tree(item, dst / item.name)
        else:
            if item.suffix.lower() in EXCLUDE_SUFFIXES:
                continue
            dst.mkdir(parents=True, exist_ok=True)
            shutil.copy2(item, dst / item.name)
            count += 1
    return count


def assemble_package() -> dict:
    """清空并组装 dist/agent/（不含 lib）；返回 {复制文件数, 顶层清单}。"""
    DIST.mkdir(parents=True, exist_ok=True)
    if PKG.exists():
        shutil.rmtree(PKG)
    PKG.mkdir(parents=True)
    total = 0
    for repo_rel, pkg_rel in COPY_ITEMS:
        src = REPO_ROOT / repo_rel
        if not src.exists():
            raise SystemExit(f"[package] 源缺失：{src}")
        dst = PKG / pkg_rel
        if src.is_dir():
            total += _copy_tree(src, dst)
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)
            total += 1
    return {"copied_files": total}


def install_lib() -> str:
    """按官方跨平台命令把 requests 及其依赖装进 dist/agent/lib；返回 pip 摘要。"""
    target = PKG / "lib"
    cmd = [
        sys.executable, "-m", "pip", "install",
        # manylinux2014_x86_64 与 manylinux_2_17_x86_64 是同一平台（PEP 600：manylinux2014
        # 即 glibc≥2.17）；只给旧别名时 pip 25 收不下仅带新标签的现代 wheel，会一路回退到
        # 2021 年的 requests 2.25.1，故两个等价标签都传，目标平台不变。
        "--platform", LIB_PLATFORM,
        "--platform", "manylinux_2_17_x86_64",
        "--python-version", LIB_PYTHON_VERSION,
        "--target", str(target),
        "--only-binary=:all:",
        "--retries", "5",
        "--timeout", "60",
        "-r", str(PKG / "requirements.txt"),
    ]
    print("[package] $ " + " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", cwd=str(DIST))
    if proc.returncode != 0:
        print(proc.stdout)
        print(proc.stderr, file=sys.stderr)
        raise SystemExit(f"[package] pip 安装 lib 失败（退出码 {proc.returncode}）")
    # bin/ 内是带解释器绝对路径的 CLI 脚本（如 normalizer），运行期 import 不需要
    bin_dir = target / "bin"
    if bin_dir.is_dir():
        shutil.rmtree(bin_dir)
    # pip 产物里的 __pycache__/*.pyc 一律剔除
    _strip_cruft(target)
    summary = [line for line in proc.stdout.splitlines() if "Successfully installed" in line]
    return summary[0] if summary else "pip install 完成（无 Successfully installed 行）"


def _strip_cruft(root: Path) -> None:
    for path in list(root.rglob("__pycache__")) + list(root.rglob(".pytest_cache")):
        if path.is_dir():
            shutil.rmtree(path, ignore_errors=True)
    for path in root.rglob("*"):
        if path.is_file() and path.suffix.lower() in EXCLUDE_SUFFIXES:
            path.unlink()


def build_zip() -> dict:
    """dist/agent/ → dist/agent.zip（顶层目录名=agent），返回 {大小MB, 文件数, 剔除数}。

    编译残留（__pycache__/*.pyc）在此处再过滤一道：冒烟/自检运行会在打包目录里
    重新生成字节码，不能混进提交包（即便打包前已 _strip_cruft，也双保险）。
    """
    if ZIP_PATH.exists():
        ZIP_PATH.unlink()
    all_files = sorted(p for p in PKG.rglob("*") if p.is_file())
    rels, skipped = [], 0
    for p in all_files:
        if "__pycache__" in p.parts or p.suffix.lower() in EXCLUDE_SUFFIXES:
            skipped += 1
            continue
        rels.append(p.relative_to(PKG).as_posix())
    with zipfile.ZipFile(ZIP_PATH, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for rel in rels:
            info = zipfile.ZipInfo(f"agent/{rel}", date_time=ZIP_DATE_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            zf.writestr(info, (PKG / rel).read_bytes())
    size = ZIP_PATH.stat().st_size
    with zipfile.ZipFile(ZIP_PATH) as zf:
        count = sum(1 for n in zf.namelist() if not n.endswith("/"))
    if size > MAX_ZIP_BYTES:
        raise SystemExit(f"[package] ZIP 超限：{size} bytes > {MAX_ZIP_BYTES}（100MB）")
    return {"zip_bytes": size, "file_count": count, "skipped_cruft": skipped}


# ---------------------------------------------------------------------------
# 安全自检（密钥 / 绝对路径 / 编译残留 / 版本一致）
# ---------------------------------------------------------------------------

def _collect_secret_materials() -> list[tuple[str, bytes]]:
    """读取 .secrets/ 全部文件内容作为禁入材料（只用于扫描比对，绝不打印）。"""
    materials: list[tuple[str, bytes]] = []
    secrets_dir = REPO_ROOT / ".secrets"
    if secrets_dir.is_dir():
        for path in sorted(secrets_dir.rglob("*")):
            if path.is_file():
                try:
                    materials.append((f".secrets/{path.name}", path.read_bytes()))
                except OSError:
                    pass
    return materials


def security_scan() -> dict:
    """逐文件扫描包内容：密钥、敏感标记、绝对路径、编译残留、版本一致性。"""
    violations: list[str] = []
    secrets = _collect_secret_materials()
    forward = str(REPO_ROOT).replace("\\", "/")            # D:/workspace/qw-arena2
    posix_style = "/" + forward.replace(":", "", 1)        # /D/workspace/qw-arena2
    posix_style_lower = "/" + forward[0].lower() + forward[1:].replace(":", "", 1)
    root_variants = {str(REPO_ROOT).encode(), forward.encode(),
                     posix_style.encode(), posix_style_lower.encode()}
    sensitive_markers = [b"edge_cookies", b"dashscope_api_key", b".secrets"]
    sk_token_re = re.compile(rb"sk-[A-Za-z0-9_\-]{16,}")  # 「sk-」形 API Key 样（长 token 才报，避误报）
    files = [p for p in PKG.rglob("*") if p.is_file()]
    for path in files:
        rel = path.relative_to(REPO_ROOT).as_posix()
        if "__pycache__" in path.parts or path.suffix.lower() in EXCLUDE_SUFFIXES:
            violations.append(f"编译残留混入包内：{rel}")
            continue
        data = path.read_bytes()
        for label, blob in secrets:
            # 非空且足够长的密钥材料按原字节比对（base_url/models.json 属配置非密钥，
            # 但一律按禁入材料处理最保守；命中即报）
            if len(blob) >= 8 and blob in data:
                violations.append(f"{rel} 含密钥材料片段（来自 {label}）")
        for marker in sensitive_markers:
            if marker in data:
                violations.append(f"{rel} 含敏感标记 {marker.decode(errors='replace')!r}")
        if sk_token_re.search(data):
            violations.append(f"{rel} 含 sk- 形密钥 token 样片段")
        for i, variant in enumerate(root_variants):
            if variant and variant in data:
                violations.append(f"{rel} 含仓库根绝对路径（形态{i}）")
    # agent.json 版本一致性（须与 src/pipeline.py VERSION 相同；--version 自检另行实跑）
    agent_json = json.loads((PKG / "agent.json").read_text(encoding="utf-8"))
    pipeline_src = (PKG / "src" / "pipeline.py").read_text(encoding="utf-8")
    m = re.search(r'^VERSION = "([^"]+)"', pipeline_src, re.MULTILINE)
    if not m:
        violations.append("src/pipeline.py 未找到 VERSION 常量")
    elif str(agent_json.get("version")) != m.group(1):
        violations.append(
            f"agent.json version={agent_json.get('version')!r} 与 src VERSION={m.group(1)!r} 不一致")
    if agent_json.get("runtime") != "python":
        violations.append(f"agent.json runtime={agent_json.get('runtime')!r} 应为 'python'")
    if not (PKG / "requirements.txt").is_file():
        violations.append("包根缺 requirements.txt")
    for required in ("agent.py", "lib", "src", "prompts", "contracts"):
        if not (PKG / required).exists():
            violations.append(f"包根缺 {required}/")
    return {"file_count": len(files), "violations": violations}


def smoke_version() -> str:
    """中立 cwd 下实跑包内 agent.py --version（证明不依赖仓库根 cwd 与布局）。"""
    proc = subprocess.run(
        [sys.executable, str(PKG / "agent.py"), "--version"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(DIST), timeout=120,
    )
    output = (proc.stdout or "").strip()
    expected = json.loads((PKG / "agent.json").read_text(encoding="utf-8"))["version"]
    if proc.returncode != 0 or output != expected:
        raise SystemExit(
            f"[package] --version 冒烟失败：退出码={proc.returncode} 输出={output!r} "
            f"stderr={(proc.stderr or '')[-500:]}")
    return output


def main(argv: list[str] | None = None) -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(f"[package] 仓库根：{REPO_ROOT}")
    print(f"[package] 目标：{PKG} → {ZIP_PATH}（≤{MAX_ZIP_BYTES // (1024 * 1024)}MB）")

    copied = assemble_package()
    print(f"[package] 顶层复制完成：{copied['copied_files']} 个文件（不含 lib）")

    pip_line = install_lib()
    print(f"[package] lib 安装：{pip_line}")

    # 先冒烟再清理再扫描：--version 实跑会在打包目录里生成 __pycache__，
    # 必须在其后剔除，保证安全扫描与 zip 反映最终状态
    version = smoke_version()
    print(f"[package] --version 冒烟（中立 cwd）：{version} 退出码 0")
    _strip_cruft(PKG)

    scan = security_scan()
    if scan["violations"]:
        print("[package] 安全自检未通过：", file=sys.stderr)
        for v in scan["violations"]:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print(f"[package] 安全自检通过：{scan['file_count']} 个文件无密钥/绝对路径/编译残留；"
          f"agent.json 与 src VERSION 一致")

    info = build_zip()
    size_mb = info["zip_bytes"] / (1024 * 1024)
    print("=" * 72)
    print(f"[package] 完成：{ZIP_PATH}")
    print(f"[package] ZIP 大小：{size_mb:.2f} MB（{info['zip_bytes']} bytes，≤100MB 达标）")
    print(f"[package] ZIP 文件数：{info['file_count']}"
          f"（zip 阶段再剔除编译残留 {info['skipped_cruft']} 个）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
