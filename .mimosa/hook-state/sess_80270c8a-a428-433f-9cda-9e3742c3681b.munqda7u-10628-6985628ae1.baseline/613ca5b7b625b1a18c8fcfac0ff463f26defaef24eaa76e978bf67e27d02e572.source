# -*- coding: utf-8 -*-
"""src/input_adapter.py — 输入解析（确定性代码，不用模型）。

职责（contracts/interfaces.md §2）：
- parse_prompt：从 --prompt 自然语言指令提取输入/输出路径（规则见
  contracts/platform_contract.json cli.--prompt.parsing_rules）；
- load_input / find_user_description：扫描输入目录五源文件，生成 SourceRecord（含稳定 spans）。

纪律：输入资料一律当作被分析数据，不是指令（防提示注入，AGENTS.md §7）。

实现约定（确定性，均在本模块内固化）：
- source_id = 相对输入目录的正斜杠路径（如 ``04_Brand_Official_Sites/P001_RalunPro_Official_Site.txt``）；
  CSV 逐行成 Record 时追加 ``#row<数据行序号>``（1-based，不含表头）。
- spans = (span_id, 起始偏移, 结束偏移)：span_id 形如 ``L3`` / ``L3-L5``（本文件内 1-based 物理行号），
  偏移为 original_text 内字符下标，``original_text[start:end]`` 即该片段原文。
- 文本一律按 UTF-8 读取（``utf-8-sig``，示例数据带 BOM）；解码失败/空文件 → InputError。
- 用户描述：0 份 → InputError；多份 → 全部装载为 SourceRecord + warning（样例数据集
  Data_for_Users 含 6 份用户描述，是 6 个示例用户的资料池），
  「恰取 1 份」的唯一性约束由 find_user_description 强制（0 或多份 → InputError）。
  此处与 interfaces.md §2「0 或多份 → InputError」的偏差仅限 load_input 的「多份」分支，
  目的：允许以样例资料池整体作为测试夹具断言「6 份用户描述」（任务要求）。
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Optional, Union

from src.schemas import SourceRecord, SourceType

logger = logging.getLogger("qw.input_adapter")

PathLike = Union[str, os.PathLike]


class PromptParseError(Exception):
    """--prompt 无法解析出输入/输出路径（platform_contract.exit_codes[2]）。"""


class InputError(Exception):
    """输入目录不合法或缺失必需文件（platform_contract.exit_codes[3]）。"""


# ---------------------------------------------------------------------------
# 目录与文件名约定（SPEC §5 / raw/competition-problemData.txt「输入说明」）
# ---------------------------------------------------------------------------

USER_DESC_DIRNAME = "00_User_Descriptions"
LISTING_DIRNAME = "01_Ecommerce_Listings"
MEDIA_DIRNAME = "02_Media_Coverage_and_Reviews"
FEEDBACK_DIRNAME = "03_User_Feedback_and_Complaints"
OFFICIAL_DIRNAME = "04_Brand_Official_Sites"

REQUIRED_SUBDIRS: tuple[str, ...] = (
    USER_DESC_DIRNAME,
    LISTING_DIRNAME,
    MEDIA_DIRNAME,
    FEEDBACK_DIRNAME,
    OFFICIAL_DIRNAME,
)

USER_DESC_PREFIX = "User_Description_"
USER_DESC_SUFFIX = ".txt"

COVERAGE_FILENAME_RE = re.compile(r"^P(\d{1,3})_Product_Coverage\.txt$")
REVIEW_FILENAME_RE = re.compile(r"^P(\d{1,3})_Third_Party_Review\.txt$")
OFFICIAL_FILENAME_RE = re.compile(r"^P(\d{1,3})_(.+)_Official_Site\.txt$")

# CSV 列名（contracts/interfaces.md §4：货架/反馈均以「测试产品ID」为编号列）
COL_PRODUCT_ID = "测试产品ID"
COL_BRAND = "品牌"
COL_MODEL = "型号"
LISTING_REQUIRED_COLUMNS: tuple[str, ...] = (COL_PRODUCT_ID, COL_BRAND, COL_MODEL)
FEEDBACK_REQUIRED_COLUMNS: tuple[str, ...] = (COL_PRODUCT_ID,)


# ---------------------------------------------------------------------------
# 文件读取与 CSV 解析（load_input / SourceIndex / product_registry 共用）
# ---------------------------------------------------------------------------

@dataclass
class CsvRow:
    """一条 CSV 数据行的解析结果（表头行不在此列）。"""

    row_index: int   # 数据行序号（1-based，不含表头，跳过整行为空者）
    cells: list[str]
    raw_text: str    # 该行原文（不含行尾换行符，其余原样）
    line_start: int  # 起始物理行号（1-based）
    line_end: int    # 结束物理行号（含；带内嵌换行的字段可跨多行）


def read_text_file(path: PathLike) -> str:
    """按 UTF-8（``utf-8-sig``，容忍 BOM）读取全文。

    :raises InputError: 文件不存在/不可读，或不是有效 UTF-8。
    """
    try:
        with open(os.fspath(path), "r", encoding="utf-8-sig", newline="") as fp:
            return fp.read()
    except UnicodeDecodeError as exc:
        raise InputError(f"文件不是有效 UTF-8 编码：{path}（{exc}）") from exc
    except OSError as exc:
        raise InputError(f"文件无法读取：{path}（{exc}）") from exc


def _split_keepends(text: str) -> list[str]:
    """按 \\r\\n / \\n / \\r 切行并保留行尾（与 csv 的物理行概念一致）。"""
    lines: list[str] = []
    start = 0
    i = 0
    n = len(text)
    while i < n:
        ch = text[i]
        if ch == "\r":
            end = i + (2 if i + 1 < n and text[i + 1] == "\n" else 1)
            lines.append(text[start:end])
            start = end
            i = end
        elif ch == "\n":
            lines.append(text[start:i + 1])
            start = i + 1
            i += 1
        else:
            i += 1
    if start < n:
        lines.append(text[start:])
    return lines


def _strip_line_ending(line: str) -> str:
    if line.endswith("\r\n"):
        return line[:-2]
    if line.endswith("\n") or line.endswith("\r"):
        return line[:-1]
    return line


def read_csv_rows(path: PathLike) -> tuple[list[str], list[CsvRow]]:
    """用标准库 csv 读取 CSV（UTF-8/utf-8-sig），返回 (表头单元格, 数据行列表)。

    - 物理行号取自 csv.reader.line_num，与原文逐行对齐；
    - 整行均为空白/空单元格的行跳过（不计入数据行）；
    - 不做任何业务校验（必填列检查由调用方按信息源执行）。

    :raises InputError: 文件为空、无表头或不可读。
    """
    text = read_text_file(path)
    if not text.strip():
        raise InputError(f"CSV 文件为空：{path}")
    raw_lines = _split_keepends(text)
    reader = csv.reader(io.StringIO(text, newline=""))
    header: Optional[list[str]] = None
    rows: list[CsvRow] = []
    prev_line = 0
    for cells in reader:
        line_end = reader.line_num
        raw_body = _strip_line_ending("".join(raw_lines[prev_line:line_end]))
        if header is None:
            header = cells
        elif any(cell.strip() for cell in cells):
            rows.append(
                CsvRow(
                    row_index=len(rows) + 1,
                    cells=cells,
                    raw_text=raw_body,
                    line_start=prev_line + 1,
                    line_end=line_end,
                )
            )
        prev_line = line_end
    if header is None or not any(name.strip() for name in header):
        raise InputError(f"CSV 文件缺少表头：{path}")
    return header, rows


def csv_cells(raw_text: str) -> list[str]:
    """把单条 CSV 记录原文解析为单元格（与 read_csv_rows 同口径）。"""
    return next(iter(csv.reader([raw_text])), [])


def line_spans(text: str) -> list[tuple[str, int, int]]:
    """为整篇文本生成逐行稳定片段：(L<行号>, 起始偏移, 结束偏移)。

    行号与原文物理行一致；偏移指向 original_text（不含行尾换行符）。
    """
    spans: list[tuple[str, int, int]] = []
    offset = 0
    for line_no, line in enumerate(_split_keepends(text), start=1):
        body_len = len(_strip_line_ending(line))
        spans.append((f"L{line_no}", offset, offset + body_len))
        offset += len(line)
    return spans


def span_id_for_lines(line_start: int, line_end: int) -> str:
    """稳定片段编号：单行 ``L3``；行号区间 ``L3-L5``。"""
    if line_start == line_end:
        return f"L{line_start}"
    return f"L{line_start}-L{line_end}"


def numeric_filename_key(name: str, id_regex: Optional[re.Pattern[str]] = None) -> tuple[int, str]:
    """文件名排序键：产品编号尾号数字优先（P10 排在 P2 之后），其余按名称。"""
    if id_regex is not None:
        m = id_regex.match(name)
        if m:
            return (int(m.group(1)), name)
    m = re.match(r"^P(\d{1,3})_", name)
    if m:
        return (int(m.group(1)), name)
    return (10**9, name)


# ---------------------------------------------------------------------------
# --prompt 解析（platform_contract.json cli.--prompt.parsing_rules）
# ---------------------------------------------------------------------------

@dataclass
class PromptPaths:
    """从 --prompt 解析出的路径。"""

    input_dir: str
    output_dir: str


@dataclass
class InputBundle:
    """输入目录的完整装载结果。"""

    input_dir: str
    user_description_path: str
    user_text: str
    source_records: list[SourceRecord] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


# 带引号的路径候选（反引号/中英引号/书名号/尖括号；parsing_rules 第 2 条）
_QUOTED_PATH_RE = re.compile(
    r"`([^`]+)`"
    r"|「([^」]+)」"
    r"|『([^』]+)』"
    r"|《([^》]+)》"
    r"|[\"']([^\"']+)[\"']"
    r"|<([^>]+)>"
)

# 裸路径候选：盘符、/、\、./、../ 开头；遇空白与常见标点终止
_BARE_PATH_RE = re.compile(
    r"(?:[A-Za-z]:[\\/]|[.]{1,2}[\\/]|[\\/])"
    r"[^\s，。；、,;！？!?'\"`<>《》「」『』()\[\]（）【】]*"
)

# 语义词标记（parsing_rules 第 1 条）：(角色, 取路径方式, 正则)
_MARKER_DEFS: tuple[tuple[str, str, re.Pattern[str]], ...] = (
    ("input", "after", re.compile(r"输入目录\s*[:：]?")),
    ("input", "inner", re.compile(r"根据\s*(?P<inner>.{0,120}?)\s*中的数据", re.S)),
    ("input", "after", re.compile(r"读取")),
    ("input", "inner", re.compile(r"输入\s*(?P<inner>[^\s，。；,;]+?)\s*目录")),
    ("output", "after", re.compile(r"输出目录\s*[:：]?")),
    ("output", "after", re.compile(r"(?:将\s*)?(?:处理\s*)?结果\s*输出到")),
    ("output", "after", re.compile(r"输出到")),
    ("output", "after", re.compile(r"结果\s*写入")),
)


@dataclass
class _PathCandidate:
    start: int
    end: int
    text: str
    quoted: bool


@dataclass
class _MarkerHit:
    role: str
    kind: str           # after | inner
    start: int
    end: int            # after：标记结束位置；inner：内嵌路径搜索区间起点
    inner_end: int = -1  # 仅 inner：内嵌路径搜索区间终点


def _collect_path_candidates(prompt: str) -> list[_PathCandidate]:
    cands: list[_PathCandidate] = []
    for m in _QUOTED_PATH_RE.finditer(prompt):
        text = next(g for g in m.groups() if g is not None)
        if text.strip():
            cands.append(_PathCandidate(m.start(), m.end(), text.strip(), True))
    for m in _BARE_PATH_RE.finditer(prompt):
        text = m.group(0)
        if not text.strip():
            continue
        # 落在带引号候选区间内的裸路径视为同一候选（引号优先）
        if any(q.start <= m.start() and m.end() <= q.end for q in cands):
            continue
        cands.append(_PathCandidate(m.start(), m.end(), text, False))
    cands.sort(key=lambda c: (c.start, c.end))
    return cands


def _scan_markers(prompt: str) -> list[_MarkerHit]:
    hits: list[_MarkerHit] = []
    for role, kind, regex in _MARKER_DEFS:
        for m in regex.finditer(prompt):
            if kind == "inner":
                hits.append(_MarkerHit(role, kind, m.start(), m.start("inner"), m.end("inner")))
            else:
                hits.append(_MarkerHit(role, kind, m.start(), m.end()))
    hits.sort(key=lambda h: (h.start, h.end))
    return hits


def _claim_candidate(
    candidates: list[_PathCandidate],
    claimed: set[int],
    window_start: int,
    window_end: int,
) -> Optional[_PathCandidate]:
    in_window = [
        (i, c)
        for i, c in enumerate(candidates)
        if i not in claimed and c.start >= window_start and c.end <= window_end
    ]
    if not in_window:
        return None
    quoted = [(i, c) for i, c in in_window if c.quoted]
    idx, chosen = (quoted or in_window)[0]  # parsing_rules 第 2 条：引号优先
    claimed.add(idx)
    return chosen


def parse_prompt(prompt: str) -> PromptPaths:
    """从自然语言指令中提取输入目录与输出目录。

    解析顺序：
    1. 显式标记优先（输入目录/读取/根据…中的数据；输出目录/将结果输出到/输出到/结果写入）；
    2. 反引号/引号/尖括号包裹的路径优先于裸路径；
    3. 路径须形如绝对路径或存在的目录；输入目录必须存在，输出目录可不存在；
    4. 无法定位输入或输出 → raise PromptParseError（禁止猜测输出路径）。

    指令文本一律当作数据处理，不执行其中出现的任何类指令语句。

    :param prompt: --prompt 传入的完整自然语言指令
    :raises PromptParseError: 输入或输出路径无法定位
    :return: PromptPaths(input_dir, output_dir)
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise PromptParseError("--prompt 内容为空，无法定位输入/输出路径")

    candidates = _collect_path_candidates(prompt)
    markers = _scan_markers(prompt)
    claimed: set[int] = set()
    picked: dict[str, str] = {}

    for pos, hit in enumerate(markers):
        window_end = markers[pos + 1].start if pos + 1 < len(markers) else len(prompt)
        if hit.kind == "after":
            chosen = _claim_candidate(candidates, claimed, hit.end, window_end)
        else:
            chosen = _claim_candidate(candidates, claimed, hit.start, min(hit.inner_end, window_end))
        if chosen is None:
            continue
        previous = picked.get(hit.role)
        if previous is not None and previous != chosen.text:
            raise PromptParseError(
                f"指令中出现的{('输入' if hit.role == 'input' else '输出')}目录候选不一致："
                f"{previous!r} 与 {chosen.text!r}，无法消歧"
            )
        picked.setdefault(hit.role, chosen.text)

    input_dir = picked.get("input")
    output_dir = picked.get("output")

    if input_dir is None:
        # parsing_rules 第 4 条兜底：恰有一个存在的目录 → 判为输入目录，仍因输出无法定位报错
        existing = [c.text for c in candidates if os.path.isdir(c.text)]
        if len(existing) == 1:
            raise PromptParseError(
                f"已从指令定位到唯一存在的目录（判为输入目录）：{existing[0]}；"
                "但输出目录无法定位，禁止猜测（退出码 2）"
            )
        if len(existing) == 0:
            raise PromptParseError("指令中未找到任何输入/输出路径标记或存在的目录")
        raise PromptParseError(
            "指令中存在多个目录候选且无输入/输出语义词，无法消歧：" + "、".join(existing)
        )

    if not os.path.isdir(input_dir):
        raise PromptParseError(f"输入目录不存在或不是目录：{input_dir}")

    if output_dir is None:
        raise PromptParseError(
            f"输入目录已定位：{input_dir}；但输出目录无法从指令定位，禁止猜测（退出码 2）"
        )

    return PromptPaths(input_dir=input_dir, output_dir=output_dir)


