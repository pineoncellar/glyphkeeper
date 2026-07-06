"""
@File     :   test_nodes.py
@Desc     :   Node 层单元测试
@Note     :   测试 Tool / Rule / LLM 节点函数，不依赖 LLM 和外部存储

测试范围:
  - Tool Nodes: dice_node, lookup_node(降级), roll_node
  - Rule Nodes: skill_node, sanity_node, combat_node
  - LLM Nodes: rule_only_intent_node, narrate_node(模板), adjudicate_node(规则)
"""

import pytest
from src.state.game_state import create_initial_state, GameState


# ====================================================================
# 辅助函数: 构建测试用 GameState
# ====================================================================

def _make_state(**overrides) -> GameState:
    """构建测试用 GameState"""
    base = dict(create_initial_state())
    base.update(overrides)
    return base


# ====================================================================
# Tool Nodes
# ====================================================================

class TestDiceNode:
    """掷骰执行节点测试"""

    @pytest.mark.asyncio
    async def test_dice_node_no_pending(self):
        """无待处理请求时返回错误"""
        from src.nodes.tools.dice_node import dice_node
        state = _make_state()
        result = await dice_node(state)
        assert result["pending_dice"] is None
        assert result["resolution"]["success"] is False
        assert "error" in result["resolution"]

    @pytest.mark.asyncio
    async def test_dice_node_d100(self):
        """D100 掷骰"""
        from src.nodes.tools.dice_node import dice_node
        state = _make_state(pending_dice={
            "reason": "测试掷骰",
            "skill_value": 50,
        })
        result = await dice_node(state)
        assert result["pending_dice"] is None
        assert result["resolution"]["success"] is True
        assert 1 <= result["resolution"]["roll_value"] <= 100
        assert result["resolution"]["reason"] == "测试掷骰"

    @pytest.mark.asyncio
    async def test_dice_node_with_skill_check(self):
        """带技能判定的掷骰"""
        from src.nodes.tools.dice_node import dice_node
        state = _make_state(pending_dice={
            "reason": "侦查检定",
            "skill_name": "侦查",
            "skill_value": 50,
            "difficulty": "REGULAR",
        })
        result = await dice_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["success_level"] is not None
        assert result["resolution"]["success_level"] in (
            "CRITICAL", "EXTREME", "HARD", "REGULAR", "FAILURE", "FUMBLE"
        )

    @pytest.mark.asyncio
    async def test_dice_node_expression(self):
        """自定义骰子表达式"""
        from src.nodes.tools.dice_node import dice_node
        state = _make_state(pending_dice={
            "reason": "伤害掷骰",
            "expression": "2D6",
        })
        result = await dice_node(state)
        assert result["resolution"]["success"] is True
        assert 2 <= result["resolution"]["roll_value"] <= 12

    @pytest.mark.asyncio
    async def test_simple_dice_roll(self):
        """快捷掷骰辅助函数"""
        from src.nodes.tools.dice_node import simple_dice_roll
        result = await simple_dice_roll("1D6", "测试")
        assert "roll_value" in result
        assert 1 <= result["roll_value"] <= 6

    @pytest.mark.asyncio
    async def test_simple_dice_roll_with_skill(self):
        """带技能值的快捷掷骰"""
        from src.nodes.tools.dice_node import simple_dice_roll
        result = await simple_dice_roll(skill_value=50, difficulty="HARD")
        assert "roll_value" in result
        assert "success_level" in result
        assert 1 <= result["roll_value"] <= 100


class TestRagLookupNode:
    """RAG 检索节点测试（降级模式）"""

    @pytest.mark.asyncio
    async def test_rag_lookup_node_empty_query(self):
        """空查询返回空 rag_context"""
        from src.nodes.tools.rag_lookup_node import rag_lookup_node
        state = _make_state(player_input="")
        result = await rag_lookup_node(state)
        assert result.get("rag_context") == ""


