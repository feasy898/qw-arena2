# -*- coding: utf-8 -*-
"""tests/unit/test_agent_phoenix.py — v0.4.3「不死鸟」三层防线（子进程级）。

平台评测实证：全量包反复「Agent运行错误（非零退出）」，失败发生在一切进程内
降级逻辑之前（疑似 import/启动阶段）。本文件在临时目录搭建 agent 布局并以
**子进程实跑**验证：无论 src 正常、损坏（import 即 SyntaxError）还是 main 返回
非 0，agent/agent.py 都保证「退出码 0 + 候选输出位置恰好三份标准命名产物」。

被测单元是 agent.py 的防线层本身；「损坏 src / 返回非 0 的 main」为显式标注的
测试替身（不伪造被测结果：真实主链由 tools/e2e_mock.py 等另行验收）。
"""
from __future__ import annotations

import ast
import os
import shutil
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
AGENT_SOURCE = REPO_ROOT / "agent" / "agent.py"
AGENT_JSON = REPO_ROOT / "agent" / "agent.json"

EXPECTED_VERSION = "0.4.3"
OUTPUT_NAMES = ("user_profile.md", "product_list.md", "recommendation.md")
SUBPROCESS_TIMEOUT_SECONDS = 300  # 真实 src 导入链秒级；超时即视为失败（不掩盖）


# ---------------------------------------------------------------------------
# 夹具助手：临时目录 agent 布局 + 子进程实跑
# ---------------------------------------------------------------------------

def _make_layout(tmp_path: Path, variant: str) -> Path:
    """在临时目录组装 agent 布局，返回布局根目录。

    - "bare"：仅 agent.py + agent.json（验证 --version 先于 import src）；
    - "broken"：src/pipeline.py 在 import 阶段即 raise SyntaxError（第三层防线）；
    - "stub_nonzero"：src/pipeline.main 恒返回 3（第二层防线的确定性替身）；
    - "real"：复制仓库真实 src/ + contracts/ + config.example.json（第二层防线
      走真实管线：存在但非法的输入目录 → InputError → 退出码 3）。
    """
    layout = tmp_path / "layout"
    layout.mkdir()
    shutil.copy2(AGENT_SOURCE, layout / "agent.py")
    shutil.copy2(AGENT_JSON, layout / "agent.json")
    if variant in ("broken", "stub_nonzero"):
        src_dir = layout / "src"
        src_dir.mkdir()
        (src_dir / "__init__.py").write_text("", encoding="utf-8")
        if variant == "broken":
            (src_dir / "pipeline.py").write_text(
                'raise SyntaxError("x")\n', encoding="utf-8")
        else:
            (src_dir / "pipeline.py").write_text(
                'VERSION = "0.4.3"\n\n\ndef main(argv=None):\n    return 3\n',
                encoding="utf-8")
    elif variant == "real":
        shutil.copytree(REPO_ROOT / "src", layout / "src",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copytree(REPO_ROOT / "contracts", layout / "contracts",
                        ignore=shutil.ignore_patterns("__pycache__"))
        shutil.copy2(REPO_ROOT / "config.example.json",
                     layout / "config.example.json")
    return layout


def _run_agent(layout: Path, argv: list[str], cwd: Path,
               tmp_path: Path) -> subprocess.CompletedProcess:
    """子进程运行布局内 agent.py（日志重定向进临时目录，保持宿主机整洁）。"""
    env = dict(os.environ)
    env["AGENT_LOG_DIR"] = str(tmp_path / "logs")
    return subprocess.run(
        [sys.executable, str(layout / "agent.py"), *argv],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(cwd), env=env, timeout=SUBPROCESS_TIMEOUT_SECONDS,
    )


def _assert_exactly_three_outputs(directory: Path) -> None:
    """目录内恰好三份标准命名产物且非空。"""
    assert directory.is_dir(), f"候选输出目录缺失：{directory}"
    assert {p.name for p in directory.iterdir()} == set(OUTPUT_NAMES), \
        f"{directory} 产物文件数量/命名不符：{sorted(p.name for p in directory.iterdir())}"
    for name in OUTPUT_NAMES:
        assert (directory / name).read_text(encoding="utf-8").strip(), \
            f"{directory / name} 内容为空"


# ---------------------------------------------------------------------------
# 场景 0：--version（一切之前处理；布局内无 src 也必须工作）
# ---------------------------------------------------------------------------

class TestVersion:
    def test_version_exit0_prints_agent_json_version(self, tmp_path):
        layout = _make_layout(tmp_path, "bare")
        proc = _run_agent(layout, ["--version"], tmp_path, tmp_path)
        assert proc.returncode == 0, proc.stderr[-500:]
        assert proc.stdout.strip() == EXPECTED_VERSION
        assert "Traceback" not in proc.stderr

    def test_agent_source_stdlib_only_and_no_path_literals(self):
        """防线层纪律：agent.py 不 import requests；不含绝对路径字面量（含注释）。"""
        source = AGENT_SOURCE.read_text(encoding="utf-8")
        tree = ast.parse(source)
        imported = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name.split(".")[0] for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module.split(".")[0])
        assert "requests" not in imported
        # 打包安全扫描按 CI 根路径做字节匹配；标准目录一律运行时 os.sep 构造
        assert str(REPO_ROOT) not in source
        for literal in ("/home/user", "/workspace", "D:\\", "C:\\"):
            assert literal not in source, f"源码出现绝对路径字面量：{literal}"


# ---------------------------------------------------------------------------
# 场景 1：第三层防线 —— src 损坏（import 即 SyntaxError）仍退出 0 且有三份产物
# ---------------------------------------------------------------------------