# ---------------------------------------------------------------------------
# 输入目录装载
# ---------------------------------------------------------------------------

def _user_desc_sort_key(name: str) -> tuple[int, str]:
    m = re.fullmatch(rf"{USER_DESC_PREFIX}(\d+){re.escape(USER_DESC_SUFFIX)}", name)
    if m:
        return (int(m.group(1)), name)
    return (10**9, name)


def find_user_description(input_dir: PathLike) -> str:
    """返回用户描述文件路径（00_User_Descriptions 下，或全树发现的第一个）。

    官方约定该目录为「用户画像的唯一信息来源」，n = 1~6（正式评测可能不同）。
    0 份 → raise InputError；多份取排序第一份（v0.3.0，不再整体失败）。

    v0.4.1：标准子目录缺失时按文件名模式全树发现（与 load_input 的规范化
    装载同一口径，两处选择必然一致）。

    :param input_dir: 输入目录
    :raises InputError: 文件数为 0 或目录缺失
    :return: 用户描述文件路径（str）
    """
    root = os.fspath(input_dir)
    desc_dir = os.path.join(root, USER_DESC_DIRNAME)
    if not os.path.isdir(desc_dir):
        # v0.4.1：全树发现
        hits = [
            os.path.join(dirpath, name)
            for dirpath, _dirs, filenames in os.walk(root)
            for name in filenames
            if re.fullmatch(rf"{USER_DESC_PREFIX}\d+{re.escape(USER_DESC_SUFFIX)}", name)
        ]
        if hits:
            hits.sort(key=lambda p: _user_desc_sort_key(os.path.basename(p)))
            logger.warning(
                "输入目录缺少 %s 子目录；按文件名模式全树发现 %d 份用户描述，"
                "取第一份：%s", USER_DESC_DIRNAME, len(hits), hits[0])
            return hits[0]
        raise InputError(f"输入目录缺少必需子目录：{USER_DESC_DIRNAME}（root={root}）")
    names = sorted(
        (
            name
            for name in os.listdir(desc_dir)
            if name.startswith(USER_DESC_PREFIX)
            and name.endswith(USER_DESC_SUFFIX)
            and os.path.isfile(os.path.join(desc_dir, name))
        ),
        key=_user_desc_sort_key,
    )
    if len(names) == 0:
        raise InputError(
            f"{USER_DESC_DIRNAME} 下未找到任何用户描述文件"
            f"（期望恰 1 个 {USER_DESC_PREFIX}*{USER_DESC_SUFFIX}）"
        )
    if len(names) > 1:
        # v0.3.0（评测联调）：多份用户描述不再整体失败（原退出码 3 = 整场 0 分）。
        # 官方机测 Prompt 的输入说明写「User_Description_n.txt, n = 1~6」，评测
        # 输入形态存在多份可能；按文件名排序取第一份继续（画像字段唯一信息来源
        # 即该文件），多份本身即记入日志告警。0 份仍为 InputError（无从画像）。
        logger.warning(
            "%s 下存在 %d 份用户描述文件（%s），正式评测应恰含 1 份；"
            "按文件名排序取第一份（%s）继续（v0.3.0 健壮性：不因多份整体失败）",
            USER_DESC_DIRNAME, len(names), "、".join(names), names[0])
    return os.path.join(desc_dir, names[0])


