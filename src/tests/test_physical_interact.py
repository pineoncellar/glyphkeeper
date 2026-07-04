"""
@File     :   test_physical_interact.py
@Desc     :   物理交互子图测试 — skill_check / spatial_physics / effect_archivist
@Note     :   类型 A 测试（无 LLM），纯逻辑验证。构造 mock GameState 测试各节点。
"""

from __future__ import annotations

import pytest
from src.state.game_state import GameState, create_initial_state
from src.nodes.physical.skill_check_node import skill_check_node
from src.nodes.physical.spatial_physics_node import spatial_physics_node, FactManifest
from src.nodes.physical.effect_archivist_node import effect_archivist_node
from src.nodes.physical.physical_interact_graph import run_physical_interact_subgraph


# ====================================================================
# 辅助函数：构建测试用 state
# ====================================================================


def _make_skill_check_result(
    is_success: bool = True,
    skill_name: str = "侦查",
    skill_value: int = 60,
    roll_value: int = 34,
    success_level: str = "HARD",
    bypassed: bool = False,
) -> dict:
    return {
        "bypassed": bypassed,
        "is_success": is_success,
        "success_level": success_level,
        "skill_name": skill_name,
        "skill_value": skill_value,
        "roll_value": roll_value,
        "difficulty": "REGULAR",
        "node_type": "skill_check",
    }


def _make_spatial_result(
    physical_executed: bool = True,
    execution_phase: str = "NORMAL",
    spatial_reason: str = "OK",
    is_locked: bool = False,
    is_searched: bool = False,
    has_key: bool = False,
    target_key: str = "item_study_desk",
) -> dict:
    return {
        "spatial_valid": True,
        "spatial_reason": spatial_reason,
        "physical_executed": physical_executed,
        "execution_phase": execution_phase,
        "is_locked": is_locked,
        "is_searched": is_searched,
        "has_key": has_key,
        "target_state": "",
        "_target_key": target_key,
    }


def _make_base_state(**overrides) -> GameState:
    """构建含物理交互所需字段的最小 GameState"""
    state = create_initial_state(
        session_id="test-physical",
        scenario_name="测试模组",
        world_id="test_world",
    )
    state["intent_queue"] = [{
        "type": "PHYSICAL_INTERACT",
        "confidence": 0.95,
        "core_action": "检查书桌",
        "flavor_context": "你俯下身",
        "data": {
            "target": "书桌",
            "skill_name": "侦查",
            "check_type": "skill",
            "difficulty": "REGULAR",
            "detail": "仔细检查书桌的每一个抽屉",
        },
    }]
    state["current_intent_idx"] = 0
    state["_scene_interactables"] = [
        {"key": "item_study_desk", "name": "旧书桌", "tags": [], "state": ""},
        {"key": "item_drawer", "name": "抽屉", "tags": ["locked"], "state": "LOCKED"},
        {"key": "item_book", "name": "古书", "tags": ["searched"], "state": ""},
    ]
    state["players"]["default"]["current_location"] = "study_room"
    state.update(overrides)
    return state


# ====================================================================
# skill_check_node 测试
# ====================================================================


class TestSkillCheckNode:
    """纯数值检定节点"""

    @pytest.mark.asyncio
    async def test_normal_skill_check(self):
        """正常检定：有技能名 + 有角色卡 → 返回检定结果"""
        state = _make_base_state()
        state["players"]["default"]["character"] = {
            "skills": {"侦查": 60, "聆听": 50},
        }
        result = await skill_check_node(state)
        check = result.get("_skill_check_result", {})
        assert not check.get("bypassed", True)
        assert check.get("skill_name") == "侦查"
        assert check.get("skill_value") == 60
        assert "roll_value" in check
        assert "success_level" in check

    @pytest.mark.asyncio
    async def test_bypassed_when_no_skill_name(self):
        """无需检定：技能名为空 + check_type=none → bypassed"""
        state = _make_base_state()
        state["intent_queue"][0]["data"]["skill_name"] = ""
        state["intent_queue"][0]["data"]["check_type"] = "none"
        result = await skill_check_node(state)
        check = result.get("_skill_check_result", {})
        assert check.get("bypassed") is True
        assert check.get("is_success") is True

    @pytest.mark.asyncio
    async def test_skill_value_from_intent_data(self):
        """技能值优先取 intent.data.skill_value"""
        state = _make_base_state()
        state["intent_queue"][0]["data"]["skill_value"] = 80
        result = await skill_check_node(state)
        check = result.get("_skill_check_result", {})
        assert check.get("skill_value") == 80

    @pytest.mark.asyncio
    async def test_default_skill_value(self):
        """无角色卡无 intent 值 → 兜底 50"""
        state = _make_base_state()
        state["intent_queue"][0]["data"]["skill_value"] = None
        state["players"]["default"]["character"] = {"skills": {}}
        result = await skill_check_node(state)
        check = result.get("_skill_check_result", {})
        assert check.get("skill_value") == 50


# ====================================================================
# spatial_physics_node 测试
# ====================================================================


