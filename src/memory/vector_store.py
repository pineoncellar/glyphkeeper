"""
@File     :   vector_store.py
@Desc     :   向量/图存储 — LightRAG 封装，支持多 domain 隔离

职责:
  - 封装 LightRAG 的单例初始化与管理
  - 提供语义检索接口（local/global/hybrid/naive）
  - 多 domain 数据隔离（world / rules）
  - 文本嵌入生成与存储

接口:
    class VectorStore:
        @classmethod
        async def get_instance(cls, domain="world", llm_tier="standard") -> "VectorStore"
        async def query(self, question, mode="hybrid", top_k=60) -> str
        async def insert(self, text, source_type="narrative") -> bool
        async def clear(self) -> bool
        async def close(self)

实现:
  - 单例模式：每个 domain 一个实例
  - domain="world" → 使用 active_world 作为 workspace
  - domain="rules" → 固定 workspace="rules"
  - LLM 和 Embedding 函数通过 lazy import 加载（避免循环依赖）
"""

import asyncio
import logging
from pathlib import Path
from typing import Optional, Literal

from lightrag import LightRAG, QueryParam

from src.config import get_settings, get_logger, PROJECT_ROOT

logger = get_logger(__name__)


class VectorStore:
    """向量/图存储 — LightRAG 单例封装"""

    _instances: dict[str, "VectorStore"] = {}
    _lock = asyncio.Lock()

    def __init__(self, domain: str):
        self.domain = domain
        self.rag: Optional[LightRAG] = None
        self._initialized = False

    # ── 单例管理 ──

    @classmethod
    async def get_instance(
        cls,
        domain: str = "world",
        llm_tier: str = "standard",
        force_reinit: bool = False,
    ) -> "VectorStore":
        """获取指定 domain 的 VectorStore 实例（单例）"""
        async with cls._lock:
            if domain not in cls._instances or force_reinit:
                cls._instances[domain] = cls(domain)
                await cls._instances[domain]._initialize(llm_tier)
            return cls._instances[domain]

    @classmethod
    async def close_all(cls):
        """关闭所有实例"""
        for inst in cls._instances.values():
            await inst.close()
        cls._instances.clear()

    # ── 初始化 ──

    async def _initialize(self, llm_tier: str = "standard"):
        """初始化 LightRAG 实例"""
        if self._initialized:
            logger.warning(f"VectorStore({self.domain}) 已初始化，跳过")
            return

        settings = get_settings()

        # 确定工作目录和 workspace
        if self.domain == "rules":
            working_dir = PROJECT_ROOT / "data" / "rules"
            workspace = "rules"
        else:
            active_world = settings.project.active_world
            working_dir = PROJECT_ROOT / "data" / "worlds"
            workspace = active_world

        working_dir.mkdir(parents=True, exist_ok=True)

        # 通过 lazy import 获取 LLM/Embedding 函数
        llm_func = self._create_llm_func(llm_tier)
        embedding_func = self._create_embedding_func()

        # 构建存储配置
        storage_config = self._build_storage_config(str(working_dir), workspace)

        try:
            self.rag = LightRAG(
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                **storage_config,
            )
            await self.rag.initialize_storages()
            self._initialized = True
            logger.info(f"VectorStore({self.domain}) 初始化完成: workspace={workspace}")
        except Exception as e:
            logger.error(f"VectorStore({self.domain}) 初始化失败: {e}")
            raise

    def _create_llm_func(self, tier: str):
        """创建 LightRAG 兼容的 LLM 调用函数（lazy import）"""
        from lightrag.llm.openai import openai_complete_if_cache

        settings = get_settings()
        model_config, provider_config = settings.get_full_model_config(tier)

        async def llm_model_func(
            prompt: str,
            system_prompt: Optional[str] = None,
            history_messages: Optional[list] = None,
            **kwargs,
        ) -> str:
            return await openai_complete_if_cache(
                model=model_config.model_name,
                prompt=prompt,
                system_prompt=system_prompt,
                history_messages=history_messages or [],
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
                **kwargs,
            )

        return llm_model_func

    def _create_embedding_func(self):
        """创建 LightRAG 兼容的 Embedding 函数（lazy import）"""
        from lightrag.llm.openai import openai_embed
        from lightrag.utils import EmbeddingFunc
        import numpy as np

        settings = get_settings()
        vector_config = settings.vector_store
        provider_config = settings.get_provider_config(vector_config.provider)

        if provider_config is None:
            raise ValueError(
                f"未找到提供方 '{vector_config.provider}' 的配置。"
                f"请检查 providers.ini 文件"
            )

        raw_openai_embed = openai_embed.func

        async def embedding_func(texts: list[str]) -> np.ndarray:
            return await raw_openai_embed(
                texts=texts,
                model=vector_config.embedding_model_name,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
            )

        return EmbeddingFunc(
            embedding_dim=vector_config.embedding_dim,
            max_token_size=8192,
            func=embedding_func,
        )

    def _build_storage_config(self, working_dir: str, workspace: str) -> dict:
        """构建 LightRAG 存储配置"""
        settings = get_settings()
        db_config = settings.database

        config: dict = {
            "working_dir": working_dir,
            "workspace": workspace,
            "graph_storage": "NetworkXStorage",
            "vector_storage": "NanoVectorDBStorage",
            "kv_storage": "JsonKVStorage",
            "doc_status_storage": "JsonDocStatusStorage",
        }

        # 如果配置了 PostgreSQL，升级存储
        if db_config.host:
            config.update({
                "vector_storage": "PGVectorStorage",
                "kv_storage": "PGKVStorage",
                "doc_status_storage": "PGDocStatusStorage",
                "vector_db_storage_cls_kwargs": {
                    "cosine_better_than_threshold": 0.2,
                    "embedding_dim": settings.vector_store.embedding_dim,
                },
                "addon_params": {
                    "db_url": self._build_postgres_url(),
                    "use_jsonb": True,
                },
            })

        return config

    def _build_postgres_url(self) -> str:
        settings = get_settings()
        db = settings.database
        user = db.username or "postgres"
        password = db.password or ""
        host = db.host or "localhost"
        port = db.port or "5432"
        dbname = db.project_name or "glyphkeeper"
        return f"postgresql://{user}:{password}@{host}:{port}/{dbname}"

    # ── 核心操作 ──

    async def query(
        self,
        question: str,
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        top_k: int = 60,
        user_prompt: Optional[str] = None,
    ) -> str:
        """查询知识库"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("VectorStore 未初始化，请先调用 get_instance()")

        param = QueryParam(mode=mode, top_k=top_k)
        if user_prompt:
            param.user_prompt = user_prompt

        logger.debug(f"VectorStore.query: question={question[:50]}..., mode={mode}")
        try:
            return await self.rag.aquery(question, param=param)
        except Exception as e:
            logger.error(f"VectorStore 查询失败: {e}")
            raise

    async def insert(self, text: str, source_type: str = "narrative") -> bool:
        """插入文本内容到知识库"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("VectorStore 未初始化")

        try:
            await self.rag.ainsert(text)
            logger.debug(f"VectorStore.insert OK: len={len(text)}, type={source_type}")
            return True
        except Exception as e:
            logger.error(f"VectorStore 插入失败: {e}")
            return False

    async def insert_batch(self, contents: list[str]) -> int:
        """批量插入文本内容"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("VectorStore 未初始化")

        success = 0
        for content in contents:
            try:
                await self.rag.ainsert(content)
                success += 1
            except Exception as e:
                logger.error(f"批量插入中某项失败: {e}")
        logger.info(f"批量插入完成: {success}/{len(contents)}")
        return success

    async def clear(self) -> bool:
        """清空知识库（仅用于测试）"""
        if not self._initialized or self.rag is None:
            return False
        try:
            # LightRAG 没有直接的 clear 方法，通过删除工作目录实现
            import shutil
            settings = get_settings()
            if self.domain == "rules":
                data_dir = PROJECT_ROOT / "data" / "rules"
            else:
                data_dir = PROJECT_ROOT / "data" / "worlds"
            if data_dir.exists():
                shutil.rmtree(data_dir)
                data_dir.mkdir(parents=True, exist_ok=True)
            self._initialized = False
            self.rag = None
            return True
        except Exception as e:
            logger.error(f"VectorStore 清理失败: {e}")
            return False

    async def close(self):
        """关闭 VectorStore，释放资源"""
        if self.rag is not None:
            try:
                await self.rag.finalize_storages()
            except Exception as e:
                logger.error(f"关闭 VectorStore 失败: {e}")
        self._initialized = False
        self.rag = None

    @property
    def is_initialized(self) -> bool:
        return self._initialized