def _make_text_record(
    subdir: str,
    name: str,
    path: str,
    source_type: SourceType,
    text: str,
) -> SourceRecord:
    return SourceRecord(
        source_id=f"{subdir}/{name}",
        source_type=source_type,
        original_text=text,
        spans=line_spans(text),
        path=path,
    )


def _load_user_descriptions(
    root: str, records: list[SourceRecord], warnings: list[str]
) -> None:
    desc_dir = os.path.join(root, USER_DESC_DIRNAME)
    entries = sorted(os.listdir(desc_dir))
    names = [
        name
        for name in entries
        if name.startswith(USER_DESC_PREFIX)
        and name.endswith(USER_DESC_SUFFIX)
        and os.path.isfile(os.path.join(desc_dir, name))
    ]
    others = [name for name in entries if name not in set(names)]
    if others:
        warnings.append(
            f"{USER_DESC_DIRNAME} 存在不符命名约定的条目，已忽略：{'、'.join(sorted(others))}"
        )
    if not names:
        raise InputError(
            f"{USER_DESC_DIRNAME} 下未找到任何用户描述文件"
            f"（期望 {USER_DESC_PREFIX}*{USER_DESC_SUFFIX}）"
        )
    names.sort(key=_user_desc_sort_key)
    for name in names:
        path = os.path.join(desc_dir, name)
        text = read_text_file(path)
        if not text.strip():
            raise InputError(f"用户描述文件为空（用户画像唯一信息来源不可为空）：{path}")
        records.append(_make_text_record(USER_DESC_DIRNAME, name, path, SourceType.USER_DESCRIPTION, text))
    if len(names) != 1:
        warnings.append(
            f"{USER_DESC_DIRNAME} 含 {len(names)} 份用户描述（正式评测恰为 1 份）；"
            "v0.3.0 健壮性：已按文件名排序取第一份作为单用户输入（不再整体失败），"
            "全部来源记录仍装载"
        )


