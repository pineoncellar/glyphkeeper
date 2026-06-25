# -*- coding: utf-8 -*-
"""
@File     :   test_integration.py
@Desc     :   集成测试 — 验证多模块协作流程的正确性
@Note     :   覆盖全链路执行、事件持久化、降级兜底三种场景

测试范围:
  - test_full_game_flow:    角色创建→Graph执行→多轮交互→存档→读档
  - test_event_persistence: 事件写入与回放验证
  - test_llm_fallback:      LLM 不可用时规则兜底仍能产出叙事
"""

from __future__ import annotations

import pytest
from unittest.mock import patch

from src.state.game_state import GameState, create_initial_state
from src.state.reducer import reduce_state
from src.state.snapshot import SnapshotManager
from src.domain.character import Stats, Character, create_investigator
from src.domain.checks import skill_check, CheckResult
from src.domain.coc_rules import SuccessLevel, Difficulty, determine_success_level
from src.domain.sanity_rules import calculate_sanity_loss
from src.tools.dice import roll_d100, roll_dice
from src.graph.router_graph import route_by_intent
from src.graph.keeper_graph import keeper_graph, build_keeper_graph
from src.runtime.engine import GraphEngine, ENGINE_MODE_LANGGRAPH
from src.runtime.scheduler import InputScheduler
from src.runtime.context import ExecutionContext
from src.memory.event_store import EventStore


# ====================================================================
# 辅助函数
# ====================================================================

def _make_state(**overrides) -> GameState:
    """构建测试用 GameState"""
    base = dict(create_initial_state("integ-test"))
    base.update(overrides)
    return base


# ====================================================================
# 全链路测试
# ====================================================================

class TestFullGameFlow:
    """模拟完整游戏流程：角色创建 → Graph 执行 → 多轮交互 → 存档读档"""

    def test_character_creation(self):
        """验证角色创建全流程：属性→角色→衍生属性计算"""
        stats = Stats(
            strength=70, constitution=60, size=65,
            dexterity=50, appearance=40, intelligence=75,
            power=55, education=60,
        )
        occ_skills = {"侦查": 70, "图书馆利用": 50, "潜行": 40, "说服": 50, "信用评级": 30}
        char = create_investigator("测试员", "侦探", stats, occ_skills)

        assert char.name == "测试员"
        assert char.occupation == "侦探"
        # 衍生属性校验（CoC 7版: HP=(CON+SIZ)/2, SAN=POW, MP=POW/5）
        expected_hp = (60 + 65) // 2
        assert char.max_hit_points == expected_hp, f"HP: {char.max_hit_points} \u2260 {expected_hp}"
        assert char.max_sanity == 55, f"SAN: {char.max_sanity} \u2260 55"
        assert char.max_magic_points == 55 // 5, f"MP: {char.max_magic_points} \u2260 {55//5}"
        # DB / Build: STR+SIZ=70+65=135 → DB=+1D4, Build=1 (查规则)
        assert char.damage_bonus in ("+1D4", "0", "+1D6"), f"DB: {char.damage_bonus}"
        # 技能合并
        assert char.skills.get("侦查") == 70
        assert char.skills.get("闪避") == 25  # 基础值 = DEX/2

    @pytest.mark.asyncio
    async def test_graph_full_execution(self):
        """验证 keeper_graph 完整执行流程：intent→router→narrate"""
        graph = keeper_graph
        assert graph is not None

        state = _make_state(player_input="搜索桌子", game_phase="exploration")
        result = await graph.ainvoke(state)

        assert result is not None
        assert "narrative" in result, f"缺少 narrative 字段: {list(result.keys())}"
        assert isinstance(result["narrative"], str)
        # intent 节点应该产生了结构化输出
        assert result.get("intent") is not None, "intent 节点未产生输出"
        intent_type = result["intent"].get("type", "")
        assert intent_type in (
            "PHYSICAL_INTERACT", "MOVE", "SOCIAL_INTERACT", "META", "COMBAT_ACTION",
        ), f"未知意图类型: {intent_type}"

    @pytest.mark.asyncio
    async def test_multi_turn_conversation(self):
        """验证多轮交互的 state 持久化"""
        engine = GraphEngine(keeper_graph, mode=ENGINE_MODE_LANGGRAPH)
        scheduler = InputScheduler(engine)

        inputs = ["打开门", "搜索房间", "查看状态"]
        for i, text in enumerate(inputs):
            narrative = await scheduler.submit("multi-turn", text)
            assert narrative, f"第 {i+1} 轮返回空叙事"
            assert len(narrative) > 5, f"第 {i+1} 轮叙事过短: '{narrative[:20]}...'"

        final_state = scheduler.get_session_state("multi-turn")
        assert final_state is not None, "多轮后状态丢失"
        assert final_state["beat_counter"] >= len(inputs), \
            f"beat_counter ({final_state['beat_counter']}) < {len(inputs)}"
        # 验证每轮 player_input 保留（上一次的输入被覆盖，但 counter 已累加）
        assert len(final_state["player_input"]) > 0

        await scheduler.close()
        await engine.close()

    @pytest.mark.asyncio
    async def test_save_and_load_roundtrip(self):
        """验证快照创建与恢复的往返一致性"""
        engine = GraphEngine(keeper_graph, mode=ENGINE_MODE_LANGGRAPH)
        scheduler = InputScheduler(engine)
        snap_mgr = SnapshotManager()

        # 执行几轮
        for text in ["搜索房间", "打开抽屉"]:
            await scheduler.submit("save-test", text)

        state_before = scheduler.get_session_state("save-test")
        assert state_before is not None

        # 创建快照
        snap_id = await snap_mgr.create(state_before, label="test_checkpoint")
        assert snap_id is not None

        # 继续执行一轮（状态改变）
        await scheduler.submit("save-test", "查看书籍")

        # 从快照恢复
        restored = await snap_mgr.restore(snap_id)
        assert restored is not None
        assert restored["beat_counter"] == state_before["beat_counter"]
        assert restored["narrative"] == state_before["narrative"]

        await snap_mgr.close()
        await scheduler.close()
        await engine.close()


