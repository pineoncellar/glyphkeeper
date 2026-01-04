"""
规则查询服务
提供 COC7th 规则数据的查询接口，与世界数据隔离
"""
from typing import Optional, Dict, Any, Literal
from lightrag import LightRAG, QueryParam
from ..memory.storage import get_rules_storage_config
from ..memory.database import rules_db_manager
from ..core import get_logger, get_settings
from ..llm import create_llm_model_func, create_embedding_func

logger = get_logger(__name__)


class RuleService:
    """
    规则查询服务
    - 使用独立的 coc7th_rules schema
    - 使用独立的 LightRAG 实例和图谱文件
    - 与世界数据完全隔离
    - 目前只存进light rag，没往数据库存
    """
    
    def __init__(self, llm_tier: str = "standard"):
        self._rag: Optional[LightRAG] = None
        self._initialized = False
        self.llm_tier = llm_tier
    
    async def _ensure_initialized(self):
        """确保 RAG 引擎已初始化（异步）"""
        if self._initialized:
            return
        
        logger.info(f"初始化规则数据 LightRAG 实例 (llm_tier={self.llm_tier})...")
        
        # 获取存储配置
        config = get_rules_storage_config()
        
        # 获取设置
        settings = get_settings()
        
        # 设置环境变量（与 RAG_engine 相同）
        import os
        db_config = settings.database
        os.environ["POSTGRES_USER"] = db_config.username or "postgres"
        os.environ["POSTGRES_PASSWORD"] = db_config.password or ""
        os.environ["POSTGRES_DATABASE"] = db_config.project_name or "postgres"
        os.environ["POSTGRES_HOST"] = db_config.host
        os.environ["POSTGRES_PORT"] = db_config.port or "5432"
        
        # 创建 LLM 和 Embedding 函数（与 RAG_engine 相同）
        llm_func = create_llm_model_func(tier=self.llm_tier)
        
        vector_config = settings.vector_store
        logger.debug(f"使用的向量存储配置: provider={vector_config.provider}, model={vector_config.embedding_model_name}, dim={vector_config.embedding_dim}")
        embedding_func = create_embedding_func(
            model_name=vector_config.embedding_model_name,
            embedding_dim=vector_config.embedding_dim,
            max_token_size=8192,
            provider=vector_config.provider
        )
        
        # 初始化 LightRAG
        self._rag = LightRAG(
            llm_model_func=llm_func,
            embedding_func=embedding_func,
            **config
        )
        
        # 关键：初始化存储连接（异步）
        await self._rag.initialize_storages()
        
        self._initialized = True
        logger.info(f"规则数据 LightRAG 初始化完成: {config['working_dir']}")
    
    @property
    def rag(self) -> LightRAG:
        """获取 RAG 实例（同步属性，仅用于已初始化的场景）"""
        if self._rag is None:
            raise RuntimeError("RuleService 未初始化，请先调用 await rule_service._ensure_initialized()")
        return self._rag
    
    async def query_rule(
        self, 
        question: str, 
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        top_k: int = 60,
        user_prompt: Optional[str] = None
    ) -> str:
        """
        查询 COC7th 规则
        
        Args:
            question: 查询问题
            mode: 查询模式 (hybrid/naive/local/global/mix)
            top_k: 返回 top_k 个结果
            user_prompt: 自定义提示词（可选）
        """
        await self._ensure_initialized()
        logger.info(f"查询规则: {question} (mode={mode})")
        
        try:
            # 构建查询参数
            param = QueryParam(
                mode=mode,
                top_k=top_k
            )
            
            # 如果有自定义提示词，设置到参数中
            if user_prompt:
                param.user_prompt = user_prompt
            
            result = await self.rag.aquery(question, param=param)
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
            await self.rag.ainsert(content)
            logger.info(f"✓ 规则文档插入成功")
        except Exception as e:
            logger.error(f"✗ 规则文档插入失败: {e}")
            raise
    
    async def insert_batch(self, contents: list[str]) -> int:
        """批量插入规则文档"""
        await self._ensure_initialized()
        logger.info(f"批量插入规则文档: {len(contents)} 个")
        
        success_count = 0
        for i, content in enumerate(contents, 1):
            try:
                await self.rag.ainsert(content)
                success_count += 1
                logger.debug(f"✓ 批量插入进度: {i}/{len(contents)}")
            except Exception as e:
                logger.error(f"✗ 批量插入第 {i} 个文档失败: {e}")
        
        logger.info(f"批量插入完成: {success_count}/{len(contents)}")
        return success_count
    
    async def close(self):
        """关闭 RAG 引擎，释放资源"""
        if self._rag is not None:
            try:
                await self._rag.finalize_storages()
                logger.info("规则 RAG 引擎已关闭")
            except Exception as e:
                logger.error(f"关闭规则 RAG 引擎失败: {e}")
        
        self._initialized = False
        self._rag = None
    
    @property
    def is_initialized(self) -> bool:
        """检查引擎是否已初始化"""
        return self._initialized
    
    def get_db_session(self):
        """获取规则数据库会话"""
        return rules_db_manager.session_factory()
    
    async def health_check(self) -> Dict[str, Any]:
        """健康检查"""
        await self._ensure_initialized()
        
        health = {
            "initialized": self._initialized,
            "rag_available": self._rag is not None,
            "db_available": False,
            "schema": "coc7th_rules"
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
