import random
import re
from dataclasses import dataclass
from typing import List, Optional

from ..core import get_logger
from ..memory import fetch_model_data

logger = get_logger(__name__)

@dataclass
class DiceResult:
    expression: str
    rolls: List[int]
    modifier: int
    total: int
    details: str

@dataclass
class CheckResult:
    skill_name: str
    advantage: int
    total: int
    success_level: int
    details: str

class DiceRoller:
    @staticmethod
    def roll(expression: str) -> DiceResult:
        """
        简单的掷骰器实现。
        支持 "NdM+X" 或 "NdM-X" 格式。
        """
        # 基础解析 NdM (+/-) X
        match = re.match(r"(\d+)d(\d+)(?:([+-])(\d+))?", expression)
        if not match:
            # 针对单个数字或无效格式的回退处理
            return DiceResult(expression, [], 0, 0, "无效表达式")
            
        num_dice = int(match.group(1)) # 掷骰数量
        sides = int(match.group(2)) # 骰子面数
        op = match.group(3) # 加减符号
        modifier = int(match.group(4)) if match.group(4) else 0 # 修正值
        
        rolls = [random.randint(1, sides) for _ in range(num_dice)]
        total_rolls = sum(rolls)
        
        final_total = total_rolls
        if op == '+':
            final_total += modifier
        elif op == '-':
            final_total -= modifier
            modifier = -modifier
            
        details = f"({' + '.join(map(str, rolls))}){'+' if modifier >= 0 else ''}{modifier} = {final_total}"
        
        return DiceResult(
            expression=expression,
            rolls=rolls,
            modifier=modifier,
            total=final_total,
            details=details
        )

    @staticmethod
    def roll_d100() -> int:
        return random.randint(1, 100)
    
    @staticmethod
    def roll_d10() -> int:
        return random.randint(0, 9)

    @staticmethod
    def check_success(skill_value: int, advantage: int = 0) -> int:
        """
        CoC 7版规则检定逻辑。

        返回：
        0-大成功
        1-极难成功
        2-困难成功
        3-成功
        4-失败
        5-大失败
        """
        roll = DiceRoller.roll_d100()
        
        if roll == 1:
            return 0
        if roll == 100:
            return 5
        
        # 奖励骰处理：掷n次10面骰，取最低值为最终十位数，惩罚骰反之
        # 这里用一个简单的实现，没有严格按规则书来，以后再说
        # TODO: 实现规则书标准的奖励/惩罚骰逻辑
        ten_digit = roll // 10
        unit_digit = roll % 10
        if advantage > 0:
            for _ in range(advantage):
                new_roll = DiceRoller.roll_d100()
                new_ten = new_roll // 10
                if new_ten < ten_digit:
                    ten_digit = new_ten
        elif advantage < 0:
            for _ in range(-advantage):
                new_roll = DiceRoller.roll_d100()
                new_ten = new_roll // 10
                if new_ten > ten_digit:
                    ten_digit = new_ten
            
        final_roll = ten_digit * 10 + unit_digit
            
        if final_roll <= skill_value:
            if final_roll <= skill_value // 5:
                 return 1
            if final_roll <= skill_value // 2:
                 return 2
            return 3
            
        if final_roll > 95 and skill_value < 50:
            return 5
            
        return 4

    @staticmethod
    async def skill_check(entity_name: str, skill_name: str, advantage: int = 0) -> CheckResult:
        """
        对指定实体的技能进行检定
        
        entity_name: 实体名称
        skill_name: 技能名称
        advantage: 优势/劣势（正数为奖励骰，负数为惩罚骰）
        """
        skill_value = 5  # 默认值
        
        entity = await fetch_model_data("Entity", {"name": entity_name})
        
        if not entity:
            logger.warning(f"实体 '{entity_name}' 未找到，使用默认技能值")
        else:
            stats = entity.get("stats", {}) or {}
            # fetch_model_data 返回的是字典，stats 也是字典（因为 JSONB 通常被转换）
            # 但如果 stats 是 None，我们需要处理
            skill_value = int(stats.get(skill_name, 5))
        
        success_level = DiceRoller.check_success(skill_value, advantage)
        
        return CheckResult(
            skill_name=skill_name,
            advantage=advantage,
            total=skill_value, # 这里的 total 似乎是技能值，而不是检定结果
            success_level=success_level,
            details=f"Check: {skill_name}, Value:{skill_value}, Advantage: {advantage}, Result Level: {success_level}"
        )