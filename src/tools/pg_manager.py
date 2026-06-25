# -*- coding: utf-8 -*-
"""
@File     :   pg_manager.py
@Desc     :   PgManager — 嵌入式 PostgreSQL 连接管理器
@Note     :   基于 pgembed，自动捆绑 PostgreSQL 17.9 + pgvector
             无需系统预装，开箱即用，适合打包发布

使用方式:
    mgr = PgManager.get_instance()
    await mgr.start()
    url = mgr.uri  # "postgresql://postgres@localhost:PORT/glyphkeeper"
    await mgr.stop()
"""

from __future__ import annotations

import asyncio
import os
import sys
from enum import Enum
from pathlib import Path
from typing import Optional

from src.tools import get_logger, PROJECT_ROOT

logger = get_logger(__name__)


# ====================================================================
# 后端状态
# ====================================================================


class PgBackend(str, Enum):
    """PG 后端状态"""
    LOCAL = "local"         # pgembed 嵌入式
    NONE = "none"           # 不可用


# ====================================================================
# PgManager
# ====================================================================


class PgManager:
    """嵌入式 PostgreSQL 连接管理器（单例）

    基于 pgembed 捆绑的 PostgreSQL，自动管理进程生命周期。
    开箱即用，无需系统预装数据库服务。

    职责:
      - 检测 pgembed 可用性
      - 管理嵌入式 PG 的进程生命周期（initdb → start → stop）
      - 提供统一的连接 URI
      - 自动安装 pgvector 扩展
      - 提供健康检查
    """

    _instance: Optional["PgManager"] = None
    _lock = asyncio.Lock()

    def __init__(self):
        self._backend: PgBackend = PgBackend.NONE
        self._uri: str = ""
        self._port: str = ""
        self._dbname: str = "glyphkeeper"
        self._pg_server = None  # pgembed.PostgresServer 实例
        self._pgdata_dir: Optional[Path] = None
        self._started: bool = False

    # ── 单例 ──

    @classmethod
    async def get_instance(cls) -> "PgManager":
        """获取 PgManager 单例"""
        async with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
                await cls._instance._detect_backend()
            return cls._instance

    @classmethod
    async def reset_instance(cls):
        """重置单例（测试/重载时使用）"""
        async with cls._lock:
            if cls._instance is not None:
                await cls._instance.stop()
                cls._instance = None

    # ── 后端检测 ──

    async def _detect_backend(self):
        """检测 pgembed 是否可用"""
        if await self._check_pgembed():
            self._backend = PgBackend.LOCAL
            self._port = self._find_free_port()
            self._pgdata_dir = PROJECT_ROOT / "data" / "pg_data"
            self._uri = f"postgresql://postgres@localhost:{self._port}/{self._dbname}"
            logger.info(f"PG后端: LOCAL (pgembed, port={self._port})")
            return

        self._backend = PgBackend.NONE
        logger.info("PG后端: NONE (将使用 SQLite 降级)")

    async def _check_pgembed(self) -> bool:
        """检查 pgembed 是否可用（总是 True，除非未安装）"""
        try:
            import pgembed
            # 验证关键的二进制文件存在
            pgdir = Path(pgembed.__file__).parent
            pg_ctl = pgdir / "pginstall" / "bin" / "pg_ctl.exe"
            postgres = pgdir / "pginstall" / "bin" / "postgres.exe"
            if pg_ctl.exists() and postgres.exists():
                logger.debug(f"pgembed: 找到 PostgreSQL 于 {pgdir / 'pginstall'}")
                return True
            logger.warning(f"pgembed: PostgreSQL 二进制不完整 (pg_ctl={pg_ctl.exists()}, postgres={postgres.exists()})")
            return False
        except ImportError:
            logger.debug("pgembed: 未安装")
            return False
        except Exception as e:
            logger.debug(f"pgembed: 检测异常 {e}")
            return False

    def _find_free_port(self) -> str:
        """找一个可用端口"""
        import socket
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(("", 0))
            return str(s.getsockname()[1])

    def _build_uri(self) -> str:
        return f"postgresql://postgres@localhost:{self._port}/{self._dbname}"

    # ── 生命周期 ──

    async def start(self):
        """启动嵌入式 PostgreSQL"""
        if self._started:
            return
        if self._backend == PgBackend.LOCAL:
            await self._start_local()
        self._started = True

    async def stop(self):
        """停止嵌入式 PostgreSQL"""
        if not self._started:
            return
        if self._backend == PgBackend.LOCAL:
            await self._stop_local()
        self._started = False

    async def _start_local(self):
        """启动嵌入式 PostgreSQL"""
        try:
            from pgembed import get_server

            pgdata = str(self._pgdata_dir)
            os.makedirs(pgdata, exist_ok=True)

            self._pg_server = get_server(pgdata, cleanup_mode="stop")
            self._pg_server.ensure_pgdata_inited()
            logger.info("pgembed: 数据目录已初始化")

            self._pg_server.ensure_postgres_running()
            logger.info("pgembed: PostgreSQL 已启动")

            # 更新 URI 和端口
            uri = self._pg_server.get_uri(database=self._dbname)
            if uri:
                self._uri = uri
                import re
                m = re.search(r":(\d+)/", uri)
                if m:
                    self._port = m.group(1)

            # 创建项目数据库（如果不存在）
            dbs = self._pg_server.psql("SELECT datname FROM pg_database")
            if self._dbname not in dbs:
                self._pg_server.psql(f"CREATE DATABASE {self._dbname}")
                logger.info(f"pgembed: 数据库 '{self._dbname}' 已创建")

            # 安装 pgvector 扩展
            try:
                uri_no_db = self._uri.rsplit("/", 1)[0] + "/postgres"
                import asyncpg
                _vconn = await asyncpg.connect(uri_no_db)
                await _vconn.execute("CREATE EXTENSION IF NOT EXISTS vector")
                ver = await _vconn.fetchval(
                    "SELECT extversion FROM pg_extension WHERE extname='vector'"
                )
                await _vconn.close()
                logger.info(f"pgembed: pgvector v{ver} 扩展已安装")
            except Exception as e:
                logger.info(f"pgembed: pgvector 不可用 ({e})，使用本地向量存储降级")

        except Exception as e:
            logger.error(f"pgembed: 启动失败: {e}")
            self._backend = PgBackend.NONE
            self._started = False

    async def _stop_local(self):
        """停止本地 pgembed"""
        try:
            if self._pg_server is not None:
                self._pg_server.cleanup()
                logger.info("pgembed: PostgreSQL 已停止")
        except Exception as e:
            logger.warning(f"pgembed: 停止时异常: {e}")



    # ── 属性和查询 ──

    @property
    def backend(self) -> PgBackend:
        """当前后端类型"""
        return self._backend

    @property
    def uri(self) -> str:
        """PostgreSQL 连接 URI"""
        return self._uri

    @property
    def available(self) -> bool:
        """PG 是否可用"""
        return self._backend != PgBackend.NONE

    @property
    def port(self) -> str:
        return self._port

    @property
    def dbname(self) -> str:
        return self._dbname

    async def health(self) -> dict:
        """健康检查"""
        result = {
            "backend": self._backend.value,
            "available": self.available,
            "started": self._started,
            "port": self._port,
            "dbname": self._dbname,
        }
        if self.available and self._started:
            try:
                import asyncpg
                conn = await asyncpg.connect(
                    self._uri, timeout=3.0,
                )
                version = await conn.fetchval("SELECT version()")
                ext_count = await conn.fetchval(
                    "SELECT count(*) FROM pg_extension"
                )
                await conn.close()
                result["pg_version"] = version
                result["extensions"] = ext_count
                result["status"] = "healthy"
            except Exception as e:
                result["status"] = "unhealthy"
                result["error"] = str(e)
        else:
            result["status"] = "unavailable"
        return result


