"""
掷骰引擎

职责:
  - 提供 D100 / D% 基础掷骰实现
  - 支持奖励骰（Bonus Dice）与惩罚骰（Penalty Dice）
  - 支持任意面数骰子（D4, D6, D8, D10, D12, D20 等）
  - 纯函数，无副作用，可独立测试

函数:
  - roll_d100() -> (tens, ones, total)
  - roll_bonus_dice(bonus_count) -> (best_of, rolls)
  - roll_penalty_dice(penalty_count) -> (worst_of, rolls)
  - roll_ndn(n, sides) -> List[int]
  - roll_dice(expression) -> int
"""

import random
import re
from typing import Tuple, List


def roll_d100() -> Tuple[int, int, int]:
    """
    掷 D100。
    返回: (十位数, 个位数, 总值)
    
    规则:
      - 十位数范围 0-9（00 = 0）
      - 个位数范围 0-9
      - 00 + 0 = 100（大失败值）
      - 00 + 1~9 = 1~9
    """
    tens_die = random.randint(0, 9)  # 十位骰
    ones_die = random.randint(0, 9)  # 个位骰
    
    tens = tens_die
    ones = ones_die
    
    # 计算总值
    if tens_die == 0 and ones_die == 0:
        total = 100
    else:
        total = tens_die * 10 + ones_die
    
    return (tens, ones, total)


def roll_bonus_dice(bonus_count: int = 1) -> Tuple[int, List[int]]:
    """
    奖励骰：掷 bonus_count+1 组 D100，取最优（最小值）。
    返回: (最优值, 所有掷骰值列表)
    
    在 CoC 7版中，奖励骰越多，取的值越小（越容易成功）。
    """
    if bonus_count < 0:
        bonus_count = 0
    
    rolls = []
    for _ in range(bonus_count + 1):
        _, _, total = roll_d100()
        rolls.append(total)
    
    best = min(rolls)
    return (best, rolls)


def roll_penalty_dice(penalty_count: int = 1) -> Tuple[int, List[int]]:
    """
    惩罚骰：掷 penalty_count+1 组 D100，取最差（最大值）。
    返回: (最差值, 所有掷骰值列表)
    
    在 CoC 7版中，惩罚骰越多，取的值越大（越难成功）。
    """
    if penalty_count < 0:
        penalty_count = 0
    
    rolls = []
    for _ in range(penalty_count + 1):
        _, _, total = roll_d100()
        rolls.append(total)
    
    worst = max(rolls)
    return (worst, rolls)


def roll_ndn(n: int, sides: int) -> List[int]:
    """
    掷 n 个 sides 面骰，返回每个骰子的值列表。
    
    参数:
      n: 骰子数量
      sides: 每个骰子的面数
    
    返回: 每个骰子的结果列表
    """
    if n < 0:
        n = 0
    if sides < 1:
        sides = 1
    
    return [random.randint(1, sides) for _ in range(n)]


def roll_dice(expression: str) -> int:
    """
    解析并掷骰表达式。
    
    支持格式：
    - "1D6" → 掷 1 个 6 面骰
    - "2D6+2" → 掷 2 个 6 面骰，结果 +2
    - "1D3" → 掷 1 个 3 面骰
    - "1D100" → 同 roll_d100()
    - "1D6-1" → 掷 1 个 6 面骰，结果 -1
    - "1D3+1D4" → 掷 1D3 + 1D4
    - "5" → 纯数字，直接返回
    
    返回掷骰结果总和。
    """
    expression = expression.strip().upper()
    
    # 纯数字
    try:
        return int(expression)
    except ValueError:
        pass
    
    # 按 +/- 分割并逐段解析
    total = 0
    current_sign = 1
    
    # 用正则拆分：保留分隔符
    parts = re.split(r'([+-])', expression)
    
    for part in parts:
        if part == '+':
            current_sign = 1
        elif part == '-':
            current_sign = -1
        else:
            part = part.strip()
            if not part:
                continue
            # 尝试匹配骰子表达式 NdN
            match = re.match(r'^(\d+)[D](\d+)$', part)
            if match:
                n = int(match.group(1))
                sides = int(match.group(2))
                roll_sum = sum(roll_ndn(n, sides))
                total += current_sign * roll_sum
            else:
                # 尝试纯数字
                try:
                    total += current_sign * int(part)
                except ValueError:
                    raise ValueError(f"无效的骰子表达式: {expression}")
    
    return total
