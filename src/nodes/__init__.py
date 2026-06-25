"""
@File     :   __init__.py
@Desc     :   GlyphKeeper Node 层 — 所有可执行 Graph 节点的统一导出
@Note     :   每个 node 是独立可测试的 async State → State 函数

子模块:
  - nodes/llm/     : LLM 驱动节点（intent / narrate / adjudicate / npc_dialogue）
  - nodes/rules/   : 确定性规则节点（combat / sanity / skill）
  - nodes/tools/   : 工具节点（dice / lookup / roll）

原则:
  - 每个 node 是 async function: async def node(state: GameState) -> dict
  - Node 只能返回 state_patch，不能直接修改 state
  - Tool / Rule 节点 100% 确定性，无 LLM 调用
  - LLM 节点有规则兜底，无 LLM 时仍可工作
"""

from src.nodes.llm.intent_node import intent_node, rule_only_intent_node
from src.nodes.llm.narrator_node import narrate_node
from src.nodes.llm.adjudicator_node import adjudicate_node
from src.nodes.llm.npc_dialogue_node import npc_dialogue_node
from src.nodes.rules.combat_node import combat_node, init_combat_node
from src.nodes.rules.sanity_node import sanity_node
from src.nodes.rules.skill_node import skill_node, batch_skill_check
from src.nodes.tools.dice_node import dice_node, simple_dice_roll
from src.nodes.tools.lookup_node import lookup_node, simple_lookup
from src.nodes.tools.roll_node import roll_node, quick_skill_check

__all__ = [
    # LLM Nodes
    "intent_node",
    "rule_only_intent_node",
    "narrate_node",
    "adjudicate_node",
    "npc_dialogue_node",
    # Rule Nodes
    "combat_node",
    "init_combat_node",
    "sanity_node",
    "skill_node",
    "batch_skill_check",
    # Tool Nodes
    "dice_node",
    "simple_dice_roll",
    "lookup_node",
    "simple_lookup",
    "roll_node",
    "quick_skill_check",
]
