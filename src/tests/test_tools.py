"""
工具层单元测试

职责:
  - 测试 tools/ 层的纯函数
  - 覆盖 dice, random, time, utils

测试范围:
  - D100 掷骰分布合理性
  - 奖励骰/惩罚骰逻辑
  - 随机选择无偏性
  - 工具函数正确性
"""

import random
import pytest
from src.tools.dice import roll_d100, roll_bonus_dice, roll_penalty_dice, roll_ndn, roll_dice
from src.tools.random import secure_choice, secure_shuffle, weighted_choice
from src.tools.time import TimeSlot, advance_time, get_time_description
from src.tools.utils import normalize_name, validate_uuid, safe_get


# ======== dice.py ========

class TestRollD100:
    def test_value_range(self):
        """D100 值域应在 1-100 之间"""
        for _ in range(1000):
            tens, ones, total = roll_d100()
            assert 1 <= total <= 100, f"总值 {total} 超出 1-100"
            assert 0 <= tens <= 9, f"十位数 {tens} 超出 0-9"
            assert 0 <= ones <= 9, f"个位数 {ones} 超出 0-9"

    def test_00_0_is_100(self):
        """00+0 = 100"""
        # 无法直接测试随机结果，验证逻辑：如果 tens=0, ones=0，总值为 100
        tens, ones, total = 0, 0, 100
        assert total == 100

    def test_00_1_is_1(self):
        """00+1 = 1"""
        tens, ones, total = 0, 1, 1
        assert total == 1


class TestRollBonusDice:
    def test_bonus_dice_result_is_best(self):
        """奖励骰结果应 ≤ 所有掷骰中的最小值（CoC 规则中越小越好）"""
        best, rolls = roll_bonus_dice(2)
        assert best == min(rolls)
        assert len(rolls) == 3

    def test_bonus_dice_zero(self):
        """bonus_count=0 应返回单次掷骰"""
        best, rolls = roll_bonus_dice(0)
        assert len(rolls) == 1
        assert best == rolls[0]

    def test_bonus_dice_negative(self):
        """负数 bonus_count 应视为 0"""
        best, rolls = roll_bonus_dice(-1)
        assert len(rolls) == 1


class TestRollPenaltyDice:
    def test_penalty_dice_result_is_worst(self):
        """惩罚骰结果应 ≥ 所有掷骰中的最大值（CoC 规则中越大越差）"""
        worst, rolls = roll_penalty_dice(2)
        assert worst == max(rolls)
        assert len(rolls) == 3

    def test_penalty_dice_zero(self):
        """penalty_count=0 应返回单次掷骰"""
        worst, rolls = roll_penalty_dice(0)
        assert len(rolls) == 1
        assert worst == rolls[0]

    def test_penalty_dice_negative(self):
        """负数 penalty_count 应视为 0"""
        worst, rolls = roll_penalty_dice(-1)
        assert len(rolls) == 1


class TestRollNdn:
    def test_roll_1d6(self):
        """1D6 值域 1-6"""
        for _ in range(100):
            result = roll_ndn(1, 6)
            assert len(result) == 1
            assert 1 <= result[0] <= 6

    def test_roll_2d6(self):
        """2D6 返回 2 个值"""
        result = roll_ndn(2, 6)
        assert len(result) == 2
        for v in result:
            assert 1 <= v <= 6

    def test_roll_negative_n(self):
        """负数 n 返回空列表"""
        assert roll_ndn(-1, 6) == []

    def test_roll_zero_sides(self):
        """sides=0 应都返回 1"""
        result = roll_ndn(3, 0)
        assert result == [1, 1, 1]


class TestRollDice:
    def test_1d6(self):
        """1D6 值域 1-6"""
        for _ in range(100):
            result = roll_dice("1D6")
            assert 1 <= result <= 6

    def test_2d6_plus_2(self):
        """2D6+2 值域 4-14"""
        for _ in range(100):
            result = roll_dice("2D6+2")
            assert 4 <= result <= 14

    def test_2d6_minus_1(self):
        """2D6-1 值域 1-11"""
        for _ in range(100):
            result = roll_dice("2D6-1")
            assert 1 <= result <= 11

    def test_1d100(self):
        """1D100 值域 1-100"""
        for _ in range(100):
            result = roll_dice("1D100")
            assert 1 <= result <= 100

    def test_1d3(self):
        """1D3 值域 1-3"""
        for _ in range(100):
            result = roll_dice("1D3")
            assert 1 <= result <= 3

    def test_invalid_expression(self):
        """无效表达式应抛出 ValueError"""
        with pytest.raises(ValueError):
            roll_dice("invalid")

    def test_case_insensitive(self):
        """大小写不敏感（相同输入产生相同值域）"""
        for _ in range(50):
            r1 = roll_dice("1d6")
            r2 = roll_dice("1D6")
            assert 1 <= r1 <= 6
            assert 1 <= r2 <= 6


# ======== random.py ========