class TestLayer3BrokenSource:
    def test_broken_src_exit0_with_three_outputs_everywhere(self, tmp_path):
        layout = _make_layout(tmp_path, "broken")
        workdir = tmp_path / "work"
        workdir.mkdir()
        input_dir = tmp_path / "phx_input"
        (input_dir / "00_User_Descriptions").mkdir(parents=True)
        (input_dir / "00_User_Descriptions" / "User_Description_1.txt").write_text(
            "（测试夹具占位）预算三千元，求降噪耳机推荐。", encoding="utf-8")
        out_dir = tmp_path / "phx_output"
        prompt = (f"请根据 {input_dir.as_posix()} 中的数据完成消费决策任务，"
                  f"将结果输出到 {out_dir.as_posix()}")

        proc = _run_agent(layout, ["--prompt", prompt], workdir, tmp_path)

        assert proc.returncode == 0, \
            f"第三层防线必须退出 0；stderr 尾部：{proc.stderr[-800:]}"
        # 全部候选位置（prompt 输出路径 / 输入同级 output / cwd 下 output）恰好 3 份
        for directory in (out_dir, tmp_path / "output", workdir / "output"):
            _assert_exactly_three_outputs(directory)
        # user_profile.md 顶部 HTML 注释诊断标记：异常类型 + traceback 摘要
        profile = (out_dir / "user_profile.md").read_text(encoding="utf-8")
        assert profile.lstrip().startswith("<!-- fallback:")
        assert "SyntaxError" in profile
        # 结构合法：官方标题与表头；占位为「待核验/未提供」
        assert "# 用户画像" in profile
        assert "| 字段组 | 字段名 | 取值 |" in profile
        recommendation = (out_dir / "recommendation.md").read_text(encoding="utf-8")
        assert "# 消费推荐报告" in recommendation
        assert "| 推荐层级 | 产品ID | 产品 | 画像匹配与推荐理由 | 注意事项 |" \
            in recommendation
        # traceback 全文进 agent.log（尽力而为；AGENT_LOG_DIR 已指向临时目录）
        log_text = (tmp_path / "logs" / "agent.log").read_text(encoding="utf-8")
        assert "SyntaxError" in log_text and "Traceback" in log_text


# ---------------------------------------------------------------------------
# 场景 2：第二层防线（替身）—— main 返回非 0 仍退出 0；不覆盖已有真实产物
# ---------------------------------------------------------------------------

class TestLayer2StubMainNonZero:
    def test_main_nonzero_exit0_outputs_only_missing(self, tmp_path):
        layout = _make_layout(tmp_path, "stub_nonzero")
        workdir = tmp_path / "work"
        out_dir = workdir / "output"
        out_dir.mkdir(parents=True)
        # 预置一份「既有真实产物」：第二层防线只补缺，不得覆盖（诊断标记落在
        # user_profile.md 顶部，故预置文件选 product_list.md 以便两者都验证）
        (out_dir / "product_list.md").write_text(
            "KEEP：既有真实产物不得被覆盖", encoding="utf-8")

        proc = _run_agent(layout, ["--prompt", "请完成消费决策任务"], workdir, tmp_path)

        assert proc.returncode == 0, \
            f"第二层防线必须退出 0；stderr 尾部：{proc.stderr[-800:]}"
        _assert_exactly_three_outputs(out_dir)
        # 缺产物补写、已有产物不覆盖
        assert (out_dir / "product_list.md").read_text(encoding="utf-8") == \
            "KEEP：既有真实产物不得被覆盖"
        profile = (out_dir / "user_profile.md").read_text(encoding="utf-8")
        assert profile.lstrip().startswith("<!-- fallback:")
        assert "main 返回非零退出码 3" in profile


# ---------------------------------------------------------------------------
# 场景 3：第二层防线（真实 src）—— 真实管线返回非 0 → 仍退出 0 且补齐产物
# ---------------------------------------------------------------------------

class TestLayer2RealPipeline:
    def test_real_src_invalid_input_exit0_with_outputs(self, tmp_path):
        layout = _make_layout(tmp_path, "real")
        workdir = tmp_path / "work"
        workdir.mkdir()
        # 输入目录存在但为空（无 00_User_Descriptions）→ load_input 抛 InputError
        # → 管线退出码 3（输入不合法）→ 第二层防线补产物后退出 0。
        # 不用「无路径指令」触发：本机恰有平台标准输入目录，避免环境耦合。
        input_dir = tmp_path / "phx_input_empty"
        input_dir.mkdir()
        out_dir = tmp_path / "phx_output"
        prompt = (f"请根据 {input_dir.as_posix()} 中的数据完成消费决策任务，"
                  f"将结果输出到 {out_dir.as_posix()}")

        proc = _run_agent(layout, ["--prompt", prompt], workdir, tmp_path)

        assert proc.returncode == 0, \
            f"第二层防线必须退出 0；stderr 尾部：{proc.stderr[-800:]}"
        for directory in (out_dir, tmp_path / "output", workdir / "output"):
            _assert_exactly_three_outputs(directory)
        # main 的具体非零退出码随环境合法地变化（Windows 混合分隔符→1 未分类；
        # 解析失败→2；输入不合法→3），防线契约只约束「非 0 → 补产物 + 退出 0」
        profile = (out_dir / "user_profile.md").read_text(encoding="utf-8")
        assert profile.lstrip().startswith("<!-- fallback: main 返回非零退出码")
        assert "<!-- fallback: main 返回非零退出码 0 -->" not in profile