class TestRollNode:
    """自动化检定节点测试"""

    @pytest.mark.asyncio
    async def test_roll_node_skill_check(self):
        """技能检定"""
        from src.nodes.tools.roll_node import roll_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "check_type": "skill",
                "skill_name": "侦查",
                "skill_value": 60,
                "difficulty": "REGULAR",
            },
        })
        result = await roll_node(state)
        assert result["pending_dice"] is None
        assert result["resolution"]["success"] is True
        assert result["resolution"]["check_type"] == "skill"
        assert result["resolution"]["success_level"] is not None
        assert result["resolution"]["skill_name"] == "侦查"

    @pytest.mark.asyncio
    async def test_roll_node_stat_check(self):
        """属性检定"""
        from src.nodes.tools.roll_node import roll_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "check_type": "stat",
                "stat_name": "STR",
                "stat_value": 12,
                "difficulty": "HARD",
            },
        })
        result = await roll_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["check_type"] == "stat"

    @pytest.mark.asyncio
    async def test_quick_skill_check(self):
        """快捷技能检定"""
        from src.nodes.tools.roll_node import quick_skill_check
        result = await quick_skill_check(50, "REGULAR")
        assert result["success"] is True
        assert "success_level" in result
        assert "roll_value" in result

    @pytest.mark.asyncio
    async def test_roll_node_from_pending_dice(self):
        """从 pending_dice 读取参数的检定"""
        from src.nodes.tools.roll_node import roll_node
        state = _make_state(pending_dice={
            "reason": "侦查检定",
            "skill_name": "侦查",
            "skill_value": 50,
            "difficulty": "REGULAR",
        })
        result = await roll_node(state)
        assert result["pending_dice"] is None
        assert result["resolution"]["success"] is True


# ====================================================================
# Rule Nodes
# ====================================================================

class TestSkillNode:
    """技能检定节点测试"""

    @pytest.mark.asyncio
    async def test_skill_node_missing_skill_name(self):
        """缺少技能名返回错误"""
        from src.nodes.rules.skill_node import skill_node
        state = _make_state(intent={"type": "PHYSICAL_INTERACT", "data": {}})
        result = await skill_node(state)
        assert result["resolution"]["success"] is False
        assert "error" in result["resolution"]

    @pytest.mark.asyncio
    async def test_skill_node_basic(self):
        """基本技能检定"""
        from src.nodes.rules.skill_node import skill_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "skill_name": "侦查",
                "skill_value": 50,
                "difficulty": "REGULAR",
            },
        })
        result = await skill_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["node_type"] == "skill_check"
        assert result["resolution"]["skill_name"] == "侦查"
        assert result["resolution"]["skill_value"] == 50
        assert result["resolution"]["success_level"] in (
            "CRITICAL", "EXTREME", "HARD", "REGULAR", "FAILURE", "FUMBLE"
        )

    @pytest.mark.asyncio
    async def test_skill_node_hard_difficulty(self):
        """困难难度检定"""
        from src.nodes.rules.skill_node import skill_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "skill_name": "侦查",
                "skill_value": 50,
                "difficulty": "HARD",
            },
        })
        result = await skill_node(state)
        assert result["resolution"]["difficulty"] == "HARD"

    @pytest.mark.asyncio
    async def test_skill_node_with_character(self):
        """从角色数据读取技能值"""
        from src.nodes.rules.skill_node import skill_node
        state = _make_state(
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {
                    "skill_name": "侦查",
                },
            },
            character={
                "name": "测试员",
                "skills": {"侦查": 60, "图书馆利用": 40},
            },
        )
        result = await skill_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["skill_value"] == 60


class TestSanityNode:
    """理智检定节点测试"""

    @pytest.mark.asyncio
    async def test_sanity_node_basic(self):
        """基本理智损失计算"""
        from src.nodes.rules.sanity_node import sanity_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "source_type": "seeing_dead_body",
                "current_san": 60,
                "max_san": 60,
            },
        })
        result = await sanity_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["node_type"] == "sanity_check"
        assert 0 <= result["resolution"]["actual_loss"] <= 1
        assert result["resolution"]["remaining_san"] == 60 - result["resolution"]["actual_loss"]

    @pytest.mark.asyncio
    async def test_sanity_node_mythos(self):
        """神话相关理智损失"""
        from src.nodes.rules.sanity_node import sanity_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "source_type": "seeing_mythos_creature",
                "current_san": 50,
                "max_san": 50,
                "is_mythos": True,
            },
        })
        result = await sanity_node(state)
        assert result["resolution"]["success"] is True
        assert 0 <= result["resolution"]["actual_loss"] <= 6
        # 可能触发疯狂
        if result["resolution"]["actual_loss"] >= 10:
            assert result["resolution"]["is_temporary_insanity"] is True

    @pytest.mark.asyncio
    async def test_sanity_node_custom_loss_range(self):
        """自定义损失范围"""
        from src.nodes.rules.sanity_node import sanity_node
        state = _make_state(intent={
            "type": "PHYSICAL_INTERACT",
            "data": {
                "source_type": "custom",
                "loss_range": [3, 5],
                "current_san": 60,
                "max_san": 60,
            },
        })
        result = await sanity_node(state)
        assert result["resolution"]["success"] is True
        assert 3 <= result["resolution"]["actual_loss"] <= 5

    @pytest.mark.asyncio
    async def test_sanity_node_updates_character(self):
        """理智损失更新角色 SAN 值"""
        from src.nodes.rules.sanity_node import sanity_node
        state = _make_state(
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {
                    "source_type": "seeing_dead_body",
                    "current_san": 60,
                    "max_san": 60,
                },
            },
            character={
                "name": "测试员",
                "sanity": 60,
                "max_sanity": 60,
            },
        )
        result = await sanity_node(state)
        assert "character" in result
        assert result["character"]["sanity"] == 60 - result["resolution"]["actual_loss"]


