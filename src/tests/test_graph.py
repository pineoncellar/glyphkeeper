"""
@File     :   test_graph.py
@Desc     :   Graph 层测试
@Note     :   验证所有 Graph 的编译、路由、完整执行链路
"""

from __future__ import annotations

import pytest
from langgraph.graph import StateGraph, START, END
from src.state.game_state import GameState, create_initial_state
from src.graph.router_graph import route_by_intent
from src.graph.combat_graph import build_combat_subgraph, combat_subgraph
from src.graph.investigation_graph import build_investigation_subgraph, investigation_subgraph
from src.graph.keeper_graph import build_keeper_graph, keeper_graph


# ====================================================================
# 编译测试
# ====================================================================

class TestGraphCompile:
    """验证所有 Graph 可正常编译"""

    def test_router_importable(self):
        """route_by_intent 函数可导入"""
        from src.graph.router_graph import route_by_intent
        assert callable(route_by_intent)

    def test_combat_subgraph_compiles(self):
        """战斗子图可编译"""
        graph = combat_subgraph
        assert graph is not None

    def test_investigation_subgraph_compiles(self):
        """调查子图可编译"""
        graph = investigation_subgraph
        assert graph is not None

    def test_keeper_graph_compiles(self):
        """主图可编译"""
        graph = keeper_graph
        assert graph is not None

    def test_build_functions_return_graph(self):
        """构建函数返回有效的 StateGraph"""
        for build_fn in [build_combat_subgraph, build_investigation_subgraph, build_keeper_graph]:
            graph = build_fn()
            assert graph is not None


# ====================================================================
# 路由测试
# ====================================================================

class TestRouter:
    """验证路由函数正确性"""

    @pytest.mark.parametrize("intent_type,expected", [
        ("COMBAT_ACTION", "combat"),
        ("MOVE", "investigate"),
        ("PHYSICAL_INTERACT", "investigate"),
        ("SOCIAL_INTERACT", "npc_dialogue"),
        ("META", "narrate"),
        ("UNKNOWN_TYPE", "narrate"),
        ("", "narrate"),
    ])
    def test_route_by_intent(self, intent_type, expected):
        """路由表正确性"""
        state = create_initial_state("test", "测试")
        state["intent"] = {"type": intent_type, "data": {}}
        result = route_by_intent(state)
        assert result == expected, f"{intent_type} → 期望 {expected}，实际 {result}"

    def test_route_no_intent(self):
        """无 intent 时路由到 narrate"""
        state = create_initial_state("test", "测试")
        state["intent"] = None
        assert route_by_intent(state) == "narrate"

        state["intent"] = {}
        assert route_by_intent(state) == "narrate"


# ====================================================================
# 子图执行测试
# ====================================================================

class TestCombatSubgraph:
    """战斗子图执行"""

    @pytest.mark.asyncio
    async def test_combat_one_round(self):
        """战斗子图执行一轮"""
        state = create_initial_state("combat-test", "战斗测试")
        state.update({
            "player_input": "我用拳头攻击敌人",
            "game_phase": "combat",
            "combat_active": True,
            "combat_round": 0,
            "combatants": [
                {"name": "调查员", "skills": {"斗殴": 50}, "hit_points": 12},
                {"name": "邪教徒", "skills": {"闪避": 30}, "hit_points": 10, "armor": 0},
            ],
            "intent": {
                "type": "COMBAT_ACTION",
                "data": {
                    "action": "attack",
                    "weapon_name": "拳头",
                    "skill_name": "斗殴",
                    "skill_value": 50,
                    "target_name": "邪教徒",
                    "target_skill": "闪避",
                    "target_skill_value": 30,
                    "target_armor": 0,
                    "bonus_dice": 0,
                    "target_bonus": 0,
                },
            },
        })

        result = await combat_subgraph.ainvoke(state)

        assert "resolution" in result
        resolution = result["resolution"]
        assert resolution.get("success", False) is not False, f"战斗裁决失败: {resolution}"
        assert resolution.get("node_type") in ("combat_attack", "combat_dodge")
        assert result.get("combat_round", 0) >= 1

    @pytest.mark.asyncio
    async def test_combat_dodge(self):
        """战斗闪避"""
        state = create_initial_state("combat-test", "战斗测试")
        state.update({
            "player_input": "我闪避攻击",
            "game_phase": "combat",
            "combat_active": True,
            "intent": {
                "type": "COMBAT_ACTION",
                "data": {
                    "action": "dodge",
                    "skill_name": "闪避",
                    "skill_value": 40,
                    "target_name": "敌人",
                },
            },
        })

        result = await combat_subgraph.ainvoke(state)
        resolution = result.get("resolution", {})
        assert resolution.get("node_type") == "combat_dodge"


