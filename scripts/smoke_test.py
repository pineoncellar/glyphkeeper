# -*- coding: utf-8 -*-
"""
@File     :   smoke_test.py
@Desc     :   冒烟测试 — 模拟完整游戏流程，验证各层协作正确
@Note     :   使用预设输入模拟玩家操作，不依赖 LLM 和数据库

使用方式:
    uv run python scripts/smoke_test.py
"""

import asyncio
import sys
import time
from pathlib import Path

# 确保项目根在 sys.path
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


# ── 颜色常量（移植自 cli.py，避免导入依赖） ──
_RESET = "\033[0m"
_BOLD = "\033[1m"
_DIM = "\033[2m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_CYAN = "\033[36m"
_RED = "\033[31m"


def _c(text: str, code: str) -> str:
    return f"{code}{text}{_RESET}" if sys.stdout.isatty() else text


def _ok(msg: str):
    print(f"  {_c('[OK]', _GREEN)} {msg}")


def _fail(msg: str):
    print(f"  {_c('[FAIL]', _RED)} {msg}")


def _info(msg: str):
    print(f"  {_c('[INFO]', _DIM)} {msg}")


async def run_smoke():
    """冒烟测试主流程"""
    print()
    print(_c("=" * 56, _CYAN))
    print(_c("  GlyphKeeper 冒烟测试", _BOLD))
    print(_c("=" * 56, _CYAN))
    print()

    passed = 0
    failed = 0
    total_start = time.time()

    # ── 步骤 1: 导入所有核心模块 ──
    print(_c("┃ 1/6  核心模块导入", _BOLD))
    try:
        from src.graph.keeper_graph import keeper_graph
        from src.runtime.engine import GraphEngine
        from src.runtime.scheduler import InputScheduler
        from src.state.game_state import create_initial_state
        from src.domain.coc_rules import (
            SuccessLevel, Difficulty,
            determine_success_level,
        )
        from src.domain.checks import skill_check
        from src.tools.dice import roll_d100, roll_dice
        _ok("所有核心模块导入成功")
        passed += 1
    except Exception as e:
        _fail(f"模块导入失败: {e}")
        failed += 1
        return passed, failed
    print()

    # ── 步骤 2: 验证 main graph 可编译 ──
    print(_c("┃ 2/6  Graph 编译", _BOLD))
    try:
        g = keeper_graph
        assert g is not None, "keeper_graph 返回 None"
        _ok(f"keeper_graph 编译完成 (节点数: {len(g.nodes)})")
        passed += 1
    except Exception as e:
        _fail(f"Graph 编译失败: {e}")
        failed += 1
    print()

    # ── 步骤 3: Engine 与 Scheduler 初始化 ──
    print(_c("┃ 3/6  Engine/Scheduler 初始化", _BOLD))
    try:
        engine = GraphEngine(keeper_graph, mode="langgraph")
        scheduler = InputScheduler(engine)
        assert engine is not None
        assert scheduler is not None
        _ok("GraphEngine + InputScheduler 初始化成功")
        passed += 1
    except Exception as e:
        _fail(f"Engine 初始化失败: {e}")
        failed += 1
        return passed, failed

    try:
        state = create_initial_state("smoke-test")
        assert state["session_id"] == "smoke-test"
        assert state["status"] == "active"
        assert state["game_phase"] == "exploration"
        _ok(f"初始 GameState 创建成功 ({len(state)} 个字段)")
        passed += 1
    except Exception as e:
        _fail(f"GameState 创建失败: {e}")
        failed += 1
    print()

    # ── 步骤 4: 多轮游戏交互 ──
    print(_c("┃ 4/6  多轮游戏交互", _BOLD))

    test_inputs = [
        ("搜索房间", "探索指令"),
        ("打开抽屉", "物理交互"),
        ("查看状态", "元操作"),
    ]

    for input_text, desc in test_inputs:
        try:
            narrative = await scheduler.submit("smoke-test", input_text)
            assert narrative, f"输入 '{input_text}' 返回空叙事"
            assert len(narrative) > 5, f"叙事文本过短: {narrative}"
            _ok(f"[{desc}] '{input_text}' → 叙事 ({len(narrative)} 字符)")
            passed += 1
        except Exception as e:
            _fail(f"[{desc}] '{input_text}' 执行失败: {e}")
            failed += 1

    # 验证 state 持久化
    try:
        final_state = scheduler.get_session_state("smoke-test")
        assert final_state is not None, "状态丢失"
        assert final_state["beat_counter"] >= len(test_inputs), \
            f"beat_counter ({final_state['beat_counter']}) 应 >= {len(test_inputs)}"
        _ok(f"多轮 state 持久化: beat={final_state['beat_counter']}")
        passed += 1
    except Exception as e:
        _fail(f"state 持久化验证失败: {e}")
        failed += 1
    print()

    # ── 步骤 5: 技能检定逻辑 ──
    print(_c("┃ 5/6  规则内核校验", _BOLD))
    try:
        from src.domain.coc_rules import determine_success_level
        from src.domain.checks import skill_check

        # CRITICAL
        lv = determine_success_level(50, 1)
        assert lv == SuccessLevel.CRITICAL, f"预期 CRITICAL, 实际 {lv}"
        _ok("D100=1 → CRITICAL")

        # FUMBLE
        lv = determine_success_level(30, 100)
        assert lv == SuccessLevel.FUMBLE, f"预期 FUMBLE, 实际 {lv}"
        _ok("D100=100(>30) → FUMBLE")

        # REGULAR
        lv = determine_success_level(50, 40)
        assert lv == SuccessLevel.REGULAR, f"预期 REGULAR, 实际 {lv}"
        _ok("D100=40(≤50) → REGULAR")

        # HARD
        lv = determine_success_level(50, 20)
        assert lv == SuccessLevel.HARD, f"预期 HARD, 实际 {lv}"
        _ok("D100=20(≤25) → HARD")

        # EXTREME
        lv = determine_success_level(50, 8)
        assert lv == SuccessLevel.EXTREME, f"预期 EXTREME, 实际 {lv}"
        _ok("D100=8(≤10) → EXTREME")

        # skill_check 完整路径
        result = skill_check(50, Difficulty.REGULAR)
        assert result.is_success or result.is_failure, "skill_check 应返回有效结果"
        _ok(f"skill_check(50): roll={result.roll_value} → {result.success_level.value}")
        passed += 6
    except Exception as e:
        _fail(f"规则内核校验失败: {e}")
        failed += 1
    print()

    # ── 步骤 6: 骰子引擎 ──
    print(_c("┃ 6/6  骰子引擎校验", _BOLD))
    try:
        tens, ones, total = roll_d100()
        assert 1 <= total <= 100, f"D100 越界: {total}"
        _ok(f"roll_d100() → {total} ({tens*10}+{ones})")

        total = roll_dice("2D6+3")
        assert 5 <= total <= 15, f"2D6+3 越界: {total}"
        _ok(f"roll_dice('2D6+3') → {total}")

        total = roll_dice("1D3+1D4")
        assert 2 <= total <= 7, f"1D3+1D4 越界: {total}"
        _ok(f"roll_dice('1D3+1D4') → {total}")

        passed += 3
    except Exception as e:
        _fail(f"骰子引擎校验失败: {e}")
        failed += 1
    print()

    # ── 清理 ──
    await scheduler.close()
    await engine.close()

    # ── 汇总 ──
    elapsed = time.time() - total_start
    print(_c("=" * 56, _CYAN))
    status = _c("PASSED", _GREEN) if failed == 0 else _c("FAILED", _RED)
    print(f"  冒烟测试 {status}  |  {passed} passed, {failed} failed  |  {elapsed:.1f}s")
    print(_c("=" * 56, _CYAN))
    print()
    return passed, failed


def main():
    passed, failed = asyncio.run(run_smoke())
    exit(0 if failed == 0 else 1)


if __name__ == "__main__":
    main()