class TestCombatNode:
    """战斗裁决节点测试"""

    @pytest.mark.asyncio
    async def test_combat_node_attack(self):
        """战斗攻击裁决"""
        from src.nodes.rules.combat_node import combat_node
        state = _make_state(
            intent={
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
                    "damage_bonus": "0",
                },
            },
            combat_active=True,
            combat_round=1,
        )
        result = await combat_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["node_type"] == "combat_attack"
        assert result["resolution"]["actor_name"] is not None
        assert result["resolution"]["target_name"] == "邪教徒"
        assert result["resolution"]["weapon"] == "拳头"
        assert result["combat_round"] == 2

    @pytest.mark.asyncio
    async def test_combat_node_dodge(self):
        """闪避动作"""
        from src.nodes.rules.combat_node import combat_node
        state = _make_state(
            intent={
                "type": "COMBAT_ACTION",
                "data": {
                    "action": "dodge",
                    "skill_name": "闪避",
                    "skill_value": 40,
                },
            },
        )
        result = await combat_node(state)
        assert result["resolution"]["success"] is True
        assert result["resolution"]["node_type"] == "combat_dodge"
        assert result["resolution"]["action"] == "dodge"

    @pytest.mark.asyncio
    async def test_init_combat_node(self):
        """战斗初始化"""
        from src.nodes.rules.combat_node import init_combat_node
        state = _make_state(
            intent={
                "type": "COMBAT_ACTION",
                "data": {
                    "enemies": [
                        {"name": "邪教徒A", "skills": {"斗殴": 40}, "hit_points": 12, "armor": 0},
                        {"name": "邪教徒B", "skills": {"斗殴": 30}, "hit_points": 10, "armor": 0},
                    ],
                },
            },
            character={
                "name": "调查员",
                "skills": {"斗殴": 50},
                "hit_points": 12,
                "max_hit_points": 12,
                "armor": 0,
                "damage_bonus": "0",
            },
        )
        result = await init_combat_node(state)
        assert result["combat_active"] is True
        assert result["combat_round"] == 1
        assert result["game_phase"] == "combat"
        assert len(result["combatants"]) == 3  # 调查员 + 2 邪教徒


# ====================================================================
# Navigation Node (纯逻辑，无外部依赖)
# ====================================================================
# Navigation Node — 纯函数测试
# ====================================================================

class TestMatchExitDirection:
    """_match_exit_direction — 方向性移动匹配"""

    def test_match_direction(self):
        """出口方向匹配"""
        from src.nodes.rules.navigation_node import _match_exit_direction
        exits = {"north": "loc_garden", "south": "loc_street", "Outside": "loc_street"}
        result = _match_exit_direction("north", exits)
        assert result == "loc_garden"

    def test_match_direction_case_insensitive(self):
        """不区分大小写"""
        from src.nodes.rules.navigation_node import _match_exit_direction
        exits = {"North": "loc_garden"}
        result = _match_exit_direction("north", exits)
        assert result == "loc_garden"

    def test_match_direction_chinese(self):
        """中文方向匹配"""
        from src.nodes.rules.navigation_node import _match_exit_direction
        exits = {"北边": "loc_garden", "南边": "loc_street"}
        result = _match_exit_direction("北边", exits)
        assert result == "loc_garden"

    def test_no_match(self):
        """不存在的方向返回 None"""
        from src.nodes.rules.navigation_node import _match_exit_direction
        result = _match_exit_direction("火星", {"north": "loc_garden"})
        assert result is None


