"""
@File     :   lookup_node.py
@Desc     :   知识检索节点 — 从 RAG 知识库和事件存储检索信息
@Note     :   组合 VectorStore + EventStore 为 Node 提供上下文

Node 签名:
    async def lookup_node(state: GameState) -> dict:
        返回: {"world_context": context_text_str}
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState
from src.tools import get_logger
from src.memory.retriever import Retriever

logger = get_logger(__name__)


# ── 全局 Retriever 实例（懒加载） ──
_retriever: Optional[Retriever] = None


async def _get_retriever() -> Retriever:
    """获取或创建全局 Retriever 实例"""
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


async def lookup_node(state: GameState) -> dict:
    """
    知识检索节点。

    从 intent 或 state 中提取查询参数，检索相关知识并返回。

    查询参数来源（按优先级）：
        1. state["intent"]["data"]["query"] — 玩家意图中提取的查询词
        2. state["player_input"] — 直接使用玩家输入
        3. 空查询 → 返回空结果
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    query = (
        intent_data.get("query")
        or intent_data.get("target")
        or state.get("player_input", "")
    )

    if not query or not query.strip():
        logger.debug("lookup_node: 无查询内容")
        return {"world_context": ""}

    session_id = state.get("session_id", "")

    try:
        retriever = await _get_retriever()

        # 并行检索三种上下文
        import asyncio
        ctx_text, rules_text, history = await asyncio.gather(
            retriever.retrieve_context(session_id, query, top_k=30),
            retriever.retrieve_rules(query, top_k=10),
            retriever.retrieve_history(session_id, limit=20),
        )

        result = {
            "success": True,
            "query": query,
            "context": ctx_text,
            "rules": rules_text,
            "history": history,
            "context_length": len(ctx_text),
            "rules_length": len(rules_text),
            "history_count": len(history) if isinstance(history, list) else 0,
        }

        logger.info(f"lookup_node: query={query[:30]}... ctx={len(ctx_text)} rules={len(rules_text)}")
        return {"world_context": ctx_text}

    except Exception as e:
        logger.error(f"lookup_node: 检索失败: {e}")
        return {"world_context": ""}


async def simple_lookup(query: str, session_id: str = "") -> dict:
    """
    简化检索辅助函数 — 不依赖 GameState，直接返回结果。
    """
    try:
        retriever = await _get_retriever()
        ctx_text = await retriever.retrieve_context(session_id, query, top_k=20)
        return {
            "success": True,
            "query": query,
            "context": ctx_text,
        }
    except Exception as e:
        return {"success": False, "query": query, "error": str(e)}