# ====================================================================
# 便捷函数
# ====================================================================


async def get_pg_uri(force_local: bool = True) -> str:
    """获取 PG 连接 URI（便捷函数）
    
    参数:
        force_local: True=仅尝试本地 pglite
    """
    mgr = await PgManager.get_instance(force_local)
    return mgr.uri


async def is_pg_available(force_local: bool = True) -> bool:
    """检查 PG 是否可用

    参数:
        force_local: True=仅检测本地 pgembed
    """
    mgr = await PgManager.get_instance(force_local)
    return mgr.available


async def get_pg_backend() -> str:
    """获取当前后端类型"""
    mgr = await PgManager.get_instance()
    return mgr.backend.value


async def ensure_pg_started():
    """确保 PG 已启动"""
    mgr = await PgManager.get_instance()
    if mgr.available and not mgr._started:
        await mgr.start()


# ── 上下文管理器 ──


class PgSession:
    """异步上下文管理器，自动管理 PG 生命周期

    使用方式:
        async with PgSession():
            url = await get_pg_uri()
            # ... 使用 PG ...
    """

    async def __aenter__(self):
        mgr = await PgManager.get_instance()
        if mgr.available:
            await mgr.start()
        return mgr

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        mgr = await PgManager.get_instance()
        if mgr.available:
            await mgr.stop()