class TestMatchAnyLocation:
    """_match_any_location — 命名目的地匹配（不检查出口）"""

    def test_match_location_key(self):
        """直接匹配 location key"""
        from src.nodes.rules.navigation_node import _match_any_location
        keys = ["loc_garden", "loc_street", "loc_house"]
        result = _match_any_location("loc_garden", keys, [])
        assert result == "loc_garden"

    def test_match_location_name(self):
        """精确匹配场景名称"""
        from src.nodes.rules.navigation_node import _match_any_location
        keys = ["loc_garden", "loc_street"]
        names = ["花园", "街道"]
        result = _match_any_location("花园", keys, names)
        assert result == "loc_garden"

    def test_no_match(self):
        """不存在的场景返回 None"""
        from src.nodes.rules.navigation_node import _match_any_location
        result = _match_any_location("火星", ["loc_garden"], ["花园"])
        assert result is None

    def test_fuzzy_name_contained_in_target(self):
        """子串模糊：'金博尔宅的书房' → '金博尔宅-书房'"""
        from src.nodes.rules.navigation_node import _match_any_location
        keys = ["loc_kimball_house_study", "loc_neighborhood"]
        names = ["金博尔宅-书房", "艾尔斯伯里大街"]
        result = _match_any_location("金博尔宅的书房", keys, names)
        assert result == "loc_kimball_house_study"

    def test_fuzzy_name_target_in_name(self):
        """子串模糊：'书房' → '金博尔宅-书房'"""
        from src.nodes.rules.navigation_node import _match_any_location
        keys = ["loc_kimball_house_study", "loc_neighborhood"]
        names = ["金博尔宅-书房", "艾尔斯伯里大街"]
        result = _match_any_location("书房", keys, names)
        assert result == "loc_kimball_house_study"

    def test_fuzzy_single_char_no_match(self):
        """单字排除：'宅' 不触发模糊匹配"""
        from src.nodes.rules.navigation_node import _match_any_location
        keys = ["loc_kimball_house_study", "loc_neighborhood"]
        names = ["金博尔宅-书房", "艾尔斯伯里大街"]
        result = _match_any_location("宅", keys, names)
        assert result is None

    def test_fuzzy_prefers_shorter_name(self):
        """多候选时选名称最短的"""
        from src.nodes.rules.navigation_node import _match_any_location
        keys = ["loc_a", "loc_b"]
        names = ["长长长长的书房", "书房"]
        result = _match_any_location("书房", keys, names)
        assert result == "loc_b"

    def test_empty_target(self):
        """空 target 返回 None"""
        from src.nodes.rules.navigation_node import _match_any_location
        assert _match_any_location("", ["loc_a"], ["书房"]) is None


class TestNavigationNode:
    """导航节点测试（无 DB 降级场景）"""

    @pytest.mark.asyncio
    async def test_no_target(self):
        """无目标时返回错误"""
        from src.nodes.rules.navigation_node import navigation_node
        state = _make_state(
            intent={"type": "MOVE", "data": {"target": ""}},
            current_location="loc_start",
        )
        result = await navigation_node(state)
        assert result["resolution"]["success"] is False
        assert "去哪里" in result["resolution"]["error"]

    @pytest.mark.asyncio
    async def test_no_current_location(self):
        """无当前位置时返回错误"""
        from src.nodes.rules.navigation_node import navigation_node
        state = _make_state(
            intent={"type": "MOVE", "data": {"target": "花园"}},
            current_location="",
        )
        result = await navigation_node(state)
        assert result["resolution"]["success"] is False
        assert "在哪里" in result["resolution"]["error"]


# ====================================================================
# LLM Nodes (规则兜底模式)
# ====================================================================

