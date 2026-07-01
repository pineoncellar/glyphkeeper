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

import psutil

from src.tools import get_logger, PROJECT_ROOT

from pgembed import get_server
from pgembed._commands import POSTGRES_BIN_PATH as _PG_BIN

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
            pgdata = str(self._pgdata_dir)
            os.makedirs(pgdata, exist_ok=True)

            # ── 防御：清理上次非干净关闭残留的 postmaster.pid ──
            pid_file = Path(pgdata) / "postmaster.pid"
            if pid_file.exists():
                # 检查 pid 文件中的进程是否还在运行
                try:
                    raw_pid = int(pid_file.read_text().splitlines()[0].strip())
                    proc_exists = psutil.pid_exists(raw_pid)
                except (ValueError, IndexError, OSError):
                    proc_exists = False
                if not proc_exists:
                    pid_file.unlink(missing_ok=True)
                    logger.debug("pgembed: 已清理残留的 postmaster.pid")

            # ── 预恢复：解决 Windows crash recovery 共享冲突问题 ──
            # pgembed 硬编码 pg_ctl 超时 10s，但 Windows 上 recovery 中 log 文件
            # 可能被防病毒锁定导致重试 30s+。这里先用长超时完成 recovery 再干净关闭。
            if (self._pgdata_dir / "PG_VERSION").exists():
                await self._run_crash_recovery(pgdata, _PG_BIN)

            self._pg_server = get_server(pgdata, cleanup_mode="stop")
            self._pg_server.ensure_pgdata_inited()
            logger.info("pgembed: 数据目录已初始化")

            self._pg_server.ensure_postgres_running()

            # ── 先连接默认 postgres 数据库做连通性验证 ──
            uri_postgres = self._pg_server.get_uri(database="postgres")
            if uri_postgres:
                import re
                m = re.search(r":(\d+)/", uri_postgres)
                if m:
                    self._port = m.group(1)
                # 尝试连接验证，最多重试 3 次
                import asyncpg
                for attempt in range(3):
                    try:
                        _test_conn = await asyncpg.connect(uri_postgres, timeout=5.0)
                        await _test_conn.close()
                        break
                    except asyncpg.exceptions.CannotConnectNowError:
                        if attempt < 2:
                            logger.warning(
                                f"pgembed: 连接被拒绝(PG仍在关闭中)，重试中 ({attempt+1}/3)"
                            )
                            await asyncio.sleep(1.0)
                            continue
                        raise
            logger.info("pgembed: PostgreSQL 已启动")

            # ── 创建项目数据库（如果不存在） ──
            dbs = self._pg_server.psql("SELECT datname FROM pg_database")
            if self._dbname not in dbs:
                self._pg_server.psql(f"CREATE DATABASE {self._dbname}")
                logger.info(f"pgembed: 数据库 '{self._dbname}' 已创建")

            # ── 最后切换到项目数据库 URI ──
            self._uri = self._pg_server.get_uri(database=self._dbname)

            # 安装 pgvector 扩展
            try:
                uri_no_db = self._uri.rsplit("/", 1)[0] + "/postgres"
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

    async def _run_crash_recovery(
        self, pgdata: str, pg_bin: Path
    ):
        """先手动完成 crash recovery，避免 pgembed 的 10s 超时问题

        ⚠ 注意: 必须用 tempfile 而非 capture_output=True 来捕获 pg_ctl 输出，
          否则 Windows 上 pg_ctl 的子进程继承管道句柄会导致无限挂起。
          （参考 pgembed._commands 中的相同处理方式）
        """
        import subprocess
        import tempfile

        pg_ctl = str(pg_bin / "pg_ctl.exe")
        port = self._find_free_port()

        pg_ctl_args = [
            pg_ctl, "-D", pgdata, "-w",
            "-o", '-h "127.0.0.1"',
            "-o", f"-p {port}",
            "start",
        ]

        def _do_start(timeout: int) -> bool:
            """尝试启动 PG，返回是否成功"""
            with (
                tempfile.TemporaryFile("w+") as stdout,
                tempfile.TemporaryFile("w+") as stderr,
            ):
                try:
                    result = subprocess.run(
                        pg_ctl_args, timeout=timeout,
                        stdout=stdout, stderr=stderr,
                    )
                    return result.returncode == 0
                except subprocess.TimeoutExpired:
                    return False

        # 先快试：数据目录干净时秒级返回
        started = _do_start(5)
        if not started:
            # 慢试：需要 crash recovery，重命名日志避免 sharing violation
            old_log = self._pgdata_dir / "log"
            if old_log.exists():
                old_log.rename(self._pgdata_dir / "log.bak")
                logger.debug("pgembed: 旧 log 已重命名为 log.bak")
            started = _do_start(60)
            if started:
                logger.info("pgembed: crash recovery 完成")

        if started:
            logger.info("pgembed: recovery 完成，干净关闭...")
            subprocess.run(
                [pg_ctl, "-D", pgdata, "-m", "fast", "-w", "stop"],
                timeout=30,
            )
            logger.info("pgembed: 数据目录已恢复到干净状态")
        else:
            logger.warning("pgembed: recovery 超时，强制终止后交由 pgembed 重试")
            subprocess.run(
                [pg_ctl, "-D", pgdata, "-m", "immediate", "stop"],
                timeout=10,
            )

    async def _stop_local(self):
        """停止本地 pgembed，兜底清理残留进程"""
        import subprocess
        pgdata = str(self._pgdata_dir)

        # 1. pgembed 自带清理（可能因 handle_pids 残留 PID 而跳过）
        try:
            if self._pg_server is not None:
                self._pg_server.cleanup()
        except Exception as e:
            logger.debug(f"pgembed: pg_server.cleanup() 异常: {e}")

        # 2. 直接 pg_ctl stop -m fast
        from pgembed._commands import POSTGRES_BIN_PATH as _PG_BIN
        pg_ctl = str(_PG_BIN / "pg_ctl.exe")
        try:
            subprocess.run(
                [pg_ctl, "-D", pgdata, "-m", "fast", "-w", "stop"],
                timeout=15, capture_output=True,
            )
        except Exception:
            pass

        # 3. 兜底：杀掉指向本 pgdata 目录的流浪 postgres 进程
        pgdata_abs = str(self._pgdata_dir.resolve()).lower()
        for proc in psutil.process_iter(["pid", "name", "cmdline"]):
            try:
                if proc.info.get("name", "").lower() != "postgres.exe":
                    continue
                cl = proc.info.get("cmdline")
                if cl and pgdata_abs in " ".join(cl).lower():
                    proc.kill()
                    logger.debug(f"pgembed: 已强制终止流浪 postgres (PID={proc.info['pid']})")
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass

        # 4. 清理残留文件
        for stale_file in ["postmaster.pid", ".handle_pids.json"]:
            fp = self._pgdata_dir / stale_file
            if fp.exists():
                try:
                    fp.unlink()
                except Exception:
                    pass

        logger.info("pgembed: PostgreSQL 已停止")



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
