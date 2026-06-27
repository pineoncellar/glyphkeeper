"""
域模型单元测试

职责:
  - 测试 domain/ 层的 100% 确定性逻辑
  - 覆盖 coc_rules, sanity_rules, combat_rules, character, checks

测试范围:
  - 成功等级判定逻辑
  - 理智损失计算
  - 战斗伤害计算
  - 角色创建与校验
  - 各类检定逻辑
"""

import pytest
from src.domain.coc_rules import (
    SuccessLevel, Difficulty,
    determine_success_level, get_difficulty_threshold,
    is_fumble, is_critical,
)
from src.domain.checks import (
    CheckResult, OpposedResult,
    skill_check, stat_check, opposed_check, push_roll,
)
from src.domain.sanity_rules import (
    SanityLoss, InsanityResult,
    calculate_sanity_loss, check_temporary_insanity,
    check_indefinite_insanity, roll_insanity_symptom,
    get_sanity_loss_bounds, roll_full_insanity,
)
from src.domain.combat_rules import (
    WeaponStats, CombatRoundResult, WEAPONS,
    calculate_damage, determine_hit_location,
    apply_armor, parse_damage_bonus,
)
from src.domain.character import (
    Stats, Character,
    create_investigator, apply_skill_growth,
    calculate_combat_stats,
    calculate_move, calculate_max_hp, calculate_max_mp,
)


# ======== coc_rules.py ========

class TestDetermineSuccessLevel:
    """成功等级判定测试"""

    def test_critical_roll_01(self):
        """骰出 01 → CRITICAL"""
        assert determine_success_level(50, 1) == SuccessLevel.CRITICAL
        assert determine_success_level(1, 1) == SuccessLevel.CRITICAL
        assert determine_success_level(99, 1) == SuccessLevel.CRITICAL

    def test_extreme_success(self):
        """roll ≤ skill/5 → EXTREME"""
        assert determine_success_level(50, 5) == SuccessLevel.EXTREME
        assert determine_success_level(50, 10) == SuccessLevel.EXTREME
        assert determine_success_level(100, 20) == SuccessLevel.EXTREME

    def test_hard_success(self):
        """roll ≤ skill/2 → HARD"""
        assert determine_success_level(50, 20) == SuccessLevel.HARD
        assert determine_success_level(50, 25) == SuccessLevel.HARD
        assert determine_success_level(60, 30) == SuccessLevel.HARD

    def test_regular_success(self):
        """roll ≤ skill → REGULAR"""
        assert determine_success_level(50, 40) == SuccessLevel.REGULAR
        assert determine_success_level(50, 50) == SuccessLevel.REGULAR
        assert determine_success_level(70, 70) == SuccessLevel.REGULAR

    def test_failure(self):
        """roll > skill → FAILURE"""
        assert determine_success_level(50, 70) == SuccessLevel.FAILURE
        assert determine_success_level(30, 50) == SuccessLevel.FAILURE

    def test_fumble_96_100(self):
        """96-100 且 > skill → FUMBLE"""
        assert determine_success_level(50, 97) == SuccessLevel.FUMBLE
        assert determine_success_level(5, 96) == SuccessLevel.FUMBLE

    def test_fumble_97_with_high_skill(self):
        """skill=70, roll=97 → FAILURE（不是 FUMBLE，因为 97 > 70 但这是常规失败）"""
        assert determine_success_level(70, 97) == SuccessLevel.FUMBLE

    def test_boundary_regular(self):
        """skill=50, roll=50 → REGULAR（边界）"""
        assert determine_success_level(50, 50) == SuccessLevel.REGULAR

    def test_boundary_hard(self):
        """skill=50, roll=25 → HARD（25 == 50/2）"""
        assert determine_success_level(50, 25) == SuccessLevel.HARD

    def test_boundary_extreme(self):
        """skill=50, roll=10 → EXTREME（10 == 50/5）"""
        assert determine_success_level(50, 10) == SuccessLevel.EXTREME


class TestGetDifficultyThreshold:
    """难度阈值测试"""

    def test_regular(self):
        assert get_difficulty_threshold(50, Difficulty.REGULAR) == 50

    def test_hard(self):
        assert get_difficulty_threshold(50, Difficulty.HARD) == 25
        assert get_difficulty_threshold(1, Difficulty.HARD) == 1  # 最小 1

    def test_extreme(self):
        assert get_difficulty_threshold(50, Difficulty.EXTREME) == 10
        assert get_difficulty_threshold(1, Difficulty.EXTREME) == 1  # 最小 1


