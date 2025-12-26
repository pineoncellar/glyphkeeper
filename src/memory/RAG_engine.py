"""
LightRAG 核心引擎
实现单例模式，确保整个应用只初始化一个 RAG 对象
避免重复加载模型和连接数据库
"""
import asyncio
from pathlib import Path
from typing import Optional, Literal
from lightrag import LightRAG, QueryParam

from ..core.config import get_settings, PROJECT_ROOT
from ..core.logger import get_logger
from ..llm import create_llm_model_func, create_embedding_func
from .storage import get_storage_config

logger = get_logger(__name__)


class RAGEngine:
    """RAG 引擎单例类"""
    _instance: Optional["RAGEngine"] = None
    _lock = asyncio.Lock()
    
    def __init__(self):
        self.rag: Optional[LightRAG] = None
        self._initialized = False
    
    @classmethod
    async def get_instance(
        cls,
        llm_tier: str = "standard",
        force_reinit: bool = False # 是否强制初始化
    ) -> "RAGEngine":
        """获取 RAG 引擎单例实例"""
        async with cls._lock:
            if cls._instance is None or force_reinit:
                cls._instance = cls()
                await cls._instance._initialize(llm_tier)
            return cls._instance
    
    async def _initialize(
        self,
        llm_tier: str
    ):
        """初始化 LightRAG 实例"""
        if self._initialized:
            logger.warning("RAG 引擎已初始化，跳过重复初始化")
            return
        
        logger.info(f"正在初始化 RAG 引擎: llm_tier={llm_tier}")
        
        settings = get_settings()
        
        # 获取工作目录
        data_dir = PROJECT_ROOT / "data"
        
        # 确保基础目录结构存在
        (data_dir / "raw_sources").mkdir(parents=True, exist_ok=True)
        (data_dir / "intermediate").mkdir(parents=True, exist_ok=True)
        
        # LightRAG 工作目录设置为 modules
        working_dir = data_dir / "modules"
        working_dir.mkdir(parents=True, exist_ok=True)
        
        # 获取存储配置
        storage_config = get_storage_config(
            working_dir=str(working_dir)
        )
        
        # 设置环境变量,满足 LightRAG 对 PGKVStorage 的要求
        # LightRAG 内部会检查这些环境变量
        import os
        settings = get_settings()
        db_config = settings.database
        os.environ["POSTGRES_USER"] = db_config.username or "postgres"
        os.environ["POSTGRES_PASSWORD"] = db_config.password or ""
        os.environ["POSTGRES_DATABASE"] = db_config.project_name or "postgres"
        os.environ["POSTGRES_HOST"] = db_config.url.split(":")[0] if ":" in db_config.url else db_config.url
        os.environ["POSTGRES_PORT"] = db_config.url.split(":")[1] if ":" in db_config.url else "5432"
        
        # 创建 LLM 和 Embedding 函数
        llm_func = create_llm_model_func(tier=llm_tier)
        
        vector_config = settings.vector_store
        logger.debug(f"使用的向量存储配置: provider={vector_config.provider}, model={vector_config.embedding_model_name}, dim={vector_config.embedding_dim}")
        embedding_func = create_embedding_func(
            model_name=vector_config.embedding_model_name,
            embedding_dim=vector_config.embedding_dim,
            max_token_size=8192,
            provider=vector_config.provider
        )
        
        try:
            # 初始化 LightRAG
            self.rag = LightRAG(
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                **storage_config
            )
            
            # 初始化存储连接 (异步)
            await self.rag.initialize_storages()
            
            self._initialized = True
            logger.debug("RAG 引擎初始化完成")
            
        except Exception as e:
            logger.error(f"RAG 引擎初始化失败: {e}")
            raise
    
    async def insert(self, content: str) -> bool:
        """插入文本内容到知识库"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("RAG 引擎未初始化")
        
        try:
            await self.rag.ainsert(content)
            logger.debug(f"成功插入文本 (长度: {len(content)})")
            return True
        except Exception as e:
            logger.error(f"插入文本失败: {e}")
            return False
    
    async def insert_batch(self, contents: list[str]) -> int:
        """批量插入文本内容"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("RAG 引擎未初始化")
        
        success_count = 0
        for content in contents:
            try:
                await self.rag.ainsert(content)
                success_count += 1
            except Exception as e:
                logger.error(f"批量插入中某项失败: {e}")
        
        logger.info(f"批量插入完成: {success_count}/{len(contents)}")
        return success_count
    
    async def query(
        self,
        question: str,
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        top_k: int = 60,
        user_prompt: Optional[str] = None
    ) -> str:
        """查询知识库

        查询模式
        local: 局部搜索，侧重实体关系
        global: 全局搜索，侧重主题概念
        hybrid: 混合模式 (推荐)
        mix: 组合多种结果
        naive: 朴素搜索
        """
        if not self._initialized or self.rag is None:
            raise RuntimeError("RAG 引擎未初始化")
        
        # 构建查询参数
        param = QueryParam(
            mode=mode,
            top_k=top_k,
        )
        
        # 如果有自定义提示词，设置到参数中
        if user_prompt:
            param.user_prompt = user_prompt
        
        logger.debug(f"RAG 查询: question={question[:50]}..., mode={mode}")
        
        try:
            result = await self.rag.aquery(question, param=param)
            return result
        except Exception as e:
            logger.error(f"RAG 查询失败: {e}")
            raise
    
    async def close(self):
        """关闭RAG引擎，释放资源"""
        if self.rag is not None:
            try:
                await self.rag.finalize_storages()
                logger.info("RAG 引擎已关闭")
            except Exception as e:
                logger.error(f"关闭 RAG 引擎失败: {e}")
        
        self._initialized = False
        self.rag = None
    
    @property
    def is_initialized(self) -> bool:
        """检查引擎是否已初始化"""
        return self._initialized


##########################################################################
#便捷测试函数

async def get_rag_engine(
    llm_tier: str = "standard"
) -> RAGEngine:
    """获取RAG引擎实例的便捷函数"""
    return await RAGEngine.get_instance(llm_tier)


async def quick_query(question: str, mode: str = "hybrid") -> str:
    """快速查询的便捷函数"""
    engine = await get_rag_engine()
    return await engine.query(question, mode=mode)
