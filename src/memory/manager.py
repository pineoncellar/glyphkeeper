"""
记忆管理器
负责协调对话记录、记忆固化和上下文构建的核心逻辑。
"""
import uuid
from typing import List, Optional, Dict, Any
from sqlalchemy import select, update, desc
from sqlalchemy.ext.asyncio import AsyncSession

from ..core.logger import get_logger
from .database import db_manager
from .models import DialogueRecord, MemoryTrace
from .strategies import ConsolidationStrategy, TokenCountStrategy
from .RAG_engine import RAGEngine
from ..llm.llm_factory import LLMFactory

logger = get_logger(__name__)

class MemoryManager:
    def __init__(self, investigator_id: Optional[uuid.UUID] = None):
        self.investigator_id = investigator_id
        self.rag_engine: Optional[RAGEngine] = None
        self.strategies: List[ConsolidationStrategy] = [
            TokenCountStrategy(max_tokens=2000)
        ]
        # 使用 standard 等级模型进行总结，如果配置了 fast 等级也可以使用 fast
        self.summarizer_llm = LLMFactory.get_llm("standard") 

    async def _get_rag_engine(self) -> RAGEngine:
        if not self.rag_engine:
            self.rag_engine = await RAGEngine.get_instance()
        return self.rag_engine 

    async def add_dialogue(self, role: str, content: str):
        """
        添加一条新的对话记录
        """
        async with db_manager.session_factory() as session:
            # 获取当前最大的 turn_number
            stmt = select(DialogueRecord.turn_number).order_by(desc(DialogueRecord.turn_number)).limit(1)
            result = await session.execute(stmt)
            last_turn = result.scalar_one_or_none() or 0
            
            new_record = DialogueRecord(
                investigator_id=self.investigator_id,
                turn_number=last_turn + 1,
                role=role,
                content=content,
                is_consolidated=False
            )
            session.add(new_record)
            await session.commit()
            
            # 检查是否需要固化
            # 注意：这里传入 session 可能会有问题，因为 _check_and_consolidate 内部可能需要长时间运行 LLM
            # 最好是提交当前事务后，再开启新的流程，或者在后台任务中运行
            # 为简单起见，这里在同一个流程中执行，但要注意 session 的生命周期
            await self._check_and_consolidate()

    async def _check_and_consolidate(self):
        """
        检查并执行固化逻辑
        """
        async with db_manager.session_factory() as session:
            # 获取未固化的记录
            stmt = select(DialogueRecord).where(DialogueRecord.is_consolidated == False).order_by(DialogueRecord.turn_number)
            result = await session.execute(stmt)
            buffer = result.scalars().all()
            
            should_consolidate = False
            for strategy in self.strategies:
                if strategy.should_consolidate(buffer):
                    should_consolidate = True
                    break
            
            if should_consolidate:
                await self._consolidate(session, buffer)

    async def _consolidate(self, session: AsyncSession, buffer: List[DialogueRecord]):
        """
        执行固化：总结 -> 存储 -> 标记
        """
        if not buffer:
            return

        logger.info(f"Triggering memory consolidation for {len(buffer)} records.")

        # 1. 总结
        text_to_summarize = "\n".join([f"{r.role}: {r.content}" for r in buffer])
        prompt = f"请总结以下跑团对话，提取关键线索与决策，保持简洁：\n\n{text_to_summarize}"
        
        summary = await self._get_llm_response(prompt)
        
        # 2. 存储 MemoryTrace
        start_turn = buffer[0].turn_number
        end_turn = buffer[-1].turn_number
        
        trace = MemoryTrace(
            summary=summary,
            start_turn=start_turn,
            end_turn=end_turn,
            tags=["consolidated_dialogue"]
        )
        session.add(trace)
        
        # 3. 存储 LightRAG
        engine = await self._get_rag_engine()
        try:
            # 注意：RAGEngine.insert 目前忽略 metadata，但接口保留以备未来扩展
            await engine.insert(summary, metadata={"start_turn": start_turn, "end_turn": end_turn})
        except Exception as e:
            logger.error(f"Failed to insert summary into LightRAG: {e}")
        
        # 4. 标记已固化
        record_ids = [r.id for r in buffer]
        stmt = update(DialogueRecord).where(DialogueRecord.id.in_(record_ids)).values(is_consolidated=True)
        await session.execute(stmt)
        await session.commit()
        logger.info("Memory consolidation complete.")

    async def _get_llm_response(self, prompt: str) -> str:
        """辅助函数：获取 LLM 完整响应"""
        messages = [{"role": "user", "content": prompt}]
        response_text = ""
        async for chunk in self.summarizer_llm.chat(messages):
            response_text += chunk
        return response_text

    async def get_recent_context(self, limit: int = 10) -> List[DialogueRecord]:
        """
        获取最近的对话窗口 (Runtime Window)
        即使已固化，也可能需要获取最近的 N 条以保持连贯性
        """
        async with db_manager.session_factory() as session:
            stmt = select(DialogueRecord).order_by(desc(DialogueRecord.turn_number)).limit(limit)
            result = await session.execute(stmt)
            records = result.scalars().all()
            return sorted(records, key=lambda r: r.turn_number) # 返回正序

    async def build_prompt_context(self, query: str) -> str:
        """
        构建完整的 Prompt Context
        """
        # 1. 获取 RAG 知识
        rag_context = ""
        try:
            engine = await self._get_rag_engine()
            rag_context = await engine.query(query, mode="hybrid", top_k=5)
        except Exception as e:
            logger.error(f"Failed to query LightRAG: {e}")
        
        # 2. 获取最近对话 (Runtime Window)
        # 策略：获取最近 N 轮，无论是否固化
        recent_records = await self.get_recent_context(limit=10) 
        recent_context = "\n".join([f"{r.role}: {r.content}" for r in recent_records])
        
        # 3. 组装
        context = f"""
[World Knowledge]
{rag_context}

[Recent Context]
{recent_context}
"""
        return context