class TestSpatialPhysicsNode:
    """空间与物理可行性仲裁"""

    @pytest.mark.asyncio
    async def test_spatial_reachable(self):
        """空间可达：目标物品在当前场景中 → spatial_valid=True"""
        state = _make_base_state()
        # 模拟 disambiguation_node 的消歧结果
        state["resolved_targets"] = {"书桌": "item_study_desk"}
        result = await spatial_physics_node(state)
        spatial = result.get("_spatial_result", {})
        assert spatial.get("spatial_valid") is True
        assert spatial.get("spatial_reason") == "OK"

    @pytest.mark.asyncio
    async def test_spatial_out_of_reach(self):
        """空间不可达：目标不在场景中且 LLM 拒绝即兴 → OUT_OF_REACH"""
        state = _make_base_state()
        state["intent_queue"][0]["data"]["target"] = "金戒指"
        state["intent_queue"][0]["data"]["detail"] = "捡起地上的金戒指"
        state["_scene_interactables"] = []  # 空场景
        result = await spatial_physics_node(state)
        spatial = result.get("_spatial_result", {})
        assert spatial.get("spatial_valid") is False
        assert spatial.get("execution_phase") == "OUT_OF_REACH"

    @pytest.mark.asyncio
    async def test_impromptu_approval(self):
        """即兴降级：场景无该物品但 LLM 批准 → IMPROMPTU"""
        state = _make_base_state()
        state["_scene_interactables"] = []  # 空场景
        result = await spatial_physics_node(state)
        spatial = result.get("_spatial_result", {})
        # 书桌是常见物品，LLM 应批准即兴交互
        assert spatial.get("spatial_valid") is True
        assert spatial.get("execution_phase") == "IMPROMPTU"
        assert "impromptu_" in spatial.get("_target_key", "")

    @pytest.mark.asyncio
    async def test_locked_item_no_key(self):
        """上锁且无钥匙 → LOCKED"""
        state = _make_base_state()
        state["intent_queue"][0]["data"]["target"] = "抽屉"
        state["_scene_interactables"] = [
            {"key": "item_drawer", "name": "抽屉", "tags": ["locked"], "state": "LOCKED"},
        ]
        state["resolved_targets"] = {"抽屉": "item_drawer"}
        result = await spatial_physics_node(state)
        spatial = result.get("_spatial_result", {})
        assert spatial.get("is_locked") is True
        assert spatial.get("has_key") is False
        assert spatial.get("execution_phase") == "LOCKED"

    @pytest.mark.asyncio
    async def test_already_searched(self):
        """已搜索 → ALREADY_SEARCHED"""
        state = _make_base_state()
        state["_scene_interactables"] = [
            {"key": "item_book", "name": "古书", "tags": ["searched"], "state": ""},
        ]
        state["resolved_targets"] = {"古书": "item_book"}
        state["intent_queue"][0]["data"]["target"] = "古书"
        result = await spatial_physics_node(state)
        spatial = result.get("_spatial_result", {})
        assert spatial.get("is_searched") is True
        assert spatial.get("execution_phase") == "ALREADY_SEARCHED"

    @pytest.mark.asyncio
    async def test_normal_execution(self):
        """正常执行：可达 + 未锁定 + 未搜索 → NORMAL"""
        state = _make_base_state()
        state["resolved_targets"] = {"书桌": "item_study_desk"}
        result = await spatial_physics_node(state)
        spatial = result.get("_spatial_result", {})
        assert spatial.get("physical_executed") is True
        assert spatial.get("execution_phase") == "NORMAL"


# ====================================================================
# effect_archivist_node 测试
# ====================================================================


