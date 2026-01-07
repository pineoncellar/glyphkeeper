"""
规则查询服务
提供 COC7th 规则数据的查询接口，与世界数据隔离
"""
from typing import Optional, Dict, Any, Literal, TYPE_CHECKING
if TYPE_CHECKING: # 编译运行时会跳过这行，这行只是给IDE用
    from ..memory.RAG_engine import RAGEngine

from ..memory.database import rules_db_manager
from ..core import get_logger

logger = get_logger(__name__)


class RuleService:
    """
    规则查询服务
    使用 rules workspace
    与世界数据完全隔离
    所有世界都可以访问相同的规则数据
    """
    
    def __init__(self, llm_tier: str = "standard"):
        self._engine: Optional["RAGEngine"] = None
        self.llm_tier = llm_tier
    
    async def _ensure_initialized(self):
        """确保 RAG 引擎已初始化（异步）"""
        if self._engine is not None:
            return
        
        logger.info(f"初始化规则数据 RAG 引擎 (llm_tier={self.llm_tier})...")
        
        # 延迟导入避免循环依赖
        from ..memory.RAG_engine import RAGEngine
        
        # 使用 RAGEngine 获取 rules domain 的实例
        self._engine = await RAGEngine.get_instance(
            domain="rules",
            llm_tier=self.llm_tier
        )
        
        logger.info("规则数据 RAG 引擎初始化完成")
    
    @property
    def engine(self) -> "RAGEngine":
        """获取 RAG 引擎实例（同步属性，仅用于已初始化的场景）"""
        if self._engine is None:
            raise RuntimeError("RuleService 未初始化，请先调用 await rule_service._ensure_initialized()")
        return self._engine
    
    async def query_rule(
        self, 
        question: str, 
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        top_k: int = 60,
        user_prompt: Optional[str] = None
    ) -> str:
        """
        查询 COC7th 规则
        mode: 查询模式 (hybrid/naive/local/global/mix)
        user_prompt: 自定义提示词（可选）
        """
        await self._ensure_initialized()
        logger.info(f"查询规则: {question} (mode={mode})")
        
        try:
            result = await self.engine.query(
                question=question,
                mode=mode,
                top_k=top_k,
                user_prompt=user_prompt
            )
            logger.debug(f"规则查询成功")
            return result
        except Exception as e:
            logger.error(f"规则查询失败: {e}")
            raise
    
    async def insert_rule_document(self, content: str, doc_id: Optional[str] = None):
        """插入规则文档到知识库"""
        await self._ensure_initialized()
        logger.info(f"插入规则文档 (doc_id={doc_id}, 长度={len(content)})")
        
        try:
            success = await self.engine.insert(content)
            if success:
                logger.info(f"规则文档插入成功")
            else:
                logger.error(f"规则文档插入失败")
        except Exception as e:
            logger.error(f"规则文档插入失败: {e}")
            raise
    
    async def insert_batch(self, contents: list[str]) -> int:
        """批量插入规则文档"""
        await self._ensure_initialized()
        logger.info(f"批量插入规则文档: {len(contents)} 个")
        
        success_count = await self.engine.insert_batch(contents)
        logger.info(f"批量插入完成: {success_count}/{len(contents)}")
        return success_count
    
    async def close(self):
        """关闭 RAG 引擎，释放资源"""
        if self._engine is not None:
            try:
                await self._engine.close()
                logger.info("规则 RAG 引擎已关闭")
            except Exception as e:
                logger.error(f"关闭规则 RAG 引擎失败: {e}")
        
        self._engine = None
    
    @property
    def is_initialized(self) -> bool:
        """检查引擎是否已初始化"""
        return self._engine is not None
    
    def get_db_session(self):
        """获取规则数据库会话"""
        return rules_db_manager.session_factory()
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        await self._ensure_initialized()
        
        health = {
            "initialized": self.is_initialized,
            "rag_available": self._engine is not None and self._engine.is_initialized,
            "db_available": False,
            "workspace": "rules"
        }
        
        # 检查数据库连接
        try:
            async with self.get_db_session() as session:
                await session.execute("SELECT 1")
                health["db_available"] = True
        except Exception as e:
            logger.error(f"规则数据库健康检查失败: {e}")
            health["error"] = str(e)
        
        return health


# 全局单例
_rule_service: Optional[RuleService] = None


def get_rule_service(llm_tier: str = "standard") -> RuleService:
    """获取规则服务单例"""
    global _rule_service
    if _rule_service is None:
        _rule_service = RuleService(llm_tier=llm_tier)
    return _rule_service
