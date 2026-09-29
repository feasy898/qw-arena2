# -*- coding: utf-8 -*-
"""src/report_writer.py — 产出落盘与日志初始化。

契约（contracts/interfaces.md §14）：
- write_outputs：恰好写出 3 份 UTF-8（无 BOM）Markdown，命名严格一致；
- setup_logging：AGENT_LOG_DIR 下 agent.log；本地开发缺省 reports/logs/
  （AGENTS.md §10）；记录关键阶段耗时与错误，不打印完整用户资料与密钥。

组装职责说明（与任务口径的对应关系）：
- DecisionPlan → recommendation.md 的内容由 selector 生成的推荐卡全部固定字段
  （身份/适配结论/事实依据/取舍/代价与风险/购买前核验/改选条件）与未选原因表
  （五类原因+条件句）+ 综合结论承载；Markdown 渲染复用 renderer.render_recommendation
  （契约 §10，唯一渲染实现，本模块不重复实现渲染）；
- build_output_bundle 把前两份文档全文与 DecisionPlan 组装为 OutputBundle；
  write_outputs 为唯一落盘出口。任务书中「report_writer 从 DecisionPlan 组装」
  由本模块的 build_output_bundle + write_outputs 组合完成。
"""
from __future__ import annotations

import logging
import os
from typing import Union

from src.renderer import render_recommendation
from src.schemas import DecisionPlan, OutputBundle

PathLike = Union[str, os.PathLike]

OUTPUT_FILENAMES = ("user_profile.md", "product_list.md", "recommendation.md")
"""三份产出的精确命名（platform_contract.outputs）。"""

LOGGER_NAME = "agent"
LOG_FORMAT = "%(asctime)s | %(levelname)s | %(name)s | %(message)s"


def setup_logging(log_dir: PathLike) -> logging.Logger:
    """初始化 agent 日志（契约签名）。

    :param log_dir: 日志目录（AGENT_LOG_DIR；缺失时由调用方给本地缺省 reports/logs）
    :return: 名为 "agent" 的 logger（handler 写 log_dir/agent.log，UTF-8，追加写）
    """
    directory = os.fspath(log_dir)
    os.makedirs(directory, exist_ok=True)
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(logging.INFO)
    log_path = os.path.join(directory, "agent.log")
    if not any(
        isinstance(handler, logging.FileHandler)
        and os.path.abspath(getattr(handler, "baseFilename", "")) == os.path.abspath(log_path)
        for handler in logger.handlers
    ):
        handler = logging.FileHandler(log_path, encoding="utf-8")
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        logger.addHandler(handler)
    logger.propagate = False
    return logger


def log_stage(logger: logging.Logger, stage: str, started_monotonic: float) -> None:
    """记录一个关键阶段的耗时（pipeline 各阶段调用；不打印资料内容）。"""
    import time

    elapsed = time.monotonic() - float(started_monotonic)
    logger.info("阶段 %s 完成，耗时 %.2fs", stage, elapsed)


def build_output_bundle(user_profile_md: str, product_list_md: str,
                        plan: DecisionPlan) -> OutputBundle:
    """前两份文档全文 + DecisionPlan → OutputBundle（组装出口）。

    recommendation_md 由 renderer.render_recommendation 从 DecisionPlan 渲染：
    推荐结果表恰好 3 行（含异常路径空位行）、表后一句综合结论、未选原因表。
    渲染失败（如推荐卡超过 3 张）直接抛错，不静默兜底。
    """
    return OutputBundle(
        user_profile_md=user_profile_md,
        product_list_md=product_list_md,
        recommendation_md=render_recommendation(plan),
    )


def write_outputs(bundle: OutputBundle, output_dir: PathLike) -> list[str]:
    """写出三份 Markdown（唯一落盘出口，契约签名）。

    输出目录不存在则创建；已存在的三份文件覆盖写；编码 UTF-8 无 BOM；
    换行符 "\\n"（平台为 Linux）。

    :param bundle: OutputBundle（三份全文）
    :param output_dir: 输出目录
    :return: 写出的三个文件路径（str，顺序与 OUTPUT_FILENAMES 一致）
    """
    if not isinstance(bundle, OutputBundle):
        raise TypeError(f"write_outputs 需要 OutputBundle，实为 {type(bundle)!r}")
    directory = os.fspath(output_dir)
    os.makedirs(directory, exist_ok=True)
    contents = (bundle.user_profile_md, bundle.product_list_md, bundle.recommendation_md)
    written: list[str] = []
    for name, content in zip(OUTPUT_FILENAMES, contents):
        text = str(content or "")
        path = os.path.join(directory, name)
        with open(path, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        written.append(path)
    return written