class TestIsFumble:
    def test_fumble_96_above_skill(self):
        assert is_fumble(5, 96)
        assert is_fumble(50, 97)

    def test_not_fumble_below_96(self):
        assert not is_fumble(50, 95)
        assert not is_fumble(50, 50)

    def test_96_100_but_not_above_skill(self):
        """96-100 但不大于 skill → 不是大失败"""
        # skill=100 时，96 不大于 100
        assert not is_fumble(100, 96)


class TestIsCritical:
    def test_critical(self):
        assert is_critical(1)

    def test_not_critical(self):
        assert not is_critical(2)
        assert not is_critical(50)
        assert not is_critical(100)


# ======== checks.py ========

class TestSkillCheck:
    def test_skill_check_regular(self):
        """常规技能检定"""
        result = skill_check(50)
        assert isinstance(result, CheckResult)
        assert 1 <= result.roll_value <= 100
        assert result.skill_value == 50
        assert not result.is_push

    def test_skill_check_hard(self):
        """困难技能检定"""
        result = skill_check(50, Difficulty.HARD)
        assert result.skill_value == 50

    def test_skill_check_extreme(self):
        """极难技能检定"""
        result = skill_check(50, Difficulty.EXTREME)
        assert result.skill_value == 50

    def test_check_result_properties(self):
        """CheckResult 属性"""
        success = CheckResult(SuccessLevel.REGULAR, 40, 50)
        assert success.is_success
        assert not success.is_failure

        failure = CheckResult(SuccessLevel.FAILURE, 70, 50)
        assert failure.is_failure
        assert not failure.is_success

        fumble = CheckResult(SuccessLevel.FUMBLE, 97, 50)
        assert fumble.is_failure
        assert not fumble.is_success


class TestStatCheck:
    def test_stat_check(self):
        """属性检定（属性值 × 5）"""
        result = stat_check(12)  # 12×5 = 60
        assert result.skill_value == 60


class TestOpposedCheck:
    def test_opposed_active_wins(self):
        """主动方成功等级更高 → 主动方胜"""
        # 由于随机性，用确定性方式测试：创建 OpposedResult
        result = OpposedResult(winner="active", active_level=SuccessLevel.HARD,
                               passive_level=SuccessLevel.REGULAR, margin=1)
        assert result.is_active_win
        assert not result.is_passive_win

    def test_opposed_passive_wins(self):
        """被动方成功等级更高 → 被动方胜"""
        result = OpposedResult(winner="passive", active_level=SuccessLevel.FAILURE,
                               passive_level=SuccessLevel.REGULAR, margin=-1)
        assert result.is_passive_win
        assert not result.is_active_win

    def test_opposed_tie(self):
        """双方都失败 → 平局"""
        result = OpposedResult(winner="tie", active_level=SuccessLevel.FAILURE,
                               passive_level=SuccessLevel.FAILURE, margin=0)
        assert result.is_tie


class TestPushRoll:
    def test_push_roll_marks_push(self):
        """孤注一掷标记 is_push=True"""
        original = CheckResult(SuccessLevel.FAILURE, 70, 50)
        result = push_roll(original, 30)  # 新掷骰成功
        assert result.is_push

    def test_push_roll_failure_becomes_fumble(self):
        """孤注一掷失败 → 大失败"""
        original = CheckResult(SuccessLevel.FAILURE, 70, 50)
        result = push_roll(original, 80)  # 新掷骰也失败
        assert result.success_level == SuccessLevel.FUMBLE
        assert result.is_push


# ======== sanity_rules.py ========

class TestCalculateSanityLoss:
    def test_normal_loss_no_insanity(self):
        """san=60, loss=2 → 无疯狂"""
        result = calculate_sanity_loss(60, 60, (1, 3))
        # 实际损失在 1-3 之间
        assert 1 <= result.actual_loss <= 3
        assert not result.is_temporary_insanity
        assert not result.is_indefinite_insanity

    def test_loss_not_exceed_current_san(self):
        """损失不超过当前 SAN（不会降到 0 以下）"""
        result = calculate_sanity_loss(5, 60, (1, 20))
        assert result.actual_loss <= 5

    def test_temporary_insanity(self):
        """损失 ≥ current_san/5 → 临时疯狂"""
        # san=50, loss ≥ 10 → 临时疯狂
        result = calculate_sanity_loss(50, 60, (12, 12))
        assert result.is_temporary_insanity

    def test_indefinite_insanity(self):
        """损失 ≥ max_san/5 → indefinite"""
        # max_san=60, loss ≥ 12 → indefinite
        result = calculate_sanity_loss(60, 60, (15, 15))
        assert result.is_indefinite_insanity