def _load_csv_dir(
    root: str,
    dirname: str,
    source_type: SourceType,
    required_columns: tuple[str, ...],
    records: list[SourceRecord],
    warnings: list[str],
) -> None:
    dir_path = os.path.join(root, dirname)
    entries = sorted(os.listdir(dir_path))
    csv_names = [name for name in entries if name.lower().endswith(".csv")]
    others = [name for name in entries if name not in set(csv_names)]
    if others:
        warnings.append(f"{dirname} 存在非 CSV 条目，已忽略：{'、'.join(sorted(others))}")
    if not csv_names:
        raise InputError(f"{dirname} 下未找到任何 CSV 文件（缺必需输入）")
    for name in csv_names:
        path = os.path.join(dir_path, name)
        header, rows = read_csv_rows(path)
        rel = f"{dirname}/{name}"
        missing = [col for col in required_columns if col not in header]
        if missing:
            raise InputError(f"CSV 缺少必需列 {'、'.join(missing)}：{path}（表头：{'、'.join(header)}）")
        id_idx = header.index(COL_PRODUCT_ID)
        for row in rows:
            if len(row.cells) != len(header):
                warnings.append(
                    f"{rel}#row{row.row_index} 列数（{len(row.cells)}）与表头（{len(header)}）不一致"
                )
            if id_idx < len(row.cells) and not row.cells[id_idx].strip():
                warnings.append(f"{rel}#row{row.row_index}「{COL_PRODUCT_ID}」为空，未参与产品编号关联")
            records.append(
                SourceRecord(
                    source_id=f"{rel}#row{row.row_index}",
                    source_type=source_type,
                    original_text=row.raw_text,
                    spans=[
                        (span_id_for_lines(row.line_start, row.line_end), 0, len(row.raw_text))
                    ],
                    path=path,
                )
            )