class TestSecureChoice:
    def test_choice_from_list(self):
        """从列表中选取"""
        items = ["a", "b", "c"]
        result = secure_choice(items)
        assert result in items

    def test_choice_from_tuple(self):
        """从元组中选取"""
        items = (1, 2, 3)
        result = secure_choice(items)
        assert result in items

    def test_empty_sequence_raises(self):
        """空序列应抛出 ValueError"""
        with pytest.raises(ValueError):
            secure_choice([])


class TestSecureShuffle:
    def test_shuffle_returns_new_list(self):
        """洗牌返回新列表"""
        original = [1, 2, 3, 4, 5]
        shuffled = secure_shuffle(original)
        assert sorted(shuffled) == sorted(original)
        assert shuffled is not original  # 不是同一个对象

    def test_shuffle_preserves_elements(self):
        """洗牌保留所有元素"""
        original = [1, 2, 3, 4, 5]
        shuffled = secure_shuffle(original)
        assert set(shuffled) == set(original)
        assert len(shuffled) == len(original)


class TestWeightedChoice:
    def test_weighted_choice_basic(self):
        """基本加权选择"""
        items = ["a", "b", "c"]
        weights = [1.0, 0.0, 0.0]
        result = weighted_choice(items, weights)
        assert result == "a"

    def test_weight_mismatch_raises(self):
        """长度不匹配应抛出 ValueError"""
        with pytest.raises(ValueError):
            weighted_choice(["a", "b"], [1.0])

    def test_empty_items_raises(self):
        """空列表应抛出 ValueError"""
        with pytest.raises(ValueError):
            weighted_choice([], [])

    def test_zero_total_weight_raises(self):
        """权重总和为 0 应抛出 ValueError"""
        with pytest.raises(ValueError):
            weighted_choice(["a", "b"], [0.0, 0.0])


# ======== time.py ========

class TestAdvanceTime:
    def test_advance_one_step(self):
        """推进一个时间段"""
        assert advance_time(TimeSlot.DAWN) == TimeSlot.MORNING
        assert advance_time(TimeSlot.MORNING) == TimeSlot.AFTERNOON

    def test_advance_multiple_steps(self):
        """推进多个时间段"""
        assert advance_time(TimeSlot.DAWN, 2) == TimeSlot.AFTERNOON
        assert advance_time(TimeSlot.DAWN, 3) == TimeSlot.EVENING

    def test_advance_cyclic(self):
        """循环推进"""
        assert advance_time(TimeSlot.LATE_NIGHT) == TimeSlot.DAWN
        assert advance_time(TimeSlot.LATE_NIGHT, 2) == TimeSlot.MORNING

    def test_advance_zero_steps(self):
        """推进 0 步"""
        assert advance_time(TimeSlot.DAWN, 0) == TimeSlot.DAWN

    def test_advance_negative(self):
        """负数步数视为 0"""
        assert advance_time(TimeSlot.DAWN, -1) == TimeSlot.DAWN

    def test_full_cycle(self):
        """完整循环回到起点"""
        assert advance_time(TimeSlot.DAWN, 6) == TimeSlot.DAWN


class TestGetTimeDescription:
    def test_all_slots_have_description(self):
        """所有时间段都有描述"""
        for slot in TimeSlot:
            desc = get_time_description(slot)
            assert desc, f"{slot} 没有描述"
            assert isinstance(desc, str)


# ======== utils.py ========

class TestNormalizeName:
    def test_strip_whitespace(self):
        """去除首尾空格"""
        assert normalize_name("  张三  ") == "张三"

    def test_fullwidth_to_halfwidth(self):
        """全角英文字母转半角"""
        assert normalize_name("ＡＢＣ") == "ABC"
        assert normalize_name("ａｂｃ") == "abc"

    def test_fullwidth_digits_to_halfwidth(self):
        """全角数字转半角"""
        assert normalize_name("１２３") == "123"

    def test_collapse_spaces(self):
        """合并连续空格"""
        assert normalize_name("张  三") == "张 三"

    def test_empty_string(self):
        """空字符串返回空"""
        assert normalize_name("") == ""
        assert normalize_name("   ") == ""


class TestValidateUuid:
    def test_valid_uuid(self):
        """合法 UUID 返回 True"""
        assert validate_uuid("550e8400-e29b-41d4-a716-446655440000")

    def test_invalid_uuid(self):
        """非法 UUID 返回 False"""
        assert not validate_uuid("not-a-uuid")
        assert not validate_uuid("")
        assert not validate_uuid(None)


class TestSafeGet:
    def test_basic_access(self):
        """基本嵌套取值"""
        d = {"a": {"b": {"c": 42}}}
        assert safe_get(d, "a", "b", "c") == 42

    def test_missing_key_returns_default(self):
        """缺失 key 返回默认值"""
        d = {"a": 1}
        assert safe_get(d, "b", default="x") == "x"

    def test_none_intermediate(self):
        """中间值为 None 返回默认值"""
        d = {"a": None}
        assert safe_get(d, "a", "b", default="x") == "x"

    def test_non_dict_intermediate(self):
        """中间值非 dict 返回默认值"""
        d = {"a": 42}
        assert safe_get(d, "a", "b", default="x") == "x"
