# -*- coding: utf-8 -*-
"""
@File     :   rag_lookup_node.py
@Desc     :   RAG Lookup Node — 消歧靶点驱动右脑检索，三档弹性唤醒
@Note     :   卡在 disambiguation 之后、dispatch 之前消费 resolved_targets。
              内部完成：SQL 线索探针硬唤醒 → intent needs_rag 主动唤醒 → 静默跳过。
              检索使用 naive 模式避免 KG 构建开销，top_k 按档位动态调整。
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState, get_current_player
from src.tools import get_logger, get_settings
from src.memory.vector_store import VectorStore

logger = get_logger(__name__)


async def _has_undiscovered_clues_at_location(state: GameState) -> bool:
    """选项 A 轻量 SQL 探针：检查当前场景是否存在可触发的线索埋点

    三表 JOIN：clue_discoveries → interactables → locations，
    以 current_location 为靶心。不依赖 session_id，不查已发现状态。
    返回布尔值供上层判定是否强制硬唤醒 RAG。
    """
    current_loc = get_current_player(state).get("current_location", "")
    world_id = state.get("world_id", "")
    if not current_loc or not world_id:
        return False
    try:
        from src.tools.pg_manager import PgManager
        mgr = await PgManager.get_instance()
        if not mgr.available:
            return False
        await mgr.start()
        conn = await mgr.get_conn()
        try:
            row = await conn.fetchrow(
                """SELECT 1 FROM clue_discoveries cd
                   JOIN interactables i ON cd.interactable_id = i.id
                   JOIN locations l ON i.location_id = l.id
                   WHERE l.key = $1 AND l.world_id = $2 AND cd.world_id = $2
                   LIMIT 1""",
                current_loc, world_id,
            )
            return row is not None
        finally:
            await mgr.release_conn(conn)
    except Exception as e:
        logger.debug(f"rag_lookup_node: 线索探针查询失败: {e}")
        return False


def _build_rag_query(state: GameState, intent_data: dict) -> str:
    """组装 RAG 检索查询：玩家输入 + 消歧系统 ID 作为靶点"""
    parts = []
    player_input = state.get("player_input", "")
    if player_input:
        parts.append(player_input)
    query = intent_data.get("query") or intent_data.get("detail", "")
    if query and query not in parts:
        parts.append(query)
    resolved = state.get("resolved_targets") or {}
    for target_type, target_id in resolved.items():
        if isinstance(target_id, str) and target_id not in parts:
            parts.append(target_id)
    return " ".join(parts) if parts else player_input


async def rag_lookup_node(state: GameState) -> dict:
    """三档弹性 RAG 检索：线索埋点硬唤醒 → 主动回忆 → 静默跳过

    从 intent_queue[current_intent_idx] 读取当前迭代意图，
    废弃旧 state.intent 路径。跳过时 return {} 透传前序结果。
    """
    # ── 读取当前迭代意图（串行指针校准） ──
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    current_intent = queue[idx] if idx < len(queue) else {}
    intent_data = current_intent.get("data") or {}
    intent_type = current_intent.get("type", "")

    # ── A 档：SQL 线索探针硬唤醒 ──
    has_clues = await _has_undiscovered_clues_at_location(state)
    if has_clues:
        query = _build_rag_query(state, intent_data)
        if not query.strip():
            return {}
        try:
            world_id = state.get("world_id", "")
            cfg = get_settings().rag_retrieval
            vs = await VectorStore.get_instance(
                knowledge_space="world", world_id=world_id,
            )
            ctx_text = await vs.query(question=query, mode=cfg.clue_probe_mode, top_k=cfg.clue_probe_top_k)
            if ctx_text.strip():
                result = f"<semantic_knowledge>\n{ctx_text}\n</semantic_knowledge>"
                logger.info(f"rag_lookup_node[A]: query={query[:30]}... len={len(ctx_text)}")
                return {"rag_context": result}
        except Exception as e:
            logger.error(f"rag_lookup_node[A]: 检索失败: {e}")
        return {}

    # ── B 档：意图 needs_rag 主动唤醒 ──
    needs_rag = intent_data.get("needs_rag", False)
    if isinstance(needs_rag, str):
        needs_rag = needs_rag.lower() in ("true", "yes", "1")
    if needs_rag or intent_type in {"RECALL", "INVESTIGATE_DEEP", "RESEARCH", "CONTEMPLATE"}:
        query = _build_rag_query(state, intent_data)
        if not query.strip():
            return {}
        try:
            world_id = state.get("world_id", "")
            cfg = get_settings().rag_retrieval
            vs = await VectorStore.get_instance(
                knowledge_space="world", world_id=world_id,
            )
            ctx_text = await vs.query(question=query, mode=cfg.recall_mode, top_k=cfg.recall_top_k)
            if ctx_text.strip():
                result = f"<semantic_knowledge>\n{ctx_text}\n</semantic_knowledge>"
                logger.info(f"rag_lookup_node[B]: query={query[:30]}... len={len(ctx_text)}")
                return {"rag_context": result}
        except Exception as e:
            logger.error(f"rag_lookup_node[B]: 检索失败: {e}")
        return {}

    # ── C 档：静默跳过，空 patch 透传前序 rag_context ──
    logger.debug("rag_lookup_node[C]: 跳过（无触发条件）")
    return {}