class TestEffectArchivistNode:
    """结算与线索颁发"""

    @pytest.mark.asyncio
    async def test_normal_flow_produces_action(self):
        """正常流程：skill_check + spatial 都通过 → 产出 ActionExecutionResult"""
        state = _make_base_state()
        state["_skill_check_result"] = _make_skill_check_result()
        state["_spatial_result"] = _make_spatial_result()
        result = await effect_archivist_node(state)

        actions = result.get("executed_actions", [])
        assert len(actions) == 1
        action = actions[0]
        assert action["intent_type"] == "PHYSICAL_INTERACT"
        assert action["rule_context"]["physical_executed"] is True
        assert action["rule_context"]["execution_phase"] == "NORMAL"

    @pytest.mark.asyncio
    async def test_locked_phase_clears_clues(self):
        """上锁场景：physical_executed=false → clues_discovered 强行锁空"""
        state = _make_base_state()
        state["_skill_check_result"] = _make_skill_check_result(is_success=True)
        state["_spatial_result"] = _make_spatial_result(
            physical_executed=False,
            execution_phase="LOCKED",
            is_locked=True,
        )
        result = await effect_archivist_node(state)
        action = result["executed_actions"][0]
        assert action["rule_context"]["physical_executed"] is False
        assert action["rule_context"]["execution_phase"] == "LOCKED"
        assert action["rule_context"]["clues_discovered"] == []

    @pytest.mark.asyncio
    async def test_already_searched_phase_no_clues(self):
        """重复搜索：is_searched=True → 无线索"""
        state = _make_base_state()
        state["_skill_check_result"] = _make_skill_check_result(is_success=True)
        state["_spatial_result"] = _make_spatial_result(
            execution_phase="ALREADY_SEARCHED",
            is_searched=True,
        )
        result = await effect_archivist_node(state)
        action = result["executed_actions"][0]
        assert action["rule_context"]["execution_phase"] == "ALREADY_SEARCHED"
        assert action["rule_context"]["clues_discovered"] == []

    @pytest.mark.asyncio
    async def test_temp_fields_cleaned(self):
        """临时字段 _skill_check_result 和 _spatial_result 被清理"""
        state = _make_base_state()
        state["_skill_check_result"] = _make_skill_check_result()
        state["_spatial_result"] = _make_spatial_result()
        result = await effect_archivist_node(state)
        assert result.get("_skill_check_result") is None
        assert result.get("_spatial_result") is None

    @pytest.mark.asyncio
    async def test_bypassed_check_no_clues(self):
        """绕过检定（check_type=none）：bypassed=True → 正常执行但线索为空"""
        state = _make_base_state()
        state["_skill_check_result"] = _make_skill_check_result(bypassed=True)
        state["_spatial_result"] = _make_spatial_result()
        result = await effect_archivist_node(state)
        action = result["executed_actions"][0]
        assert action["rule_context"]["bypassed"] is True
        # bypassed 时 physical_executed=True 但无技能名 → 线索查询可能因无 skill_name 跳过
        assert action["rule_context"]["clues_discovered"] == []


# ====================================================================
# 子图集成测试
# ====================================================================


class TestPhysicalInteractSubgraph:
    """物理交互子图集成"""

    @pytest.mark.asyncio
    async def test_full_subgraph_normal(self):
        """完整子图正常流程：所有节点串联 → 产出 ActionExecutionResult"""
        state = _make_base_state()
        state["players"]["default"]["character"] = {
            "skills": {"侦查": 60},
        }
        result = await run_physical_interact_subgraph(state)

        actions = result.get("executed_actions", [])
        assert len(actions) == 1  # 只产生一条 action
        action = actions[0]
        assert action["intent_type"] == "PHYSICAL_INTERACT"
        assert "rule_context" in action
        assert "skill_value" in action["rule_context"]
        assert "execution_phase" in action["rule_context"]

    @pytest.mark.asyncio
    async def test_full_subgraph_out_of_reach(self):
        """子图隔空取物：目标不在场景且 LLM 拒绝即兴 → OUT_OF_REACH + 无线索"""
        state = _make_base_state()
        state["intent_queue"][0]["data"]["target"] = "金戒指"
        state["intent_queue"][0]["data"]["detail"] = "捡起地上的金戒指"
        state["players"]["default"]["character"] = {"skills": {"侦查": 60}}
        state["_scene_interactables"] = []  # 空场景
        result = await run_physical_interact_subgraph(state)

        action = result["executed_actions"][0]
        assert action["rule_context"]["physical_executed"] is False
        assert action["rule_context"]["execution_phase"] == "OUT_OF_REACH"
        assert action["rule_context"]["clues_discovered"] == []

    @pytest.mark.asyncio
    async def test_full_subgraph_impromptu(self):
        """子图即兴落包：LLM 批准 → IMPROMPTU + 无线索 + deterministic_changes 含落包"""
        state = _make_base_state()
        state["players"]["default"]["character"] = {"skills": {"侦查": 60}}
        state["_scene_interactables"] = []  # 空场景
        result = await run_physical_interact_subgraph(state)

        action = result["executed_actions"][0]
        assert action["rule_context"]["physical_executed"] is True
        assert action["rule_context"]["execution_phase"] == "IMPROMPTU"
        assert action["rule_context"]["clues_discovered"] == []
        dyn = action.get("deterministic_changes", {})
        assert "_inventory_append" in dyn
        assert "_mark_searched" in dyn

    @pytest.mark.asyncio
    async def test_subgraph_temp_fields_cleaned(self):
        """子图退出后临时字段被清理"""
        state = _make_base_state()
        state["_skill_check_result"] = {"dummy": True}
        state["_spatial_result"] = {"dummy": True}

        result = await run_physical_interact_subgraph(state)

        # 包裹函数兜底清理
        assert result.get("_skill_check_result") is None
        assert result.get("_spatial_result") is None


# ====================================================================
# FactManifest 测试
# ====================================================================


class TestFactManifest:
    """FactManifest 序列化/反序列化"""

    def test_to_dict_roundtrip(self):
        f = FactManifest()
        f.spatial_valid = True
        f.spatial_reason = "OK"
        f.physical_executed = True
        f.execution_phase = "NORMAL"
        f._target_key = "item_desk"
        f.is_locked = False
        f.is_searched = False

        d = f.to_dict()
        assert d["spatial_valid"] is True
        assert d["execution_phase"] == "NORMAL"

        restored = FactManifest.from_dict(d)
        assert restored.spatial_valid is True
        assert restored.execution_phase == "NORMAL"
        assert restored._target_key == "item_desk"
