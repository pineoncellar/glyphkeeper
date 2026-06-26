# -*- coding: utf-8 -*-
"""
@File     :   rag_lookup_node.py
@Desc     :   RAG Lookup Node — 按需从 LightRAG 检索语义知识
@Note     :   仅当 intent 类型为知识挖掘/深度回忆时触发，其余情况返回空占位

Node 签名:
    async def rag_lookup_node(state: GameState) -> dict:
        检查 intent.needs_rag → 若需要则查 LightRAG
        返回: {"rag_context": str} 或空
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState
from src.tools import get_logger
from src.memory.retriever import Retriever

logger = get_logger(__name__)


# ── 需触发 RAG 检索的意图类型 ──

_RAG_INTENTS = {
    "RECALL",       # 回忆/回想
    "INVESTIGATE_DEEP",  # 深度调查
    "RESEARCH",     # 研究/阅读
    "CONTEMPLATE",  # 沉思/联想
}

# ── 全局 Retriever 实例 ──

_retriever: Optional[Retriever] = None


async def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


async def rag_lookup_node(state: GameState) -> dict:
    """按需从 LightRAG 检索语义知识

    Router 在 intent.data.needs_rag 中标记是否需要 RAG。
    若不需要，直接返回空占位 <semantic_knowledge /> 避免不必要的 API 调用。
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    intent_type = intent.get("type", "")

    # 判断是否需要 RAG
    needs_rag = intent_data.get("needs_rag", False)
    if isinstance(needs_rag, str):
        needs_rag = needs_rag.lower() in ("true", "yes", "1")
    if not needs_rag and intent_type not in _RAG_INTENTS:
        logger.debug("rag_lookup_node: 跳过（无需 RAG）")
        return {"rag_context": ""}

    query = (
        intent_data.get("query")
        or intent_data.get("detail")
        or state.get("player_input", "")
    )
    if not query or not query.strip():
        return {"rag_context": ""}

    session_id = state.get("session_id", "")
    try:
        retriever = await _get_retriever()
        ctx_text = await retriever.retrieve_context(session_id, query, top_k=30)
        if ctx_text.strip():
            result = f"<semantic_knowledge>\n{ctx_text}\n</semantic_knowledge>"
        else:
            result = "<semantic_knowledge />"
        logger.info(f"rag_lookup_node: query={query[:30]}... len={len(ctx_text)}")
        return {"rag_context": result}
    except Exception as e:
        logger.error(f"rag_lookup_node: 检索失败: {e}")
        return {"rag_context": ""}