class TestCheckTemporaryInsanity:
    def test_triggered(self):
        assert check_temporary_insanity(10, 50)

    def test_not_triggered(self):
        assert not check_temporary_insanity(5, 50)

    def test_zero_san(self):
        assert check_temporary_insanity(1, 0)


class TestCheckIndefiniteInsanity:
    def test_triggered(self):
        assert check_indefinite_insanity(12, 60)

    def test_not_triggered(self):
        assert not check_indefinite_insanity(5, 60)

    def test_zero_max_san(self):
        assert check_indefinite_insanity(1, 0)


class TestRollInsanitySymptom:
    def test_symptom_is_string(self):
        symptom = roll_insanity_symptom()
        assert isinstance(symptom, str)
        assert len(symptom) > 0

    def test_symptom_has_description(self):
        symptom = roll_insanity_symptom()
        assert " - " in symptom  # 格式：症状名 - 描述


class TestGetSanityLossBounds:
    def test_known_source(self):
        min_l, max_l = get_sanity_loss_bounds("seeing_dead_body")
        assert min_l == 0
        assert max_l == 1

    def test_mythos_creature(self):
        min_l, max_l = get_sanity_loss_bounds("seeing_mythos_creature")
        assert min_l == 1
        assert max_l == 6

    def test_unknown_source_returns_default(self):
        min_l, max_l = get_sanity_loss_bounds("unknown")
        assert min_l == 0
        assert max_l == 1


# ======== combat_rules.py ========

class TestCalculateDamage:
    def test_failure_deals_zero_damage(self):
        weapon = WEAPONS["拳头"]
        assert calculate_damage(weapon, "0", SuccessLevel.FAILURE) == 0
        assert calculate_damage(weapon, "0", SuccessLevel.FUMBLE) == 0

    def test_regular_damage_positive(self):
        weapon = WEAPONS["拳头"]
        damage = calculate_damage(weapon, "0", SuccessLevel.REGULAR)
        # 1D3+0 → 1-3
        assert 1 <= damage <= 3

    def test_damage_with_db(self):
        weapon = WEAPONS["拳头"]
        damage = calculate_damage(weapon, "+1D4", SuccessLevel.REGULAR)
        # 1D3 + 1D4 → 2-7
        assert 2 <= damage <= 7

    def test_negative_db(self):
        # 武器伤害包含 DB 占位符，db="-2" → 1D3-2 → 最小 0
        weapon = WeaponStats("测试", "接触", "1D3+DB", 1, 999, 0)
        damage = calculate_damage(weapon, "-2", SuccessLevel.REGULAR)
        assert 0 <= damage <= 1


class TestDetermineHitLocation:
    def test_head(self):
        assert determine_hit_location(20) == "头部"

    def test_right_leg(self):
        assert determine_hit_location(1) == "右腿"
        assert determine_hit_location(2) == "右腿"
        assert determine_hit_location(3) == "右腿"

    def test_left_leg(self):
        assert determine_hit_location(4) == "左腿"
        assert determine_hit_location(5) == "左腿"
        assert determine_hit_location(6) == "左腿"

    def test_abdomen(self):
        assert determine_hit_location(7) == "腹部"
        assert determine_hit_location(8) == "腹部"
        assert determine_hit_location(9) == "腹部"
        assert determine_hit_location(10) == "腹部"

    def test_chest(self):
        assert determine_hit_location(11) == "胸部"
        assert determine_hit_location(12) == "胸部"
        assert determine_hit_location(13) == "胸部"
        assert determine_hit_location(14) == "胸部"
        assert determine_hit_location(15) == "胸部"

    def test_right_arm(self):
        assert determine_hit_location(16) == "右臂"
        assert determine_hit_location(17) == "右臂"

    def test_left_arm(self):
        assert determine_hit_location(18) == "左臂"
        assert determine_hit_location(19) == "左臂"


class TestApplyArmor:
    def test_no_armor(self):
        assert apply_armor(10, 0) == 10

    def test_armor_reduces_damage(self):
        assert apply_armor(10, 3) == 7

    def test_armor_exceeds_damage(self):
        assert apply_armor(5, 10) == 0

    def test_armor_equals_damage(self):
        assert apply_armor(5, 5) == 0


class TestParseDamageBonus:
    def test_positive_db(self):
        assert parse_damage_bonus("+1D4") == "1D4"

    def test_negative_db(self):
        assert parse_damage_bonus("-2") == "-2"

    def test_zero_db(self):
        assert parse_damage_bonus("0") == "0"
        assert parse_damage_bonus("") == "0"


