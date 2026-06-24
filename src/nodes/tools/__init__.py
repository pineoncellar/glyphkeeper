"""
@File     :   __init__.py
@Desc     :   工具节点 — 外部能力封装（掷骰 / 检索 / 自动化检定）
@Note     :   100% 确定性（lookup_node 除外，它调用外部存储）

节点:
  - dice_node:    掷骰执行（pending_dice → 掷骰 → resolution）
  - simple_dice_roll: 快捷掷骰辅助函数（不依赖 GameState）
  - lookup_node:  知识检索（intent → Retriever → context/rules）
  - simple_lookup: 快捷检索辅助函数（不依赖 GameState）
  - roll_node:    自动化检定（组合 dice + skill 完成完整流程）
  - quick_skill_check: 快捷技能检定（不依赖 GameState）
"""

from src.nodes.tools.dice_node import dice_node, simple_dice_roll
from src.nodes.tools.lookup_node import lookup_node, simple_lookup
from src.nodes.tools.roll_node import roll_node, quick_skill_check

__all__ = [
    "dice_node",
    "simple_dice_roll",
    "lookup_node",
    "simple_lookup",
    "roll_node",
    "quick_skill_check",
]
