"""
Dice Roller - 掷骰子工具

实现 TRPG 常用的掷骰子逻辑：

支持格式：
- 标准格式: "3d6+5" (3个6面骰 + 5)
- 优势/劣势: "2d20kh1" (掷2个d20取高)
- 多次掷骰: "4d6kh3" (掷4个d6，取高3个)
- 成功计数: "5d10>=6" (掷5个d10，统计>=6的数量)

核心函数：
- roll_dice(expression: str) -> DiceResult
- advantage_roll() -> int (优势掷骰，2d20取高)
- disadvantage_roll() -> int (劣势掷骰，2d20取低)
- stat_check(modifier: int, dc: int) -> bool (属性检定)

返回格式：
    DiceResult(
        expression="3d6+5",
        rolls=[4, 2, 6],
        modifier=5,
        total=17,
        details="(4+2+6)+5=17"
    )

用法示例：
    result = roll_dice("2d20+3")
    success = stat_check(modifier=5, dc=15)
"""