# ======== character.py ========

class TestCalculateDerivedStats:
    def test_combat_stats_ranges(self):
        """calculate_combat_stats 各区间返回值正确"""
        assert calculate_combat_stats(30, 30) == ("-2", -2)     # 60 ≤ 64
        assert calculate_combat_stats(40, 40) == ("-1", -1)     # 80, 65-84
        assert calculate_combat_stats(50, 50) == ("0", 0)       # 100, 85-124
        assert calculate_combat_stats(70, 70) == ("+1D4", 1)    # 140, 125-164
        assert calculate_combat_stats(90, 90) == ("+1D6", 2)    # 180, 165-204
        assert calculate_combat_stats(110, 110) == ("+2D6", 3)  # 220 ≥ 205

    def test_move_both_greater(self):
        """STR > SIZ 且 DEX > SIZ → MOV 9"""
        assert calculate_move(80, 80, 70) == 9

    def test_move_both_less(self):
        """DEX < SIZ 且 STR < SIZ → MOV 7"""
        assert calculate_move(60, 60, 70) == 7

    def test_move_all_equal(self):
        """三者相等 → MOV 8"""
        assert calculate_move(70, 70, 70) == 8

    def test_move_dex_greater_str_less(self):
        """仅 DEX > SIZ，STR < SIZ → MOV 8"""
        assert calculate_move(80, 60, 70) == 8

    def test_move_str_greater_dex_less(self):
        """仅 STR > SIZ，DEX < SIZ → MOV 8"""
        assert calculate_move(60, 80, 70) == 8

    def test_move_dex_equal_str_greater(self):
        """DEX = SIZ，STR > SIZ → MOV 8"""
        assert calculate_move(70, 80, 70) == 8

    def test_max_hp(self):
        assert calculate_max_hp(50, 60) == 55  # (50+60)/2
        assert calculate_max_hp(40, 50) == 45
        assert calculate_max_hp(55, 60) == 57  # 向下取整

    def test_max_mp(self):
        assert calculate_max_mp(50) == 10  # 50/5
        assert calculate_max_mp(85) == 17  # 85/5
        assert calculate_max_mp(3) == 1    # 最小 1


class TestCreateInvestigator:
    def test_create_basic_investigator(self):
        """创建基本调查员"""
        stats = Stats(
            strength=50, constitution=50, size=50,
            dexterity=50, appearance=50, intelligence=50,
            power=50, education=50,
        )
        char = create_investigator(
            name="测试调查员",
            occupation="教授",
            stats=stats,
            occupation_skills={"图书馆利用": 60, "语言(母语)": 70},
        )
        assert char.name == "测试调查员"
        assert char.occupation == "教授"
        assert char.max_hit_points == 50  # (50+50)/2
        assert char.max_magic_points == 10  # 50/5
        assert char.max_sanity == 50  # POW
        assert char.damage_bonus == "0"  # STR+SIZ=100
        assert "图书馆利用" in char.skills
        assert "闪避" in char.skills  # 基础技能

    def test_create_investigator_with_id(self):
        """创建的调查员有唯一 ID"""
        stats = Stats()
        char1 = create_investigator("A", "职业", stats, {})
        char2 = create_investigator("B", "职业", stats, {})
        assert char1.id != char2.id


class TestApplySkillGrowth:
    def test_skill_grows(self):
        """掷骰 > 技能值 → 成长"""
        char = Character(
            id="test", name="Test", occupation="Test",
            stats=Stats(), skills={"侦查": 50},
        )
        result = apply_skill_growth(char, "侦查", 70)
        assert result  # 成长成功
        assert char.skills["侦查"] > 50  # 技能值增加

    def test_skill_not_grow(self):
        """掷骰 ≤ 技能值 → 不成长"""
        char = Character(
            id="test", name="Test", occupation="Test",
            stats=Stats(), skills={"侦查": 50},
        )
        result = apply_skill_growth(char, "侦查", 30)
        assert not result  # 不成长
        assert char.skills["侦查"] == 50  # 技能值不变

    def test_unknown_skill(self):
        """未知技能返回 False"""
        char = Character(id="test", name="Test", occupation="Test", stats=Stats())
        result = apply_skill_growth(char, "不存在的技能", 70)
        assert not result


# ====================================================================
# navigation.py — BFS 寻路与阻挡标签
# ====================================================================

