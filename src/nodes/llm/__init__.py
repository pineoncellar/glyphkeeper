"""
@File     :   __init__.py
@Desc     :   LLM 驱动节点 — 自然语言理解与生成
@Note     :   每个节点有 LLM + 规则双模式，无 LLM 时降级可工作

节点:
  - intent_node:      玩家输入 → 结构化 Intent（LLM + 规则兜底）
  - rule_only_intent_node:  纯规则意图识别（测试/禁用 LLM 时使用）
  - narrate_node:     裁决结果 → 沉浸式叙事文本（LLM + 模板兜底）
  - adjudicate_node:  即兴行为 → 规则参数（LLM + 规则兜底）
"""

from src.nodes.llm.intent_node import intent_node, rule_only_intent_node
from src.nodes.llm.narrator_node import narrate_node
from src.nodes.llm.adjudicator_node import adjudicate_node

__all__ = [
    "intent_node",
    "rule_only_intent_node",
    "narrate_node",
    "adjudicate_node",
]