def _load_media_dir(root: str, records: list[SourceRecord], warnings: list[str]) -> None:
    dir_path = os.path.join(root, MEDIA_DIRNAME)
    classified: dict[str, list[str]] = {
        SourceType.MEDIA_COVERAGE: [],
        SourceType.THIRD_PARTY_REVIEW: [],
    }
    others: list[str] = []
    for name in sorted(os.listdir(dir_path)):
        if COVERAGE_FILENAME_RE.match(name):
            classified[SourceType.MEDIA_COVERAGE].append(name)
        elif REVIEW_FILENAME_RE.match(name):
            classified[SourceType.THIRD_PARTY_REVIEW].append(name)
        else:
            others.append(name)
    if others:
        warnings.append(f"{MEDIA_DIRNAME} 存在不符命名约定的文件，已忽略：{'、'.join(sorted(others))}")
    for source_type in (SourceType.MEDIA_COVERAGE, SourceType.THIRD_PARTY_REVIEW):
        names = sorted(
            classified[source_type], key=lambda n: numeric_filename_key(n, COVERAGE_FILENAME_RE)
        )
        for name in names:
            path = os.path.join(dir_path, name)
            text = read_text_file(path)
            if not text.strip():
                raise InputError(f"资料文件为空（若确无该源资料应删除文件而非留空）：{path}")
            records.append(_make_text_record(MEDIA_DIRNAME, name, path, source_type, text))


