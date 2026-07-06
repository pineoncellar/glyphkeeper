"""
@File     :   retriever.py
@Desc     :   记忆检索器 — Graph Node 的统一记忆输入源

职责:
  - 为 Graph Node 提供统一的记忆检索接口
  - 组合向量检索 + 事件查询 + 对话历史
  - 构建 Node 执行所需的上下文（state_view / memory）
  - 支持多源结果融合

接口:
    class Retriever:
        async def retrieve_context(self, session_id, query, top_k=30) -> str
        async def retrieve_rules(self, query, top_k=10) -> str
        async def retrieve_history(self, session_id, limit=20) -> list[dict]

使用方式:
    context = await retriever.retrieve_context(
        session_id=session_id,
        query=player_intent,
        top_k=30,
    )
"""

from typing import Optional

from src.tools import get_logger
from src.memory.event_store import EventStore
from src.memory.vector_store import VectorStore

logger = get_logger(__name__)


class Retriever:
    """
    记忆检索器 — 组合多种记忆源为 Graph Node 提供上下文。

    组合策略：
        1. 向量检索 → 语义相关的世界知识 / 规则
        2. 事件查询 → 当前会话的事件历史
        3. 融合排序 → 拼接为 LLM 友好的上下文文本
    """

    def __init__(
        self,
        vector_store: Optional[VectorStore] = None,
        event_store: Optional[EventStore] = None,
    ):
        """
        参数：
            vector_store: VectorStore 实例（可为 None，此时降级为纯文本检索）
            event_store: EventStore 实例（可为 None，此时跳过事件历史）
        """
        self._vector_store = vector_store
        self._event_store = event_store

    # ── 属性（懒加载） ──

    @property
    async def vector_store(self) -> VectorStore:
        """获取 VectorStore 实例（懒加载，默认本地存储）"""
        if self._vector_store is None:
            self._vector_store = await VectorStore.get_instance(knowledge_space="world")
        return self._vector_store

    @property
    async def event_store(self) -> EventStore:
        """获取 EventStore 实例（懒加载）"""
        if self._event_store is None:
            from src.memory.event_store import create_event_store
            self._event_store = await create_event_store()
        return self._event_store

    # ── 核心检索方法 ──

    async def retrieve_context(
        self,
        session_id: str,
        query: str,
        top_k: int = 30,
    ) -> str:
        """
        为 Node 构建执行上下文。

        返回拼接后的上下文文本，包含：
        1. 向量检索结果（世界知识）
        2. 当前会话的事件摘要
        3. 综合上下文

        参数：
            session_id: 会话 ID
            query: 检索查询（通常为玩家意图描述）
            top_k: 向量检索返回的最大文档数
        """
        if not query.strip():
            return "（无查询内容）"

        parts: list[str] = []

        # 向量检索（世界知识）
        try:
            vs = await self.vector_store
            semantic_result = await vs.query(question=query, top_k=top_k)
            if semantic_result.strip():
                parts.append(f"【相关知识】\n{semantic_result}")
        except Exception as e:
            logger.warning(f"向量检索失败: {e}")
            parts.append("（相关知识检索暂不可用）")

        # 事件历史
        try:
            es = await self.event_store
            events = await es.get_events(session_id)
            if events:
                recent = events[-10:]  # 最近 10 条
                event_summary = "\n".join(
                    f"  [{e['type']}] {e['data']}" for e in recent
                )
                parts.append(f"【事件历史（最近 {len(recent)} 条）】\n{event_summary}")
        except Exception as e:
            logger.warning(f"事件历史检索失败: {e}")

        # 拼接
        return "\n\n".join(parts)

    async def retrieve_rules(self, query: str, top_k: int = 10) -> str:
        """
        检索规则知识库中的相关规则。

        使用 domain="rules" 的 VectorStore 实例。
        """
        if not query.strip():
            return "（无查询内容）"

        try:
            vs = await VectorStore.get_instance(knowledge_space="rules")
            result = await vs.query(question=query, top_k=top_k)
            return result if result.strip() else "（未找到相关规则）"
        except Exception as e:
            logger.warning(f"规则检索失败: {e}")
            return "（规则检索暂不可用）"

    async def retrieve_history(
        self,
        session_id: str,
        limit: int = 20,
    ) -> list[dict]:
        """
        获取最近的对话/事件历史记录。

        返回按时间正序排列的事件列表（最新的 limit 条）。
        """
        try:
            es = await self.event_store
            events = await es.get_events(session_id)
            return events[-limit:] if len(events) > limit else events
        except Exception as e:
            logger.warning(f"历史记录检索失败: {e}")
            return []

    async def build_memory_input(
        self,
        session_id: str,
        query: str,
        top_k: int = 30,
    ) -> dict:
        """
        构建 NodeInput 所需的 memory 字段。

        返回：
            {
                "context": str,          # 拼接后的上下文文本
                "recent_events": list,   # 最近事件列表
                "rules": str,            # 相关规则
            }
        """
        context = await self.retrieve_context(session_id, query, top_k)
        recent = await self.retrieve_history(session_id, limit=10)
        rules = await self.retrieve_rules(query)

        return {
            "context": context,
            "recent_events": recent,
            "rules": rules,
        }
