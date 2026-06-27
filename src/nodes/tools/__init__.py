"""
@File     :   __init__.py
@Desc     :   工具节点 — 外部能力封装（掷骰 / 检索 / 线索查询）
@Note     :   db_lookup_node 纯 SQL 零 LLM；rag_lookup_node 按需查 LightRAG

节点:
  - dice_node:         掷骰执行（pending_dice → 掷骰 → resolution）
  - db_lookup_node:    DB 查询（PG 读模型 → <physical_reality> XML）
  - rag_lookup_node:   RAG 检索（LightRAG → <semantic_knowledge>，按需触发）
  - roll_node:         自动化检定（组合 dice + skill 完成完整流程）
  - archivist_node:    线索查询（检定成功后查 PG + LLM 降级解析目标 key）
"""

from src.nodes.tools.dice_node import dice_node, simple_dice_roll
from src.nodes.tools.db_lookup_node import db_lookup_node
from src.nodes.tools.rag_lookup_node import rag_lookup_node
from src.nodes.tools.roll_node import roll_node, quick_skill_check
from src.nodes.tools.archivist_node import archivist_node

__all__ = [
    "dice_node",
    "simple_dice_roll",
    "db_lookup_node",
    "rag_lookup_node",
    "roll_node",
    "quick_skill_check",
    "archivist_node",
]