def _load_official_dir(root: str, records: list[SourceRecord], warnings: list[str]) -> None:
    dir_path = os.path.join(root, OFFICIAL_DIRNAME)
    names: list[str] = []
    others: list[str] = []
    for name in sorted(os.listdir(dir_path)):
        if OFFICIAL_FILENAME_RE.match(name):
            names.append(name)
        else:
            others.append(name)
    if others:
        warnings.append(f"{OFFICIAL_DIRNAME} 存在不符命名约定的文件，已忽略：{'、'.join(sorted(others))}")
    names.sort(key=lambda n: numeric_filename_key(n, OFFICIAL_FILENAME_RE))
    for name in names:
        path = os.path.join(dir_path, name)
        text = read_text_file(path)
        if not text.strip():
            raise InputError(f"官网资料文件为空（若确无该源资料应删除文件而非留空）：{path}")
        records.append(_make_text_record(OFFICIAL_DIRNAME, name, path, SourceType.OFFICIAL_SITE, text))


def _normalize_input_root(root: str) -> tuple[str, Optional[str], list[str]]:
    """v0.4.1（评测联调）：输入目录形状容差。

    标准五子目录齐备 → 原样返回。缺失时：按文件名模式全树发现（各源文件名
    约定唯一、与目录位置无关），把发现的文件**符号链接**（Linux 评测环境）或
    **复制**（Windows 开发环境回退）进规范化临时目录，复用全部既有加载逻辑。
    发现不到任何可归类文件 → 返回原 root（让标准路径给出精确报错）。

    :return: (生效 root, 规范化临时目录或 None, warnings)
    """
    missing = [d for d in REQUIRED_SUBDIRS if not os.path.isdir(os.path.join(root, d))]
    if not missing:
        return root, None, []
    warnings = [f"输入目录缺少标准子目录（{'、'.join(missing)}）；已按文件名模式全树发现并规范化装载"]

    patterns = {
        USER_DESC_DIRNAME: re.compile(rf"{USER_DESC_PREFIX}\d+{re.escape(USER_DESC_SUFFIX)}$"),
        LISTING_DIRNAME: re.compile(r".*Listing_Snapshot\.csv$", re.IGNORECASE),
        MEDIA_DIRNAME: re.compile(r"P\d+_(Product_Coverage|Third_Party_Review)\.txt$"),
        FEEDBACK_DIRNAME: re.compile(r"User_Feedback.*\.csv$", re.IGNORECASE),
        OFFICIAL_DIRNAME: re.compile(r"P\d+_.+_Official_Site\.txt$"),
    }
    found: dict[str, list[str]] = {d: [] for d in REQUIRED_SUBDIRS}
    for dirpath, _dirnames, filenames in os.walk(root):
        for name in filenames:
            for dirname, pattern in patterns.items():
                if pattern.match(name):
                    found[dirname].append(os.path.join(dirpath, name))
    if not any(found.values()):
        return root, None, warnings

    import tempfile
    norm_root = tempfile.mkdtemp(prefix="qw_input_norm_")
    for dirname, paths in found.items():
        os.makedirs(os.path.join(norm_root, dirname), exist_ok=True)
        for src_path in paths:
            dst = os.path.join(norm_root, dirname, os.path.basename(src_path))
            try:
                os.symlink(os.path.abspath(src_path), dst)
            except (OSError, NotImplementedError):
                import shutil
                shutil.copy2(src_path, dst)
    return norm_root, norm_root, warnings


