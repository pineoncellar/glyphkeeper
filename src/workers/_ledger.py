# -*- coding: utf-8 -*-
"""
@File     :   _ledger.py
@Desc     :   共享隔离账本 + 判定引擎 — MemorizerWorker 和 WorldSummarizer 共用
@Note     :   每个 world_id 持有独立的三字段内存桶，Worker 循环据此判定是否触发固化刷盘。
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from src.tools import get_logger

logger = get_logger(__name__)


# ====================================================================
# 单世界隔离桶
# ====================================================================


@dataclass
class LedgerBucket:
    """单个世界的内存隔离账本 — 仅记数，不持文本"""

    unread_tokens: int = 0
    """自上次固化以来累积的未读发言 Token 估算值"""

    has_boundary: bool = False
    """当前一幕是否踩中了跨场景/切阶段边界"""

    target_version: int = 0
    """前台最新推进到的 EventStore version 终点线"""

    last_signal_at: float = 0.0
    """最近一次收到信号的时间戳（time.time），用于 TimeBased 策略"""


# ====================================================================
# 世界级互斥锁
# ====================================================================


_world_locks: dict[str, asyncio.Lock] = {}
_world_locks_lock = asyncio.Lock()


async def get_world_lock(world_id: str) -> asyncio.Lock:
    """获取指定世界的互斥锁，防止同一世界并发刷盘"""
    async with _world_locks_lock:
        if world_id not in _world_locks:
            _world_locks[world_id] = asyncio.Lock()
        return _world_locks[world_id]


# ====================================================================
# 判定引擎 — Gatekeeper
# ====================================================================


class Gatekeeper:
    """状态评估判定引擎 — 组合三种策略判定是否放水收网

    参数:
        token_threshold:     TokenCount 策略阈值
        time_idle_threshold: TimeBased 兜底策略阈值（秒）
        min_flush_interval:  最小刷盘间隔（秒）

    使用方式:
        gate = Gatekeeper(token_threshold=2000, min_flush_interval=180)
        if await gate.should_flush(bucket, now, last_flush_at):
            ...
    """

    def __init__(
        self,
        token_threshold: int = 2000,
        time_idle_threshold: int = 600,
        min_flush_interval: int = 180,
    ):
        self.token_threshold = token_threshold
        self.time_idle_threshold = time_idle_threshold
        self.min_flush_interval = min_flush_interval

    async def should_flush(
        self,
        bucket: LedgerBucket,
        now: float,
        last_flush_at: Optional[float] = None,
    ) -> bool:
        """判定是否应当触发固化刷盘

        先检查最小间隔，再依次检查三条策略。
        任一条满足即返回 True。

        参数:
            bucket:         世界的隔离账本桶
            now:            当前 time.time()
            last_flush_at:  上次刷盘时间戳，None 表示从未刷过
        """
        # 空桶不触发
        if bucket.target_version == 0:
            return False

        # 最小间隔保护
        if last_flush_at is not None:
            elapsed_since_flush = now - last_flush_at
            if elapsed_since_flush < self.min_flush_interval:
                return False

        # 策略 A: TopicEnd — 边界信号触发
        if bucket.has_boundary:
            logger.debug("Gatekeeper: TopicEnd 策略触发")
            return True

        # 策略 B: TokenCount — 积压超标触发
        if bucket.unread_tokens >= self.token_threshold:
            logger.debug("Gatekeeper: TokenCount 策略触发")
            return True

        # 策略 C: TimeBased — 长考/挂机兜底
        idle_seconds = now - bucket.last_signal_at
        if idle_seconds >= self.time_idle_threshold and bucket.target_version > 0:
            logger.debug("Gatekeeper: TimeBased 策略触发")
            return True

        return False


# ====================================================================
# LedgerManager — 多世界隔离账本管理器
# ====================================================================


class LedgerManager:
    """进程级单例的多世界隔离账本管理器

    MemorizerWorker 和 WorldSummarizer 共享同一个实例。
    按 world_id 隔离桶，确保多世界并行时状态不交叉。
    """

    def __init__(self):
        self._buckets: dict[str, LedgerBucket] = {}
        self._lock = asyncio.Lock()

    # ── 账本操作 ──

    async def feed_signal(self, signal: dict):
        """将前台信号累加入对应世界的隔离桶

        signal 格式:
            {"type": "TurnRecordCommitted" | "BoundaryEncountered" | "WorldRollback",
             "world_id": str,
             "data": {...}}
        """
        signal_type = signal.get("type", "")
        world_id = signal.get("world_id", "")
        data = signal.get("data", {})
        if not world_id:
            return

        async with self._lock:
            bucket = self._buckets.setdefault(world_id, LedgerBucket())
            bucket.last_signal_at = time.time()

            if signal_type == "TurnRecordCommitted":
                tokens = data.get("turn_tokens", 0)
                msg_id = data.get("latest_msg_id", 0)
                bucket.unread_tokens += tokens
                if msg_id > bucket.target_version:
                    bucket.target_version = msg_id

            elif signal_type == "BoundaryEncountered":
                bucket.has_boundary = True

            elif signal_type == "WorldRollback":
                # 读档抹盘：清空桶计数，退回分界线
                rollback_version = data.get("rollback_version", 0)
                bucket.unread_tokens = 0
                bucket.has_boundary = False
                bucket.target_version = rollback_version
                logger.info(
                    f"LedgerManager: 世界 {world_id[:8]} 已抹盘归零 "
                    f"target_version={rollback_version}"
                )

    async def get_bucket(self, world_id: str) -> Optional[LedgerBucket]:
        """获取指定世界的账本桶副本（读操作用，不直接改）"""
        async with self._lock:
            orig = self._buckets.get(world_id)
            if orig is None:
                return None
            return LedgerBucket(
                unread_tokens=orig.unread_tokens,
                has_boundary=orig.has_boundary,
                target_version=orig.target_version,
                last_signal_at=orig.last_signal_at,
            )

    async def reset_bucket(self, world_id: str):
        """刷盘成功后重置桶计数"""
        async with self._lock:
            bucket = self._buckets.get(world_id)
            if bucket:
                bucket.unread_tokens = 0
                bucket.has_boundary = False

    async def remove_bucket(self, world_id: str):
        """世界结束或清理时移除桶"""
        async with self._lock:
            self._buckets.pop(world_id, None)

    @property
    def active_worlds(self) -> list[str]:
        """当前有账本的世界列表"""
        return list(self._buckets.keys())
