# -*- coding: utf-8 -*-
"""tests/conftest.py — 全局夹具。

timekeeper 复位：src.timekeeper 是进程级全局时钟（默认总预算 1500s，自模块
import 计时）。单测进程里它会从套件启动一直走表——套件整体运行超过阈值后，
advisor/pipeline 的死线检查会把「套件跑得久」误判为「预算耗尽」（假阳性）。
每个用例前重置为默认预算+全新起点，使各用例时间语义与全新进程一致。
"""
from __future__ import annotations

import pytest

from src import timekeeper


@pytest.fixture(autouse=True)
def _reset_timekeeper():
    timekeeper.init()
    yield
    timekeeper.init()