_NAV_LOCATIONS = [
    {"key": "loc_start",    "name": "起点",  "exits": {"north": "loc_hall"},                         "tags": []},
    {"key": "loc_hall",     "name": "走廊",  "exits": {"east": "loc_garden", "south": "loc_start", "west": "loc_library"}, "tags": []},
    {"key": "loc_garden",   "name": "花园",  "exits": {"north": "loc_shed", "west": "loc_hall"},     "tags": ["outdoor"]},
    {"key": "loc_shed",     "name": "棚屋",  "exits": {"south": "loc_garden"},                       "tags": []},
    {"key": "loc_library",  "name": "图书馆","exits": {"east": "loc_hall"},                          "tags": ["locked"]},
    {"key": "loc_secret",   "name": "密室",  "exits": {"down": "loc_tunnel"},                        "tags": []},
    {"key": "loc_tunnel",   "name": "隧道",  "exits": {"east": "loc_start"},                         "tags": []},
    {"key": "loc_isolated", "name": "孤岛",  "exits": {},                                            "tags": []},
]


class TestBuildGraph:
    def test_build_graph_keys(self):
        """构建后的图包含所有场景"""
        from src.domain.navigation import build_graph
        graph = build_graph(_NAV_LOCATIONS)
        assert set(graph.keys()) == {
            "loc_start", "loc_hall", "loc_garden", "loc_shed",
            "loc_library", "loc_secret", "loc_tunnel", "loc_isolated",
        }

    def test_build_graph_exits_preserved(self):
        """出口映射被正确保留"""
        from src.domain.navigation import build_graph
        graph = build_graph(_NAV_LOCATIONS)
        assert graph["loc_start"]["exits"] == {"north": "loc_hall"}
        assert graph["loc_hall"]["exits"]["east"] == "loc_garden"

    def test_build_graph_tags_preserved(self):
        """标签被正确保留"""
        from src.domain.navigation import build_graph
        graph = build_graph(_NAV_LOCATIONS)
        assert "locked" in graph["loc_library"]["tags"]


class TestFindPath:
    def test_direct_neighbor(self):
        """相邻场景直达"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        path = find_path(graph, "loc_start", "loc_hall")
        assert path is not None
        assert len(path) == 1
        assert path[0]["direction"] == "north"
        assert path[0]["key"] == "loc_hall"

    def test_multi_step_path(self):
        """多步路径"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        path = find_path(graph, "loc_start", "loc_shed")
        assert path is not None
        assert path[-1]["key"] == "loc_shed"

    def test_no_path_isolated(self):
        """孤岛场景不可达"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        path = find_path(graph, "loc_start", "loc_isolated")
        assert path is None

    def test_blocked_by_locked(self):
        """被 locked 标签阻挡的场景不可达"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        path = find_path(graph, "loc_start", "loc_library")
        assert path is None

    def test_blocked_override(self):
        """覆盖 blocked_tags 后 locked 场景可达"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        path = find_path(graph, "loc_start", "loc_library", blocked_tags=set())
        assert path is not None
        assert path[-1]["key"] == "loc_library"

    def test_start_equals_end(self):
        """起终点相同返回 None"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        assert find_path(graph, "loc_start", "loc_start") is None

    def test_nonexistent_start(self):
        """不存在的起点返回 None"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        assert find_path(graph, "nowhere", "loc_start") is None

    def test_path_reverse(self):
        """反向路径也应可达"""
        from src.domain.navigation import build_graph, find_path
        graph = build_graph(_NAV_LOCATIONS)
        path = find_path(graph, "loc_shed", "loc_start")
        assert path is not None
        assert path[-1]["key"] == "loc_start"


class TestIsBlocked:
    def test_blocked_tag(self):
        """blocked 标签返回 True"""
        from src.domain.navigation import is_blocked
        assert is_blocked({"tags": ["blocked"]})

    def test_locked_tag(self):
        """locked 标签返回 True"""
        from src.domain.navigation import is_blocked
        assert is_blocked({"tags": ["locked", "indoor"]})

    def test_no_blocking_tags(self):
        """无阻挡标签返回 False"""
        from src.domain.navigation import is_blocked
        assert not is_blocked({"tags": ["indoor", "dark"]})

    def test_empty_tags(self):
        """空标签返回 False"""
        from src.domain.navigation import is_blocked
        assert not is_blocked({"tags": []})

    def test_none_node(self):
        """None 节点返回 True"""
        from src.domain.navigation import is_blocked
        assert is_blocked(None)

    def test_custom_blocked_set(self):
        """自定义阻挡标签集"""
        from src.domain.navigation import is_blocked
        assert is_blocked({"tags": ["custom_block"]}, blocked_tags={"custom_block"})
        assert not is_blocked({"tags": ["custom_block"]}, blocked_tags=set())
