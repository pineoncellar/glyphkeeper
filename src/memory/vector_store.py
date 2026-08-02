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
  - 单例模式：每个 domain + world_id 组合一个实例
  - domain="world" → workspace 由 world_id 确定
  - domain="rules" → 固定 workspace="rules"
  - LLM 和 Embedding 函数通过 lazy import 加载（避免循环依赖）
"""

import asyncio
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Optional, Literal

from lightrag import LightRAG, QueryParam
from lightrag.llm.openai import openai_complete_if_cache

from src.tools import get_settings, get_logger, PROJECT_ROOT
from src.tools.config import _DEBUG_MODE

logger = get_logger(__name__)

# ── 修复 LightRAG 日志格式 ──
# LightRAG 默认日志格式为 "%(levelname)s: %(message)s"（无时间戳），
# 且 logger.propagate=False，消息无法透传到项目日志系统。
# 此处替换其 handler 的 formatter 使格式与项目一致。
_lightrag_logger = logging.getLogger("lightrag")
_LIGHTRAG_FORMAT = "[%(asctime)s] [%(levelname)s] [%(name)s] - %(message)s"
_LIGHTRAG_DATE_FMT = "%Y-%m-%d %H:%M:%S"
for _h in _lightrag_logger.handlers:
    _h.setFormatter(logging.Formatter(_LIGHTRAG_FORMAT, datefmt=_LIGHTRAG_DATE_FMT))
    _h.setLevel(logging.DEBUG if _DEBUG_MODE else logging.WARNING)
_lightrag_logger.setLevel(logging.DEBUG if _DEBUG_MODE else logging.WARNING)

# LightRAG 内部 shared_storage.direct_log() 直接用 print() 输出到 stderr，
# 格式为 "DEBUG: xxx"，无时间戳无模块名。patch 为走 lightrag logger。
try:
    from lightrag.kg.shared_storage import direct_log as _original_direct_log

    def _patched_direct_log(message, enable_output=True, level="DEBUG"):
        """替换 direct_log 使其走 lightrag logger 而非 print()"""
        getattr(_lightrag_logger, level.lower(), _lightrag_logger.debug)(message)

    import lightrag.kg.shared_storage as _ss
    _ss.direct_log = _patched_direct_log
except Exception:
    pass

# ── 屏蔽 LightRAG 文件锁噪音日志 ──
# LightRAG 每次 KG 查询都会刷几十行 "== Lock == Process N: Acquired/released lock"
# 这些是 PG 后端不需要的文件锁操作，但 LightRAG 仍会打印。
class _LockFilter(logging.Filter):
    def filter(self, record):
        msg = record.getMessage()
        return "== Lock ==" not in msg and "lock " not in msg.lower()

# 如果 debug 模式开启，至少过滤掉锁日志
for _h in _lightrag_logger.handlers:
    _h.addFilter(_LockFilter())
_lightrag_logger.addFilter(_LockFilter())


class VectorStore:
    """向量/图存储 — LightRAG 单例封装"""

    _instances: dict[str, "VectorStore"] = {}
    _lock = asyncio.Lock()

    def __init__(self, domain: str):
        self.domain = domain
        self.rag: Optional[LightRAG] = None
        self._initialized = False
        self._world_id_override: str = ""

    # ── 单例管理 ──

    @classmethod
    async def get_instance(
        cls,
        domain: str = "world",
        llm_tier: str = "standard",
        force_reinit: bool = False,
        world_id: str = "",
    ) -> "VectorStore":
        """获取指定 domain 的 VectorStore 实例（单例）

        参数:
            domain:     数据域 ("world" / "rules")
            llm_tier:   LLM 模型层级
            force_reinit: 强制重新初始化
            world_id:   世界标识
        """
        # 多世界缓存键：domain + world_id，确保不同世界隔离
        cache_key = f"{domain}:{world_id}" if world_id and domain == "world" else domain

        async with cls._lock:
            if cache_key not in cls._instances or force_reinit:
                instance = cls(domain)
                instance._world_id_override = world_id
                await instance._initialize(llm_tier)
                cls._instances[cache_key] = instance
            return cls._instances[cache_key]

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

        if self.domain == "rules":
            working_dir = PROJECT_ROOT / "data" / "rules"
            workspace = "rules"
        else:
            if not self._world_id_override:
                raise ValueError(
                    "VectorStore(domain='world') 需要显式指定 world_id。"
                    "请通过 get_instance(domain='world', world_id=...) 传入。"
                )
            working_dir = PROJECT_ROOT / "data" / "worlds"
            workspace = self._world_id_override

        working_dir.mkdir(parents=True, exist_ok=True)

        llm_func = self._create_llm_func(llm_tier)
        embedding_func = self._create_embedding_func()
        storage_config = await self._build_storage_config(str(working_dir), workspace)

        try:
            self.rag = LightRAG(
                llm_model_func=llm_func,
                embedding_func=embedding_func,
                entity_extract_max_gleaning=0,
                embedding_func_max_async=16,
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
        settings = get_settings()
        model_config, provider_config = settings.get_full_model_config(tier)

        # 创建禁用代理的 httpx 客户端
        # TODO: 优化此处代码
        import httpx
        _no_proxy_client = httpx.AsyncClient(proxy=None, timeout=120.0)

        async def llm_model_func(
            prompt: str,
            system_prompt: Optional[str] = None,
            history_messages: Optional[list] = None,
            **kwargs,
        ) -> str:
            logger.debug(
                f"[LLM] model={model_config.model_name} "
                f"url={provider_config.base_url} "
                f"prompt_len={len(prompt)}"
            )
            try:
                import httpx
                client = httpx.AsyncClient(proxy=None, timeout=120.0)
                try:
                    result = await openai_complete_if_cache(
                        model=model_config.model_name,
                        prompt=prompt,
                        system_prompt=system_prompt,
                        history_messages=history_messages or [],
                        api_key=provider_config.api_key,
                        base_url=provider_config.base_url,
                        openai_client_configs={"http_client": client},
                        **kwargs,
                    )
                finally:
                    await client.aclose()
                logger.debug(f"[LLM] OK: len={len(result)}")
                return result
            except Exception as e:
                logger.error(f"[LLM] FAILED: {type(e).__name__}: {e}")
                raise

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

        # 每个 embedding 调用使用独立的无代理 httpx 客户端
        import httpx

        async def _make_embedding(texts: list[str]) -> np.ndarray:
            """执行单次 embedding 调用（独立 httpx 客户端）"""
            client = httpx.AsyncClient(proxy=None, timeout=120.0)
            try:
                return await raw_openai_embed(
                    texts=texts,
                    model=vector_config.embedding_model_name,
                    api_key=provider_config.api_key,
                    base_url=provider_config.base_url,
                    client_configs={
                        "http_client": client,
                        "max_retries": 0,   # httpx 客户端自身不重试
                        "timeout": 120.0,
                    },
                )
            finally:
                await client.aclose()

        async def embedding_func(texts: list[str]) -> np.ndarray:
            logger.debug(
                f"[EMBED] model={vector_config.embedding_model_name} "
                f"url={provider_config.base_url} "
                f"texts={len(texts)} chunks"
            )
            try:
                result = await _make_embedding(texts)
                logger.debug(f"[EMBED] OK: shape={result.shape}")
                return result
            except Exception as e:
                logger.error(
                    f"[EMBED] FAILED: {type(e).__name__}: {e} "
                    f"url={provider_config.base_url} "
                    f"model={vector_config.embedding_model_name}"
                )
                raise

        return EmbeddingFunc(
            embedding_dim=vector_config.embedding_dim,
            max_token_size=8192,
            func=embedding_func,
        )

    async def _build_storage_config(self, working_dir: str, workspace: str) -> dict:
        """构建 LightRAG 存储配置

        要求 PostgreSQL (pgembed) 必须可用，否则抛出异常。
        """
        settings = get_settings()

        # ── 启动 PG ──
        from src.tools.pg_manager import PgManager
        mgr = await PgManager.get_instance()

        if not mgr.available:
            raise RuntimeError(
                "PostgreSQL (pgembed) 不可用，LightRAG 需要 PG 存储。"
                "请检查 pgembed 是否已安装。"
            )

        await mgr.start()

        # ── 设置环境变量（LightRAG 的 PG 存储通过环境变量获取连接信息） ──
        from urllib.parse import urlparse
        parsed = urlparse(mgr.uri)
        os.environ["POSTGRES_USER"] = parsed.username or "postgres"
        os.environ["POSTGRES_PASSWORD"] = parsed.password or ""
        os.environ["POSTGRES_DATABASE"] = parsed.path.lstrip("/") or "glyphkeeper"
        os.environ["POSTGRES_HOST"] = parsed.hostname or "localhost"
        os.environ["POSTGRES_PORT"] = str(parsed.port or 5432)

        logger.info(
            f"VectorStore: 使用 PG 后端 (pgvector, {mgr.backend.value}, "
            f"host={parsed.hostname}, port={parsed.port})"
        )

        return {
            "working_dir": working_dir,
            "workspace": workspace,
            "graph_storage": "NetworkXStorage",
            "vector_storage": "PGVectorStorage",
            "kv_storage": "PGKVStorage",
            "doc_status_storage": "PGDocStatusStorage",
            "vector_db_storage_cls_kwargs": {
                "cosine_better_than_threshold": 0.2,
                "embedding_dim": settings.vector_store.embedding_dim,
            },
            "addon_params": {
                "db_url": mgr.uri,
                "use_jsonb": True,
            },
        }

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

    async def insert(self, text: str | list[str], source_type: str = "narrative") -> bool:
        """插入文本内容到知识库（支持单篇或批量列表，批量时并发处理）"""
        if not self._initialized or self.rag is None:
            raise RuntimeError("VectorStore 未初始化")

        try:
            await self.rag.ainsert(text)
            count = len(text) if isinstance(text, list) else 1
            logger.debug(f"VectorStore.insert OK: {count} docs, type={source_type}")
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

    # ── 存档用：LightRAG 全量备份与恢复 ──

    @property
    def _workspace(self) -> str:
        """获取当前实例的 workspace 名称"""
        if self.domain == "rules":
            return "rules"
        if not self._world_id_override:
            raise ValueError(
                "VectorStore(domain='world') 缺失 world_id，"
                "无法确定 workspace 名称。"
                "请确保通过 get_instance(world_id=...) 传入。"
            )
        return self._world_id_override

    _BACKUP_TABLES = [
        "LIGHTRAG_VDB_ENTITY", "LIGHTRAG_VDB_RELATION", "LIGHTRAG_VDB_CHUNKS",
        "LIGHTRAG_LLM_CACHE", "LIGHTRAG_DOC_STATUS", "LIGHTRAG_DOC_FULL",
        "LIGHTRAG_DOC_CHUNKS", "LIGHTRAG_FULL_ENTITIES", "LIGHTRAG_FULL_RELATIONS",
        "LIGHTRAG_ENTITY_CHUNKS", "LIGHTRAG_RELATION_CHUNKS",
    ]

    async def backup_to(self, backup_dir: Path) -> bool:
        """将当前 workspace 的 LightRAG 数据全量备份到指定目录

        对每张表按 workspace 过滤导出为 JSON，同时拷贝 graphml 文件。
        备份目录结构:
          backup_dir/
            lightrag/
              LIGHTRAG_VDB_ENTITY.json
              LIGHTRAG_VDB_RELATION.json
              ... (每张表一个 JSON)
              graph_chunk_entity_relation.graphml  (如有)
        """
        import json as _json

        workspace = self._workspace
        data_dir = backup_dir / "lightrag"
        data_dir.mkdir(parents=True, exist_ok=True)

        try:
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            if not mgr.available:
                await mgr.start()
            import asyncpg
            conn = await asyncpg.connect(mgr.uri)

            for tbl in self._BACKUP_TABLES:
                rows = await conn.fetch(
                    f"SELECT * FROM {tbl} WHERE workspace=$1", workspace
                )
                if rows:
                    records = [dict(r) for r in rows]
                    # 向量字段需要特殊处理
                    for rec in records:
                        for k, v in rec.items():
                            if isinstance(v, memoryview):
                                rec[k] = list(bytes(v))
                    (data_dir / f"{tbl}.json").write_text(
                        _json.dumps(records, ensure_ascii=False, default=str),
                        encoding="utf-8",
                    )
                    logger.debug(f"LightRAG 备份: {tbl} ({len(records)} 条)")

            await conn.close()

            # 拷贝 graphml 文件
            if self.domain == "rules":
                graphml_dir = PROJECT_ROOT / "data" / "rules"
            else:
                graphml_dir = PROJECT_ROOT / "data" / "worlds" / workspace
            graphml_file = graphml_dir / "graph_chunk_entity_relation.graphml"
            if graphml_file.exists():
                import shutil
                shutil.copy2(str(graphml_file), str(data_dir / graphml_file.name))
                logger.debug(f"LightRAG 备份: graphml 文件已拷贝")

            logger.info(f"LightRAG 备份完成: workspace={workspace}, dir={data_dir}")
            return True

        except Exception as e:
            logger.error(f"LightRAG 备份失败: {e}")
            return False

    async def restore_from(self, backup_dir: Path) -> bool:
        """从备份目录恢复 LightRAG 数据到当前 workspace

        先清空当前 workspace 的所有 LightRAG 数据，再逐表恢复。
        恢复后需重新初始化 VectorStore。
        """
        import json as _json

        workspace = self._workspace
        data_dir = backup_dir / "lightrag"
        if not data_dir.exists():
            logger.warning(f"LightRAG 备份目录不存在: {data_dir}")
            return False

        try:
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            if not mgr.available:
                await mgr.start()
            import asyncpg
            conn = await asyncpg.connect(mgr.uri)

            # 先清空当前 workspace 的数据
            for tbl in self._BACKUP_TABLES:
                # 检查表是否存在
                exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.tables WHERE table_name=$1",
                    tbl.lower(),
                )
                if exists:
                    await conn.execute(
                        f"DELETE FROM {tbl} WHERE workspace=$1", workspace
                    )
                    logger.debug(f"LightRAG 恢复: 清空 {tbl}")

            # 逐表恢复
            restored_count = 0
            for tbl in self._BACKUP_TABLES:
                json_file = data_dir / f"{tbl}.json"
                if not json_file.exists():
                    continue
                records = _json.loads(json_file.read_text(encoding="utf-8"))
                if not records:
                    continue

                # 取列名（排除向量字段的特殊处理）
                columns = list(records[0].keys())
                col_names = ", ".join(columns)
                placeholders = ", ".join(f"${i+1}" for i in range(len(columns)))

                from datetime import datetime as _dt

                def _parse_timestamp(val):
                    """将 ISO 格式时间戳字符串转为 datetime 对象
                    兼容 YYYY-MM-DDTHH:MM:SS 和 YYYY-MM-DD HH:MM:SS 两种格式"""
                    if isinstance(val, str) and len(val) >= 19:
                        head = val[:19]
                        if head[4] == "-" and head[7] == "-" and head[10] in ("T", " ") and head[13] == ":":
                            try:
                                return _dt.fromisoformat(val.replace("Z", "+00:00"))
                            except (ValueError, TypeError):
                                pass
                    return val

                for rec in records:
                    values = []
                    for col in columns:
                        val = rec.get(col)
                        # 向量字段：bytes → memoryview
                        if isinstance(val, list) and col.endswith("_vector"):
                            import struct
                            val = memoryview(bytes(val))
                        # 时间戳字段：str → datetime
                        val = _parse_timestamp(val)
                        values.append(val)
                    try:
                        await conn.execute(
                            f"INSERT INTO {tbl} ({col_names}) VALUES ({placeholders})"
                            f" ON CONFLICT (workspace, id) DO NOTHING",
                            *values,
                        )
                        restored_count += 1
                    except Exception as e:
                        logger.debug(f"LightRAG 恢复: 跳过 {tbl} 中一行 ({e})")

                logger.debug(f"LightRAG 恢复: {tbl} ({len(records)} 条)")

            await conn.close()

            # 恢复 graphml 文件
            backup_graphml = data_dir / "graph_chunk_entity_relation.graphml"
            if backup_graphml.exists():
                if self.domain == "rules":
                    target_dir = PROJECT_ROOT / "data" / "rules"
                else:
                    target_dir = PROJECT_ROOT / "data" / "worlds" / workspace
                target_dir.mkdir(parents=True, exist_ok=True)
                import shutil
                shutil.copy2(str(backup_graphml), str(target_dir / backup_graphml.name))
                logger.debug(f"LightRAG 恢复: graphml 文件已恢复")

            # 关闭当前实例并标记需重新初始化
            await self.close()
            self._initialized = False

            logger.info(
                f"LightRAG 恢复完成: workspace={workspace}, "
                f"总恢复行数={restored_count}"
            )
            return True

        except Exception as e:
            logger.error(f"LightRAG 恢复失败: {e}")
            return False

    # ------- 种子工作区管理 -------

    @staticmethod
    def seed_workspace_name(module_name: str) -> str:
        """返回模组对应的种子工作区名称"""
        return f"__seed__{module_name}"

    async def copy_workspace_from(self, source_workspace: str) -> bool:
        """将 source_workspace 的 LightRAG 数据全量复制到当前 workspace

        PG 级批量 INSERT...SELECT，重写 workspace 和 id 列。
        适用于种子工作区 → 新世界的高速复制。
        复制完后当前实例失效，需重新初始化。
        """
        target_workspace = self._workspace
        if source_workspace == target_workspace:
            logger.warning("copy_workspace_from: 源和目标 workspace 相同，跳过")
            return False

        try:
            from src.tools.pg_manager import PgManager
            mgr = await PgManager.get_instance()
            if not mgr.available:
                await mgr.start()
            import asyncpg
            conn = await asyncpg.connect(mgr.uri)

            total_rows = 0
            for tbl in self._BACKUP_TABLES:
                # 检查表是否存在
                exists = await conn.fetchval(
                    "SELECT 1 FROM information_schema.tables WHERE table_name=$1",
                    tbl.lower(),
                )
                if not exists:
                    continue

                # 获取列信息（排除向量字段的特殊处理）
                columns = await conn.fetch(
                    "SELECT column_name, data_type FROM information_schema.columns "
                    "WHERE table_name=$1 ORDER BY ordinal_position",
                    tbl.lower(),
                )
                col_names = [c["column_name"] for c in columns]

                # 构建 SELECT 子句：重写 id(新UUID) + workspace(目标) + 其余原样
                select_parts = []
                for c in col_names:
                    if c == "id":
                        select_parts.append("gen_random_uuid()")
                    elif c == "workspace":
                        select_parts.append(f"'{target_workspace}'")
                    elif c.endswith("_vector"):
                        # 向量列需要特殊处理，保持原值
                        select_parts.append(c)
                    else:
                        select_parts.append(c)

                select_sql = ", ".join(select_parts)
                insert_sql = ", ".join(col_names)

                sql = (
                    f"INSERT INTO {tbl} ({insert_sql}) "
                    f"SELECT {select_sql} FROM {tbl} "
                    f"WHERE workspace = $1 "
                    f"ON CONFLICT (workspace, id) DO NOTHING"
                )
                result = await conn.execute(sql, source_workspace)
                total_rows += 1
                logger.debug(
                    f"copy_workspace_from: {tbl} {result} "
                    f"({source_workspace} → {target_workspace})"
                )

            await conn.close()

            # 复制 graphml 文件
            from src.tools import PROJECT_ROOT
            src_graphml = (
                PROJECT_ROOT / "data" / "worlds" / source_workspace
                / "graph_chunk_entity_relation.graphml"
            )
            if src_graphml.exists():
                import shutil
                target_dir = PROJECT_ROOT / "data" / "worlds" / target_workspace
                target_dir.mkdir(parents=True, exist_ok=True)
                shutil.copy2(str(src_graphml), str(target_dir / src_graphml.name))
                logger.debug(f"copy_workspace_from: graphml 文件已复制")

            # 关闭当前实例，标记需重新初始化
            await self.close()
            self._initialized = False

            logger.info(
                f"copy_workspace_from: {source_workspace} → {target_workspace}, "
                f"涉及 {total_rows} 张表"
            )
            return True

        except Exception as e:
            logger.error(f"copy_workspace_from 失败: {e}")
            return False
