# -*- coding: utf-8 -*-
"""
@File     :   conftest.py
@Desc     :   src/tests/ 全局 pytest 配置
@Note     :   提供 fake_llm fixture，自动替换真实 LLM 调用

使用方式:
    # 方式 1: 全局启用（所有测试自动使用 fake LLM）
    # 取消下方 pytest_configure 的注释即可

    # 方式 2: 按需使用（通过 --use-real-llm 标志切换）
    # pytest src/tests/ -v                        # 用 fake LLM（默认）
    # pytest src/tests/ -v --use-real-llm         # 用真实 LLM

    # 方式 3: 在单个测试文件中覆盖预设
    # from src.tests.fake_llm import register_preset
    # register_preset("intent", {"type": "MOVE", ...})
"""

from __future__ import annotations

import pytest
from typing import Generator

from src.tests.fake_llm import (
    fake_call_llm,
    fake_call_llm_stream,
    clear_presets,
    register_preset,
    FakeLLMConfig,
)


# ====================================================================
# 测试数据库隔离 — 使用独立数据库，不污染生产数据
# ====================================================================

import asyncio as _asyncio


def pytest_sessionstart(session):
    """所有测试开始前：创建 test 数据库并切换"""
    _asyncio.run(_setup_test_db())


def pytest_sessionfinish(session, exitstatus):
    """所有测试结束后：恢复生产数据库并删除 test 数据库"""
    _asyncio.run(_teardown_test_db())


async def _setup_test_db():
    """创建测试数据库 'glyphkeeper_test' 并让 PgManager 指向它"""
    from src.tools.pg_manager import PgManager
    try:
        mgr = await PgManager.get_instance()
        await mgr.start()
        await mgr.ensure_test_database("glyphkeeper_test")
    except Exception as e:
        print(f"[conftest] 测试数据库创建失败（测试将继续，但可能污染生产数据）: {e}")


async def _teardown_test_db():
    """恢复生产数据库并删除测试数据库

    注意：部分测试 fixture 会在结束后 stop() PG 实例，
    导致无法连接删除。此时跳过删除，下次测试运行时
    ensure_test_database 会自动 DROP 重建。
    """
    from src.tools.pg_manager import PgManager
    try:
        mgr = await PgManager.get_instance()
        # 如果 PG 已停止，跳过删除（下次运行会自动重建）
        if not mgr._started:
            return
        await mgr.drop_test_database("glyphkeeper_test")
    except (ConnectionRefusedError, OSError) as e:
        # PG 已停止，正常跳过
        pass
    except Exception as e:
        print(f"[conftest] 测试数据库删除失败（可忽略，下次测试会自动重建）: {e}")


# ====================================================================
# CLI 选项：--use-real-llm
# ====================================================================


def pytest_addoption(parser: pytest.Parser):
    """添加 --use-real-llm 命令行选项"""
    parser.addoption(
        "--use-real-llm",
        action="store_true",
        default=False,
        help="使用真实 LLM（默认使用 fake LLM）",
    )


# ====================================================================
# 全局 fixture：自动应用 fake LLM
# ====================================================================


@pytest.fixture(autouse=True)
def _auto_fake_llm(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch):
    """自动替换 LLM 调用为伪造版本

    默认所有测试均使用 fake LLM（毫秒级返回预设响应）。
    通过 --use-real-llm 可恢复真实 LLM 调用。

    注意: 仅 mock LLM 调用，数据库（pgembed/PostgreSQL）和向量存储（LightRAG）
    保持真实连接，相关测试开销属正常范围。
    """
    if request.config.getoption("--use-real-llm"):
        # 使用真实 LLM — 不做任何替换
        yield
        return

    # 替换 src.tools.llm_client 中的顶层函数
    monkeypatch.setattr("src.tools.llm_client.call_llm", fake_call_llm)
    monkeypatch.setattr("src.tools.llm_client.call_llm_stream", fake_call_llm_stream)

    # 也 patch 掉各个 Node 模块中的本地导入引用
    # 这些模块在 import 时做了 from ... import call_llm as _call_llm，
    # 所以需要直接替换模块级别的 _call_llm 引用
    _patch_node_modules(monkeypatch)

    yield

    # 测试结束后清理（可选）
    # clear_presets()


def _patch_node_modules(monkeypatch: pytest.MonkeyPatch):
    """Patch 各个 Node 模块中的本地 _call_llm 引用"""
    # 这些模块在 import 时做了 from ... import call_llm as _call_llm（模块级绑定），
    # 所以必须直接替换模块级 _call_llm 引用，仅 patch llm_client 是不够的。
    modules_to_patch = [
        "src.nodes.llm.intent_node",
        "src.nodes.llm.narrator_node",
        "src.nodes.llm.npc_dialogue_node",   # 新增：模块级 import _call_llm
        "src.nodes.llm.adjudicator_node",    # 新增：模块级 import _call_llm
        "src.nodes.tools.disambiguation_node",# 新增：模块级 import _call_llm
    ]
    for mod_name in modules_to_patch:
        try:
            monkeypatch.setattr(f"{mod_name}._call_llm", fake_call_llm)
        except (AttributeError, ModuleNotFoundError):
            pass  # 模块尚未导入，稍后会在 autouse 中处理


# ====================================================================
# 更灵活的 fixture：FakeLLMConfig（可定制预设）
# ====================================================================


@pytest.fixture
def fake_llm(request: pytest.FixtureRequest, monkeypatch: pytest.MonkeyPatch) -> Generator[FakeLLMConfig, None, None]:
    """可定制的 FakeLLM fixture

    在测试中覆盖预设响应:
        def test_something(fake_llm):
            fake_llm.set_preset("intent", {"type": "MOVE", "data": {"action": "去门口"}})
            ...

    或用 marker 声明预设:
        @pytest.mark.fake_llm_preset("intent", {"type": "META"})
        def test_with_marker(fake_llm):
            ...
    """
    config = FakeLLMConfig()

    # 检查 marker 预设
    for marker in request.node.iter_markers("fake_llm_preset"):
        if len(marker.args) >= 2:
            key, value = marker.args[0], marker.args[1]
            config.set_preset(key, value)
        elif len(marker.args) == 1 and isinstance(marker.args[0], dict):
            for k, v in marker.args[0].items():
                config.set_preset(k, v)

    config.apply(monkeypatch)
    yield config


# ====================================================================
# 自定义 marker 注册
# ====================================================================


def pytest_configure(config: pytest.Config):
    """注册自定义 markers"""
    config.addinivalue_line(
        "markers",
        "fake_llm_preset(key, value): 为测试设置 fake LLM 预设响应",
    )
