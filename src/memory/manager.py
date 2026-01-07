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
        self.knowledge_service = None  # 延迟初始化，避免循环导入
        self.rag_engine: Optional[RAGEngine] = None  # 保留用于写入操作
        self.strategies: List[ConsolidationStrategy] = [
            TokenCountStrategy(max_tokens=2000)
        ]
        # 默认使用 standard 等级模型进行总结
        self.summarizer_llm = LLMFactory.get_llm("standard")
    
    def _get_knowledge_service(self):
        """延迟导入并获取 KnowledgeService（避免循环导入）"""
        if self.knowledge_service is None:
            from ..agents.tools.knowledge_service import KnowledgeService
            self.knowledge_service = KnowledgeService(domain="world")
        return self.knowledge_service

    async def _get_rag_engine(self) -> RAGEngine:
        """获取 RAG 引擎用于写入操作"""
        if not self.rag_engine:
            self.rag_engine = await RAGEngine.get_instance()
        return self.rag_engine 

    async def add_dialogue(self, role: str, content: str):
        """添加一条新的对话记录"""
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
            # TODO: 主流程编写后，再丢到主流程去定时调用
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

        logger.info(f"正在固化 {len(buffer)} 条记录。")

        # 总结 - 使用更严格的 prompt 控制输出格式
        text_to_summarize = "\n".join([f"{r.role}: {r.content}" for r in buffer])
        prompt = f"""请用一段简洁的第三人称叙述总结以下跑团对话（不超过100字）。

要求：
1. 只描述发生了什么事实和玩家的行动
2. 不要使用 XML 标签、markdown 格式或列表
3. 不要分析或评论，只陈述事实
4. 使用第三人称（"调查员..."、"艾德薇诗..."）

对话内容：
{text_to_summarize}

总结："""
        
        raw_summary = await self._get_llm_response(prompt)
        
        # 清理 summary：移除可能的格式化内容
        clean_summary = self._clean_summary(raw_summary)
        
        # 存储 MemoryTrace（保存原始总结以便审计）
        start_turn = buffer[0].turn_number
        end_turn = buffer[-1].turn_number
        
        trace = MemoryTrace(
            summary=raw_summary,  # 保存原始内容
            start_turn=start_turn,
            end_turn=end_turn,
            tags=["consolidated_dialogue"]
        )
        session.add(trace)
        
        # 存储 LightRAG（只存储清理后的简洁内容）
        if len(clean_summary) > 20:
            engine = await self._get_rag_engine()
            try:
                logger.debug(f"准备插入 LightRAG: {len(clean_summary)} 字符")
                await engine.insert(clean_summary, metadata={"start_turn": start_turn, "end_turn": end_turn})
                logger.debug("LightRAG 插入完成")
            except Exception as e:
                logger.error(f"插入 LightRAG 失败: {e}")
        else:
            logger.warning(f"清理后的 summary 过短({len(clean_summary)}字)，跳过 LightRAG 插入")
        
        # 标记已固化
        record_ids = [r.id for r in buffer]
        stmt = update(DialogueRecord).where(DialogueRecord.id.in_(record_ids)).values(is_consolidated=True)
        await session.execute(stmt)
        await session.commit()
        logger.info("固化完成。")
    
    def _clean_summary(self, text: str) -> str:
        """清理总结文本，移除格式化标记和冗余内容"""
        clean = text.strip()
        
        # 移除 thinking 标签及其内容
        if "<thinking>" in clean:
            thinking_end = clean.find("</thinking>")
            if thinking_end != -1:
                clean = clean[thinking_end + len("</thinking>"):].strip()
        
        # 提取 narrative 标签内容
        if "<narrative>" in clean and "</narrative>" in clean:
            start = clean.find("<narrative>") + len("<narrative>")
            end = clean.find("</narrative>")
            clean = clean[start:end].strip()
        
        # 移除其他 XML 标签
        import re
        clean = re.sub(r'<[^>]+>', '', clean)
        
        # 移除 markdown 格式化（**粗体**、## 标题等）
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)  # **text** -> text
        clean = re.sub(r'^#+\s+', '', clean, flags=re.MULTILINE)  # ## Title -> Title
        
        # 移除列表标记和格式化部分
        lines = clean.split('\n')
        narrative_lines = []
        for line in lines:
            line = line.strip()
            # 跳过空行、列表项、明显的格式化标题
            if not line:
                continue
            if line.startswith('-') or line.startswith('*') or line.startswith('•'):
                continue
            if line.startswith('**') or line.endswith('**'):
                continue
            if any(marker in line for marker in ['关键线索', '玩家决策', '当前状态', '环境细节']):
                continue
            narrative_lines.append(line)
        
        # 只保留前5行叙事内容
        clean = ' '.join(narrative_lines[:5])
        
        # 确保长度合理（100-500字之间）
        if len(clean) > 500:
            clean = clean[:497] + "..."
        
        return clean.strip()

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
    
    async def build_prompt_context(self, query: str) -> Dict[str, str]:
        """为 Narrator 构建三段式上下文"""
        knowledge_service = self._get_knowledge_service()
        
        # 单次检索，要求结构化输出
        try:
            full_response = await knowledge_service.search(
                query=query,
                mode="hybrid",
                smart_mode=True,
                persona="kp_context",
                top_k=50
            )
        except Exception as e:
            logger.error(f"Retrieval failed: {e}")
            full_response = ""

        # 解析输出
        context = {
            "semantic": "",
            "episodic": "",
            "keeper_notes": ""
        }
        
        # 简单解析器
        current_section = None
        lines = full_response.split('\n')
        sections = {"lore": [], "memory": [], "secret": []}
        
        for line in lines:
            stripped = line.strip()
            # 兼容有些模型可能输出 ## [Lore] 或者 **[Lore]**
            if "[Lore]" in stripped and ("#" in stripped or "**" in stripped):
                current_section = "lore"
                continue
            elif "[Memory]" in stripped and ("#" in stripped or "**" in stripped):
                current_section = "memory"
                continue
            elif "[Secret]" in stripped and ("#" in stripped or "**" in stripped):
                current_section = "secret"
                continue
            
            if current_section and stripped:
                sections[current_section].append(line)
        
        context["semantic"] = "\n".join(sections["lore"]).strip() or "暂无相关设定。"
        context["episodic"] = "\n".join(sections["memory"]).strip() or "暂无相关记忆。"
        # Keeper notes can be empty
        context["keeper_notes"] = "\n".join(sections["secret"]).strip() or ""
        
        return context