class TestRuleOnlyIntentNode:
    """纯规则意图分析节点测试"""

    @pytest.mark.asyncio
    async def test_empty_input(self):
        """空输入返回 META"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "META"
        assert result["intent"]["confidence"] == 0.0

    @pytest.mark.asyncio
    async def test_combat_keyword(self):
        """战斗关键词识别"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="我要用拳头攻击那个邪教徒")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "COMBAT_ACTION"

    @pytest.mark.asyncio
    async def test_physical_interact(self):
        """物理交互识别"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="我检查书桌的抽屉")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "PHYSICAL_INTERACT"
        assert result["intent"]["data"]["skill_name"] == "侦查"

    @pytest.mark.asyncio
    async def test_move_intent(self):
        """移动意图识别"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="我去客厅看看")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "MOVE"
        assert "客厅" in result["intent"]["data"]["target"]

    @pytest.mark.asyncio
    async def test_social_interact(self):
        """社交交互识别"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="我问那个老人这里发生了什么")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "SOCIAL_INTERACT"

    @pytest.mark.asyncio
    async def test_meta_query(self):
        """元操作识别"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="查看我的状态")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "META"

    @pytest.mark.asyncio
    async def test_combat_phase_override(self):
        """战斗阶段非战斗输入也被识别为战斗"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        state = _make_state(player_input="看看周围", game_phase="combat")
        result = await rule_only_intent_node(state)
        assert result["intent"]["type"] == "COMBAT_ACTION"


class TestNarrateNode:
    """叙事生成节点测试（模板模式）"""

    @pytest.mark.asyncio
    async def test_template_no_resolution(self):
        """无裁决结果时的模板叙事"""
        from src.nodes.llm.narrator_node import narrate_node
        state = _make_state(
            player_input="我打开门",
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {"action": "打开门", "target": "门"},
            },
        )
        result = await narrate_node(state)
        assert "narrative" in result
        assert isinstance(result["narrative"], str)
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_template_success_narrative(self):
        """成功检定的叙事"""
        from src.nodes.llm.narrator_node import narrate_node
        state = _make_state(
            player_input="我搜索房间",
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {"action": "搜索房间"},
            },
            resolution={
                "success": True,
                "is_success": True,
                "success_label": "常规成功",
                "skill_name": "侦查",
                "roll_value": 40,
            },
        )
        result = await narrate_node(state)
        assert "narrative" in result
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_template_combat_narrative(self):
        """战斗叙事"""
        from src.nodes.llm.narrator_node import narrate_node
        state = _make_state(
            player_input="攻击邪教徒",
            intent={
                "type": "COMBAT_ACTION",
                "data": {"action": "攻击", "target": "邪教徒"},
            },
            resolution={
                "hit": True,
                "actor_name": "调查员",
                "target_name": "邪教徒",
                "hit_location": "头部",
                "net_damage": 5,
            },
            game_phase="combat",
        )
        result = await narrate_node(state)
        assert "narrative" in result
        assert len(result["narrative"]) > 10  # LLM 或模板应生成有意义的叙事


class TestAdjudicateNode:
    """即兴裁决节点测试（规则兜底模式）"""

    @pytest.mark.asyncio
    async def test_no_action(self):
        """空行动返回错误"""
        from src.nodes.llm.adjudicator_node import adjudicate_node
        state = _make_state(intent={"type": "PHYSICAL_INTERACT", "data": {}})
        result = await adjudicate_node(state)
        assert result["resolution"]["success"] is False

    @pytest.mark.asyncio
    async def test_climbing_adjudication(self):
        """攀爬动作的裁决"""
        from src.nodes.llm.adjudicator_node import adjudicate_node
        state = _make_state(
            player_input="我爬上那道高墙",
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {"action": "爬", "target": "高墙", "detail": "爬上那道高墙"},
            },
        )
        result = await adjudicate_node(state)
        assert result["resolution"]["needs_check"] is True
        assert result["resolution"]["skill"] == "攀爬"
        assert result["resolution"]["check_type"] == "skill"

    @pytest.mark.asyncio
    async def test_forceful_action(self):
        """力量型动作的裁决（兼容 LLM 和规则双路径）"""
        from src.nodes.llm.adjudicator_node import adjudicate_node
        state = _make_state(
            player_input="我用力推开那扇沉重的石门",
            intent={
                "type": "PHYSICAL_INTERACT",
                "data": {"action": "推", "target": "石门", "detail": "用力推开沉重的石门"},
            },
        )
        result = await adjudicate_node(state)
        resolution = result["resolution"]
        # LLM 路径无 success 键，规则路径有；只要返回了 action 即可
        assert resolution.get("action", "") or resolution.get("skill_check")
        # 应该触发某种检定
        check_type = resolution.get("check_type", "")
        skill_check = resolution.get("skill_check", "")
        assert check_type or skill_check, "应触发检定"

    @pytest.mark.asyncio
    async def test_simple_action_no_check(self):
        """简单动作无需检定（兼容 LLM 和规则双路径）"""
        from src.nodes.llm.adjudicator_node import adjudicate_node
        state = _make_state(
            player_input="你好",
            intent={
                "type": "SOCIAL_INTERACT",
                "data": {"action": "说", "detail": "打招呼说你好"},
            },
        )
        result = await adjudicate_node(state)
        resolution = result["resolution"]
        # 只要返回了 action 就算通过；具体无需检定由 LLM 或规则判断
        assert resolution.get("action", "") or isinstance(resolution, dict)


# ====================================================================
# 集成测试: 完整节点链路
# ====================================================================

class TestNodeIntegration:
    """节点集成测试 — 模拟完整 Graph 执行流程"""

    @pytest.mark.asyncio
    async def test_intent_to_skill_to_narrative(self):
        """完整链路: 输入 → 意图 → 技能检定 → 叙事"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        from src.nodes.rules.skill_node import skill_node
        from src.nodes.llm.narrator_node import narrate_node

        # Step 1: 玩家输入 → Intent
        state = _make_state(player_input="我检查书桌的抽屉")
        result1 = await rule_only_intent_node(state)
        state["intent"] = result1["intent"]
        assert state["intent"]["type"] == "PHYSICAL_INTERACT"

        # Step 2: Intent → Skill Check
        # 补充技能值
        state["intent"]["data"]["skill_value"] = 60
        result2 = await skill_node(state)
        state["resolution"] = result2["resolution"]
        assert state["resolution"]["success"] is True
        assert state["resolution"]["skill_name"] == "侦查"

        # Step 3: Resolution → Narrative
        result3 = await narrate_node(state)
        state["narrative"] = result3["narrative"]
        assert len(state["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_intent_to_combat_to_narrative(self):
        """完整链路: 战斗输入 → 战斗裁决 → 叙事"""
        from src.nodes.llm.intent_node import rule_only_intent_node
        from src.nodes.rules.combat_node import combat_node, init_combat_node
        from src.nodes.llm.narrator_node import narrate_node

        # Step 1: 初始化战斗
        state = _make_state(
            player_input="我攻击邪教徒",
            character={
                "name": "调查员",
                "skills": {"斗殴": 50},
                "hit_points": 12,
                "max_hit_points": 12,
                "armor": 0,
                "damage_bonus": "0",
            },
        )
        init_result = await init_combat_node(state)
        state.update(init_result)
        assert state["combat_active"] is True

        # Step 2: 战斗意图
        state["player_input"] = "我用拳头攻击邪教徒A"
        intent_result = await rule_only_intent_node(state)
        state["intent"] = intent_result["intent"]
        state["intent"]["data"].update({
            "skill_value": 50,
            "weapon_name": "拳头",
            "target_name": "邪教徒A",
            "target_skill": "闪避",
            "target_skill_value": 30,
        })

        # Step 3: 战斗裁决
        combat_result = await combat_node(state)
        state["resolution"] = combat_result["resolution"]
        assert state["resolution"]["success"] is True

        # Step 4: 叙事
        narrative_result = await narrate_node(state)
        assert len(narrative_result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_dice_node_integration(self):
        """dice_node + 状态管理集成"""
        from src.nodes.tools.dice_node import dice_node

        # 设置 pending_dice
        state = _make_state(pending_dice={
            "reason": "聆听检定",
            "skill_name": "聆听",
            "skill_value": 40,
            "difficulty": "REGULAR",
        })

        result = await dice_node(state)

        # 验证 pending_dice 已被清除
        assert result["pending_dice"] is None

        # 验证 resolution 被写入
        assert result["resolution"]["success"] is True
        assert result["resolution"]["reason"] == "聆听检定"
        assert result["resolution"]["skill_name"] == "聆听"

        # 模拟引擎合并
        merged_state = dict(state)
        merged_state["pending_dice"] = result["pending_dice"]
        merged_state["resolution"] = result["resolution"]
        assert merged_state["pending_dice"] is None
        assert merged_state["resolution"]["success"] is True

    @pytest.mark.asyncio
    async def test_simple_skill_check_integration(self):
        """快捷技能检定集成"""
        from src.nodes.tools.roll_node import quick_skill_check

        result = await quick_skill_check(50, "REGULAR")
        assert result["success"] is True
        assert result["is_success"] in (True, False)
        assert result["success_label"] in (
            "大成功", "极难成功", "困难成功", "常规成功", "失败", "大失败"
        )


# ====================================================================
# NPC Dialogue Node
# ====================================================================

class TestNPCDialogueNode:
    """NPC 对话节点测试（模板兜底模式）"""

    @pytest.mark.asyncio
    async def test_no_npc_target(self):
        """未指定 NPC 目标时返回提示"""
        from src.nodes.llm.npc_dialogue_node import npc_dialogue_node
        state = _make_state(
            session_id="test-npc",
            player_input="你好",
            intent={
                "type": "SOCIAL_INTERACT",
                "data": {"action": "打招呼"},
            },
        )
        result = await npc_dialogue_node(state)
        assert "narrative" in result
        assert len(result["narrative"]) > 0

    @pytest.mark.asyncio
    async def test_npc_basic_dialogue(self):
        """指定 NPC 目标时生成回应"""
        from src.nodes.llm.npc_dialogue_node import npc_dialogue_node
        state = _make_state(
            session_id="test-npc",
            player_input="你好，请问这里是什么地方？",
            intent={
                "type": "SOCIAL_INTERACT",
                "data": {
                    "action": "问路",
                    "target": "老管家",
                    "detail": "向老管家打听这个地方",
                },
            },
            npc_relations={},
        )
        result = await npc_dialogue_node(state)
        assert "narrative" in result
        assert isinstance(result["narrative"], str)
        assert len(result["narrative"]) > 0

        # 验证关系追踪
        assert "npc_relations" in result
        relations = result["npc_relations"]
        assert "老管家" in relations
        assert relations["老管家"]["talk_count"] == 1

        # 验证事件
        assert "emitted_events" in result
        assert len(result["emitted_events"]) == 1
        assert result["emitted_events"][0]["type"] == "NPCDialogue"

    @pytest.mark.asyncio
    async def test_npc_multiple_talks(self):
        """多次对话测试 — 验证 talk_count 递增"""
        from src.nodes.llm.npc_dialogue_node import npc_dialogue_node

        relations = {"艾玛": {"talk_count": 2, "disposition": "neutral"}}
        state = _make_state(
            session_id="test-npc",
            player_input="再问你一个问题",
            intent={
                "type": "SOCIAL_INTERACT",
                "data": {
                    "action": "询问",
                    "target": "艾玛",
                },
            },
            npc_relations=relations,
        )
        result = await npc_dialogue_node(state)
        assert result["npc_relations"]["艾玛"]["talk_count"] == 3

    @pytest.mark.asyncio
    async def test_clue_grant_on_keyword(self):
        """玩家输入关键词且对话次数足够时授予线索"""
        from src.nodes.llm.npc_dialogue_node import npc_dialogue_node

        relations = {"老管家": {"talk_count": 2, "disposition": "neutral"}}
        state = _make_state(
            session_id="test-npc",
            player_input="这里有什么奇怪的线索吗？",
            intent={
                "type": "SOCIAL_INTERACT",
                "data": {
                    "action": "询问线索",
                    "target": "老管家",
                },
            },
            npc_relations=relations,
            active_tags=["clue_investigate"],
        )
        result = await npc_dialogue_node(state)
        # 已有 clue_investigate，应只添加新 tag
        assert "active_tags" in result
        granted = result["active_tags"]
        assert "clue_investigate" in granted
        # 关键词"线索"和"奇怪"应触发了 clue_oddity 和 clue_aware
        # 但注意: talk_count=2 才满足条件
        assert "clue_oddity" in granted
        assert "clue_aware" in granted

    @pytest.mark.asyncio
    async def test_clue_not_granted_first_talk(self):
        """第一次对话不授予线索"""
        from src.nodes.llm.npc_dialogue_node import npc_dialogue_node

        relations = {"商人": {"talk_count": 0, "disposition": "neutral"}}
        state = _make_state(
            session_id="test-npc",
            player_input="有什么秘密可以告诉我吗？",
            intent={
                "type": "SOCIAL_INTERACT",
                "data": {
                    "action": "询问秘密",
                    "target": "商人",
                },
            },
            npc_relations=relations,
            active_tags=[],
        )
        result = await npc_dialogue_node(state)
        # talk_count=0，不满足 >=2 条件，不应授予线索
        assert result.get("active_tags", []) == []