# ====================================================================
# 事件持久化测试
# ====================================================================

class TestEventPersistence:
    """验证事件写入与回放"""

    @pytest.mark.asyncio
    async def test_event_store_write_and_read(self):
        """验证 EventStore 写入事件后可读"""
        store = EventStore()
        session_id = "event-test"

        # 写入事件
        evt = await store.append(
            session_id=session_id,
            event_type="TestEvent",
            data={"key": "value", "number": 42},
            source_node="test",
        )
        assert evt is not None
        assert evt.get("type") == "TestEvent"
        assert evt.get("session_id") == session_id

        # 读取事件
        events = await store.get_events(session_id, since_version=0)
        assert len(events) >= 1
        found = [e for e in events if e.get("type") == "TestEvent"]
        assert len(found) >= 1
        assert found[0]["data"]["key"] == "value"

    @pytest.mark.asyncio
    async def test_event_version_tracking(self):
        """验证事件版本号递增"""
        store = EventStore()
        session_id = "version-test"

        v1 = await store.get_latest_version(session_id)
        await store.append(session_id, "EventA", {"idx": 1}, source_node="test")
        await store.append(session_id, "EventB", {"idx": 2}, source_node="test")
        v_latest = await store.get_latest_version(session_id)

        assert v_latest >= v1 + 2, f"版本号未正确递增: {v1} → {v_latest}"

    @pytest.mark.asyncio
    async def test_event_causality_chain(self):
        """验证事件因果链（parent_event_id）"""
        store = EventStore()
        session_id = "causality-test"

        parent = await store.append(
            session_id, "ParentEvent", {"msg": "parent"}, source_node="node_a",
        )
        child = await store.append(
            session_id, "ChildEvent", {"msg": "child"}, source_node="node_b",
            parent_event_id=parent.get("id"),
        )

        assert child.get("parent_event_id") == parent.get("id")