def load_input(input_dir: PathLike) -> InputBundle:
    """扫描输入目录，装载用户描述与五源产品资料。

    生成 SourceRecord 列表：source_id 稳定（相对路径，CSV 行追加 #rowN）、
    source_type 取 schemas.SourceType、original_text 为原文（UTF-8，容忍 BOM）、
    spans 为稳定片段编号（span_id=物理行号，偏移指向 original_text）。
    货架/反馈 CSV 逐行成 Record。

    v0.4.1（评测联调）：标准五子目录缺失时按文件名模式全树发现并规范化装载
    （评测输入的真实形状无法预知，快速失败 = 整场 0 分）。

    :param input_dir: 输入目录（如 /home/user/ws/input）
    :raises InputError: 目录不合法、必需文件缺失、文件为空或编码非法
    :return: InputBundle
    """
    root = os.fspath(input_dir)
    if not os.path.isdir(root):
        raise InputError(f"输入目录不存在或不是目录：{root}")
    root, _temp_root, norm_warnings = _normalize_input_root(root)

    warnings: list[str] = []
    warnings.extend(norm_warnings)
    records: list[SourceRecord] = []
    _load_user_descriptions(root, records, warnings)
    _load_csv_dir(
        root, LISTING_DIRNAME, SourceType.ECOMMERCE_LISTING,
        LISTING_REQUIRED_COLUMNS, records, warnings,
    )
    _load_media_dir(root, records, warnings)
    _load_csv_dir(
        root, FEEDBACK_DIRNAME, SourceType.USER_FEEDBACK,
        FEEDBACK_REQUIRED_COLUMNS, records, warnings,
    )
    _load_official_dir(root, records, warnings)

    user_records = [r for r in records if r.source_type is SourceType.USER_DESCRIPTION]
    if len(user_records) >= 1:
        # v0.3.0（评测联调）：多份用户描述取排序第一份（与 find_user_description
        # 的容忍口径一致——官方机测 Prompt 输入说明写「n = 1~6」，评测输入形态
        # 存在多份可能；原「多份即空」会让主链在用户描述为空处失败）。
        # 排序键与 find_user_description 相同，两处选择必然一致。
        user_records_sorted = sorted(user_records, key=lambda r: _user_desc_sort_key(
            os.path.basename(r.path or "")))
        user_description_path = user_records_sorted[0].path or ""
        user_text = user_records_sorted[0].original_text
    else:
        user_description_path = ""
        user_text = ""

    return InputBundle(
        input_dir=root,
        user_description_path=user_description_path,
        user_text=user_text,
        source_records=records,
        warnings=warnings,
    )