class TestInvestigationSubgraph:
    """调查子图执行"""

    @pytest.mark.asyncio
    async def test_skill_check_in_investigation(self):
        """调查子图中执行技能检定"""
        state = create_initial_state("invest-test", "调查测试")
        state.update({
            "player_input": "我检查书桌",
            "game_phase": "exploration",
            "intent": {
                "type": "PHYSICAL_INTERACT",
                "data": {
                    "action": "检查",
                    "target": "书桌",
                    "skill_name": "侦查",
                    "skill_value": 50,
                    "check_type": "skill",
                    "difficulty": "REGULAR",
                },
            },
        })

        result = await investigation_subgraph.ainvoke(state)

        assert "resolution" in result
        resolution = result["resolution"]
        assert resolution.get("success", False) is not False
        assert resolution.get("node_type") == "skill_check"
        assert resolution.get("skill_name") == "侦查"
        assert 1 <= resolution.get("roll_value", 0) <= 100
        assert resolution.get("success_level") in (
            "CRITICAL", "EXTREME", "HARD", "REGULAR", "FAILURE", "FUMBLE"
        )

    @pytest.mark.asyncio
    async def test_investigation_no_skill(self):
        """无技能名时直接结束"""
        state = create_initial_state("invest-test", "调查测试")
        state.update({
            "player_input": "你好",
            "intent": {
                "type": "SOCIAL_INTERACT",
                "data": {
                    "action": "打招呼",
                    "skill_name": "",
                },
            },
        })

        # 无 skill_name 时应直接结束，不触发 skill_node
        result = await investigation_subgraph.ainvoke(state)
        assert "resolution" not in result or result.get("resolution") is None


# ====================================================================
# 主图完整链路测试
# ====================================================================

class TestKeeperGraph:
    """守密人主图完整链路"""

    @pytest.mark.asyncio
    async def test_intent_to_narrate_physical(self):
        """完整链路：PHYSICAL_INTERACT → investigate → narrate"""
        state = create_initial_state("main-test", "主图测试")
        state["player_input"] = "我搜索房间"

        result = await keeper_graph.ainvoke(state)

        assert "intent" in result
        assert "narrative" in result
        intent = result["intent"]
        assert intent.get("type") in ("PHYSICAL_INTERACT", "META")
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_intent_to_narrate_combat(self):
        """完整链路：COMBAT_ACTION → combat → narrate"""
        state = create_initial_state("main-test", "主图测试")
        state.update({
            "player_input": "我用拳头攻击敌人",
            "game_phase": "combat",
            "combat_active": True,
            "combatants": [
                {"name": "调查员", "skills": {"斗殴": 50}, "hit_points": 12},
                {"name": "敌人", "skills": {"闪避": 30}, "hit_points": 10, "armor": 0},
            ],
        })

        result = await keeper_graph.ainvoke(state)

        assert "intent" in result
        assert "narrative" in result
        intent = result["intent"]
        assert intent.get("type") == "COMBAT_ACTION"
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_intent_to_narrate_meta(self):
        """完整链路：META → narrate（直接叙事）"""
        state = create_initial_state("main-test", "主图测试")
        state["player_input"] = "查看我的状态"

        result = await keeper_graph.ainvoke(state)

        assert "intent" in result
        assert "narrative" in result
        intent = result["intent"]
        assert intent.get("type") == "META"
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_intent_to_narrate_social(self):
        """完整链路：社交输入 → narrate（直接叙事）"""
        state = create_initial_state("main-test", "主图测试")
        state["player_input"] = "我问你一些问题"

        result = await keeper_graph.ainvoke(state)

        assert "intent" in result
        assert "narrative" in result
        intent = result["intent"]
        # LLM 可能将"提问"分类为 META 或 SOCIAL_INTERACT，都能接受
        assert intent.get("type") in ("SOCIAL_INTERACT", "META")
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_skill_check_with_narrative(self):
        """技能检定 + 叙事输出"""
        state = create_initial_state("main-test", "主图测试")
        state["player_input"] = "我仔细检查书桌的抽屉"

        result = await keeper_graph.ainvoke(state)

        assert "narrative" in result
        narrative = result["narrative"]
        assert isinstance(narrative, str) and len(narrative) > 0
        # 验证叙事包含动作描述
        assert "检查" in narrative or "搜索" in narrative or "侦查" in narrative or result["intent"]["type"] in ("PHYSICAL_INTERACT",)

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """空输入处理"""
        state = create_initial_state("main-test", "主图测试")
        state["player_input"] = ""

        result = await keeper_graph.ainvoke(state)

        assert "narrative" in result
        assert len(result["narrative"]) > 0


# ====================================================================
# 子图重建测试
# ====================================================================

class TestGraphRebuild:
    """验证构建函数可多次调用"""

    def test_rebuild_combat(self):
        """战斗子图可多次重建"""
        g1 = build_combat_subgraph()
        g2 = build_combat_subgraph()
        assert g1 is not None and g2 is not None

    def test_rebuild_investigation(self):
        """调查子图可多次重建"""
        g1 = build_investigation_subgraph()
        g2 = build_investigation_subgraph()
        assert g1 is not None and g2 is not None

    def test_rebuild_keeper(self):
        """主图可多次重建"""
        g1 = build_keeper_graph()
        g2 = build_keeper_graph()
        assert g1 is not None and g2 is not None
