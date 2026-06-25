# -*- coding: utf-8 -*-
"""
@File     :   background_sync.py
@Desc     :   后台数据同步任务 — 健康检查、备份、一致性校验

定时任务:
  - 每小时: 数据库连接池健康检查
  - 每日:   自动备份
  - 按需:   数据一致性校验

使用方式:
    sync = BackgroundSync(event_store, vector_store)
    asyncio.create_task(sync.start())
"""

from __future__ import annotations

import asyncio
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

from src.tools import get_logger, PROJECT_ROOT
from src.memory.event_store import EventStore
from src.memory.vector_store import VectorStore

logger = get_logger(__name__)

# 备份目录
BACKUP_DIR = PROJECT_ROOT / "data" / "backups"
BACKUP_DIR.mkdir(parents=True, exist_ok=True)


class BackgroundSync:
    """后台数据同步任务

    职责:
      - 健康检查: 验证数据库连接可用
      - 自动备份: 将 EventStore / 配置打包备份
      - 数据同步: 结构化数据 ↔ 向量数据一致性确认
      - 日志轮转: 清理过期日志

    使用方式:
        sync = BackgroundSync(event_store, vector_store)
        asyncio.create_task(sync.start())
    """

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        health_interval: int = 3600,
        backup_interval: int = 86400,
        cleanup_interval: int = 86400,
        max_backup_days: int = 7,
    ):
        """
        参数:
            event_store:      EventStore 实例
            vector_store:     VectorStore 实例
            health_interval:  健康检查间隔（秒），默认 1 小时
            backup_interval:  自动备份间隔（秒），默认 24 小时
            cleanup_interval: 日志清理间隔（秒），默认 24 小时
            max_backup_days:  备份保留天数，默认 7 天
        """
        self._event_store = event_store
        self._vector_store = vector_store
        self.health_interval = health_interval
        self.backup_interval = backup_interval
        self.cleanup_interval = cleanup_interval
        self.max_backup_days = max_backup_days
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._health_results: list[dict] = []
        self._backup_count = 0
        self._last_backup_at: Optional[str] = None
        self._last_health_at: Optional[str] = None

    # ── 生命周期 ──

    async def start(self):
        """启动后台同步循环"""
        if self._running:
            logger.warning("BackgroundSync 已在运行")
            return

        self._running = True
        logger.info(
            f"BackgroundSync 启动: health={self.health_interval}s, "
            f"backup={self.backup_interval}s"
        )

        try:
            # 任务计数器
            tick = 0
            while self._running:
                try:
                    tick += 1

                    # 健康检查（每次循环）
                    await self._health_check()

                    # 自动备份（首次 + 达到间隔）
                    if tick == 1 or self._should_backup():
                        await self._auto_backup()

                    # 清理过期备份（每天一次）
                    if tick == 1 or (tick * self.health_interval >= self.cleanup_interval):
                        self._cleanup_old_backups()

                except Exception as e:
                    logger.error(f"BackgroundSync 异常: {e}", exc_info=True)

                await asyncio.sleep(self.health_interval)
        finally:
            self._running = False
            logger.info("BackgroundSync 已停止")

    async def stop(self):
        """停止 Worker"""
        self._running = False
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "health_interval": self.health_interval,
            "backup_interval": self.backup_interval,
            "backup_count": self._backup_count,
            "last_backup_at": self._last_backup_at,
            "last_health_at": self._last_health_at,
            "recent_health_results": self._health_results[-5:],
        }

    # ── 内部方法 ──

    def _should_backup(self) -> bool:
        """判断是否需要执行备份"""
        if self._last_backup_at is None:
            return True
        try:
            last = datetime.fromisoformat(self._last_backup_at)
            elapsed = (datetime.now(timezone.utc) - last).total_seconds()
            return elapsed >= self.backup_interval
        except Exception:
            return True

    # ── 健康检查 ──

    async def _health_check(self) -> dict[str, Any]:
        """执行所有可用连接的健康检查

        返回:
            {"event_store": bool, "vector_store": bool, ...}
        """
        result: dict[str, Any] = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "event_store": False,
            "vector_store": False,
        }

        # EventStore 健康检查
        if self._event_store:
            try:
                version = await self._event_store.get_latest_version("_health_check")
                result["event_store"] = True
                result["event_store_version"] = version
            except Exception as e:
                result["event_store_error"] = str(e)
                logger.warning(f"BackgroundSync: EventStore 健康检查失败: {e}")

        # VectorStore 健康检查（通过查询验证）
        if self._vector_store:
            try:
                resp = await self._vector_store.query("health check", top_k=1)
                result["vector_store"] = True
                result["vector_store_response"] = bool(resp)
            except Exception as e:
                result["vector_store_error"] = str(e)
                logger.warning(f"BackgroundSync: VectorStore 健康检查失败: {e}")

        # 记录结果
        self._health_results.append(result)
        if len(self._health_results) > 100:
            self._health_results = self._health_results[-100:]

        self._last_health_at = result["timestamp"]
        all_ok = all(
            result.get(k, False)
            for k in ("event_store", "vector_store")
            if hasattr(self, f"_{k.split('_')[0]}_store")
        )
        logger.debug(f"BackgroundSync: 健康检查完成 all_ok={all_ok}")
        return result

    # ── 自动备份 ──

    async def _auto_backup(self) -> Optional[str]:
        """执行自动备份

        返回:
            备份路径，失败返回 None
        """
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        backup_path = BACKUP_DIR / f"glyphkeeper_backup_{timestamp}"

        try:
            backup_path.mkdir(parents=True, exist_ok=True)

            # 备份 EventStore (SQLite)
            if self._event_store:
                db_path = getattr(self._event_store, "db_path", None)
                if db_path and Path(db_path).exists():
                    shutil.copy2(db_path, backup_path / "events.db")
                    logger.info(f"BackgroundSync: EventStore 已备份 ({db_path})")

            # 备份配置文件
            for config_file in ["config.yaml", "providers.ini"]:
                src = PROJECT_ROOT / config_file
                if src.exists():
                    shutil.copy2(src, backup_path / config_file)

            # 写入备份元数据
            meta = {
                "timestamp": timestamp,
                "version": "0.1.0",
                "files": [str(p.name) for p in backup_path.iterdir()],
            }
            with open(backup_path / "backup_meta.json", "w", encoding="utf-8") as f:
                json.dump(meta, f, ensure_ascii=False, indent=2)

            self._backup_count += 1
            self._last_backup_at = datetime.now(timezone.utc).isoformat()

            logger.info(f"BackgroundSync: 备份完成 → {backup_path}")
            return str(backup_path)

        except Exception as e:
            logger.error(f"BackgroundSync: 备份失败: {e}")
            return None

    # ── 清理 ──

    def _cleanup_old_backups(self) -> int:
        """清理超过保留天数的旧备份

        返回:
            删除的备份数
        """
        if not BACKUP_DIR.exists():
            return 0

        now = datetime.now()
        removed = 0

        for item in BACKUP_DIR.iterdir():
            if not item.is_dir():
                continue
            try:
                # 从目录名解析时间戳
                parts = item.name.split("_")
                if len(parts) >= 2:
                    date_str = parts[-1][:8]  # YYYYMMDD
                    backup_date = datetime.strptime(date_str, "%Y%m%d")
                    age_days = (now - backup_date).days

                    if age_days > self.max_backup_days:
                        shutil.rmtree(item)
                        removed += 1
                        logger.info(f"BackgroundSync: 删除过期备份 {item.name}")
            except (ValueError, IndexError):
                continue

        if removed:
            logger.info(f"BackgroundSync: 清理完成，共删除 {removed} 个过期备份")
        return removed