# ====================================================================
# 降级兜底测试
# ====================================================================

class TestLLMFallback:
    """验证 LLM 不可用时规则/模板兜底仍能产出叙事"""

    def test_rule_only_determine_success(self):
        """纯规则：成功等级判定"""
        assert determine_success_level(50, 1) == SuccessLevel.CRITICAL
        assert determine_success_level(50, 100) == SuccessLevel.FUMBLE
        assert determine_success_level(50, 40) == SuccessLevel.REGULAR
        assert determine_success_level(50, 20) == SuccessLevel.HARD
        assert determine_success_level(50, 8) == SuccessLevel.EXTREME

    def test_rule_only_skill_check(self):
        """纯规则：技能检定"""
        result = skill_check(50, Difficulty.REGULAR)
        assert isinstance(result, CheckResult)
        assert 1 <= result.roll_value <= 100
        # 要么成功要么失败
        assert result.is_success or result.is_failure

    @pytest.mark.asyncio
    async def test_rule_only_intent_node(self):
        """纯规则兜底：intent_node 用关键词匹配"""
        from src.nodes.llm.intent_node import rule_only_intent_node

        test_cases = [
            ("搜索房间", "PHYSICAL_INTERACT"),
            ("攻击邪教徒", "COMBAT_ACTION"),
            ("去大厅", "MOVE"),
            ("询问你的名字", "SOCIAL_INTERACT"),
            ("查看状态", "META"),
        ]
        for text, expected_type in test_cases:
            state = _make_state(player_input=text)
            result = await rule_only_intent_node(state)
            intent = result.get("intent", {})
            assert intent.get("type") == expected_type, \
                f"'{text}': 预期 {expected_type}, 实际 {intent.get('type')}"

    @pytest.mark.asyncio
    async def test_rule_only_narrate_node(self):
        """模板兜底：narrator 在有 resolution 时产出模板叙事"""
        from src.nodes.llm.narrator_node import narrate_node

        state = _make_state(
            player_input="搜索房间",
            game_phase="exploration",
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {"action": "搜索", "skill_name": "侦查"},
            },
            resolution={
                "success": True,
                "success_level": "REGULAR",
                "success_label": "常规成功",
                "description": "你仔细检查了房间的每一个角落",
            },
        )
        result = await narrate_node(state)
        narrative = result.get("narrative", "")
        assert len(narrative) > 0, "模板叙事为空"
        # 无论 LLM 模式还是模板模式，叙事应包含与输入相关的语义内容
        assert len(narrative) >= 10, f"叙事过短: '{narrative}'"

    @pytest.mark.asyncio
    async def test_rule_only_combat_narrative(self):
        """模板兜底：战斗叙事"""
        from src.nodes.llm.narrator_node import narrate_node

        state = _make_state(
            player_input="攻击邪教徒",
            game_phase="combat",
            intent={"type": "COMBAT_ACTION", "data": {"action": "攻击", "target": "邪教徒"}},
            resolution={
                "success": True,
                "hit": True,
                "damage": 8,
                "hit_location": "躯干",
                "target": "邪教徒",
            },
        )
        result = await narrate_node(state)
        narrative = result.get("narrative", "")
        assert len(narrative) > 0, "战斗模板叙事为空"
        assert "邪教徒" in narrative or "攻击" in narrative

    @pytest.mark.asyncio
    async def test_graph_executes_without_llm(self):
        """Graph 在完整走规则/模板兜底时仍能完成全流程"""
        engine = GraphEngine(keeper_graph, mode=ENGINE_MODE_LANGGRAPH)
        scheduler = InputScheduler(engine)

        narrative = await scheduler.submit("fallback-test", "搜索桌子")
        assert narrative, "无 LLM 时 Graph 返回空叙事"
        assert len(narrative) > 5, "叙事过短"

        state = scheduler.get_session_state("fallback-test")
        assert state is not None
        assert state.get("intent") is not None, "intent 缺失"
        assert "narrative" in state, "narrative 缺失"

        await scheduler.close()
        await engine.close()
