"""
@File     :   __init__.py
@Desc     :   确定性规则节点 — 100% 纯逻辑，无 LLM 依赖
@Note     :   所有函数调用 domain/ 层的纯函数

节点:
  - combat_node:      战斗行动裁决（攻击、闪避、伤害计算）
  - init_combat_node: 战斗初始化（设置 combatants, combat_round）
  - sanity_node:      理智检定与疯狂判定
  - skill_node:       技能检定（常规/困难/极难）
  - batch_skill_check: 批量技能检定
"""

from src.nodes.rules.combat_node import combat_node, init_combat_node
from src.nodes.rules.sanity_node import sanity_node
from src.nodes.rules.skill_node import skill_node, batch_skill_check

__all__ = [
    "combat_node",
    "init_combat_node",
    "sanity_node",
    "skill_node",
    "batch_skill_check",
]
