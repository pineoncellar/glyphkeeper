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
"""
