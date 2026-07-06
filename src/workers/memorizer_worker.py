# -*- coding: utf-8 -*-
"""
@File     :   memorizer_worker.py
@Desc     :   长期记忆提取后台任务 — 增量轮询固化模式
@Note     :   前台通过 EventLog.emit_signal 投递轻量信号，Worker 记账不持文本；
              判定引擎评估达标后，从 EventStore 增量捞取原始发言，提炼后刷入 LightRAG。
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timezone
from typing import Optional

from src.tools import get_logger
from src.memory.event_store import EventStore
from src.memory.vector_store import VectorStore
from src.memory.summarizer import Summarizer
from src.state.read_models import StaticReadStore
from src.workers._ledger import (
    LedgerManager,
    Gatekeeper,
    get_world_lock,
)

logger = get_logger(__name__)


class MemorizerWorker:
    """后台记忆固化 Worker — 增量轮询固化模式

    生命周期:
        start()  -> 守护循环等待 asyncio.Event 唤醒
                    -> 对每个有账本的世界运行判定引擎
                    -> 达标则调用 _flush_world() 增量固化

    核心方法 _flush_world():
        先获取世界级互斥锁 -> 读持久化分界线
        -> 从 EventStore 增量捞取事件 -> Summarizer 提炼
        -> 刷入 VectorStore -> 推进分界线 -> 重置内存桶

    使用方式:
        worker = MemorizerWorker(event_store, vector_store, ledger, gatekeeper)
        asyncio.create_task(worker.start())
    """

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        summarizer: Optional[Summarizer] = None,
        ledger: Optional[LedgerManager] = None,
        gatekeeper: Optional[Gatekeeper] = None,
        interval: int = 60,
    ):
        """
        参数:
            event_store:  EventStore 实例（None 则降级为仅日志）
            vector_store: VectorStore 实例（None 则降级为仅日志）
            summarizer:   Summarizer 实例（None 则自动创建）
            ledger:       LedgerManager 共享实例（None 则自动创建）
            gatekeeper:   Gatekeeper 判定引擎实例（None 则自动创建）
            interval:     守护循环轮询间隔（秒）
        """
        self._event_store = event_store
        self._vector_store = vector_store
        self._summarizer = summarizer or Summarizer()
        self._ledger = ledger or LedgerManager()
        self._gatekeeper = gatekeeper or Gatekeeper()
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._trigger: asyncio.Event = asyncio.Event()
        self._flush_count = 0
        self._last_flush_at: Optional[float] = None
        self._checkpoint_store: Optional[StaticReadStore] = None

    # ---- 生命周期 ----

    async def start(self):
        """启动守护循环"""
        if self._running:
            logger.warning("MemorizerWorker 已在运行")
            return

        self._running = True
        self._checkpoint_store = StaticReadStore()
        logger.info(
            f"MemorizerWorker 启动: interval={self.interval}s, "
            f"token_threshold={self._gatekeeper.token_threshold}"
        )

        self._task = asyncio.current_task()
        try:
            while self._running:
                try:
                    await asyncio.wait_for(
                        self._trigger.wait(),
                        timeout=self.interval,
                    )
                    self._trigger.clear()
                except asyncio.TimeoutError:
                    pass

                try:
                    await self._evaluate_and_flush()
                except Exception as e:
                    logger.error(f"MemorizerWorker 处理异常: {e}", exc_info=True)
        finally:
            self._running = False
            logger.info("MemorizerWorker 已停止")

    async def stop(self):
        """停止 Worker"""
        self._running = False
        self._trigger.set()
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def trigger_now(self):
        """手动触发一次评估"""
        logger.info("MemorizerWorker: 收到手动触发信号")
        self._trigger.set()

    # ---- 状态查询 ----

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "interval": self.interval,
            "flush_count": self._flush_count,
            "last_flush_at": datetime.fromtimestamp(self._last_flush_at).isoformat()
                if self._last_flush_at else None,
            "active_worlds": self._ledger.active_worlds,
        }

    # ---- 核心评估循环 ----

    async def _evaluate_and_flush(self):
        """遍历所有有账本的世界，评估是否需要刷盘"""
        if self._event_store is None:
            return

        now = time.time()
        worlds = self._ledger.active_worlds

        for world_id in worlds:
            bucket = await self._ledger.get_bucket(world_id)
            if bucket is None:
                continue

            should = await self._gatekeeper.should_flush(
                bucket, now, self._last_flush_at,
            )
            if should:
                await self._flush_world(world_id)

    # ---- 增量收网刷盘流水线 ----

    async def _flush_world(self, world_id: str):
        """单世界增量收网：捞取 -> 提炼 -> 刷入 -> 推进分界线"""
        lock = await get_world_lock(world_id)
        async with lock:
            try:
                # 读取已固化分界线
                checkpoint = await self._checkpoint_store.get_checkpoint(world_id)
                bucket = await self._ledger.get_bucket(world_id)
                if bucket is None:
                    return

                target = bucket.target_version
                if target <= checkpoint:
                    # 没有新数据，直接重置桶
                    await self._ledger.reset_bucket(world_id)
                    return

                # 增量捞取原始发言
                events = await self._event_store.get_events(
                    world_id, since_version=checkpoint,
                )
                if not events:
                    await self._ledger.reset_bucket(world_id)
                    return

                # 过滤出叙事和玩家输入
                narratives = []
                for e in events:
                    etype = e.get("type", "")
                    edata = e.get("data", {})
                    if etype == "NarrativeOutput":
                        patch = edata.get("patch", {})
                        text = patch.get("narrative", "")
                        if text:
                            narratives.append({"text": text, "role": "assistant"})
                    elif etype == "PlayerInput":
                        text = edata.get("text", "")
                        if text:
                            narratives.append({"text": text, "role": "user"})

                if not narratives:
                    await self._checkpoint_store.update_checkpoint(
                        world_id, target,
                    )
                    await self._ledger.reset_bucket(world_id)
                    self._last_flush_at = time.time()
                    self._flush_count += 1
                    return

                # 提炼并刷入 LightRAG
                if self._vector_store:
                    await self._refine_and_store(
                        world_id, narratives,
                    )

                # 推进持久化分界线
                await self._checkpoint_store.update_checkpoint(world_id, target)
                await self._ledger.reset_bucket(world_id)
                self._last_flush_at = time.time()
                self._flush_count += 1

                logger.info(
                    f"MemorizerWorker: 刷盘完成 world={world_id[:8]} "
                    f"version={checkpoint}->{target} "
                    f"narratives={len(narratives)}"
                )

            except Exception as e:
                logger.error(
                    f"MemorizerWorker: 刷盘失败 world={world_id[:8]}: {e}",
                    exc_info=True,
                )

    # ---- 提炼与存储 ----

    async def _refine_and_store(
        self,
        world_id: str,
        narratives: list[dict],
    ):
        """将原始发言提炼为摘要和事实，刷入 VectorStore"""
        try:
            summary = await self._summarizer.summarize(narratives)

            combined_text = "\n".join(
                n["text"] for n in narratives[-5:]
            )
            facts = await self._summarizer.extract_facts(combined_text)

            if summary:
                await self._vector_store.insert(
                    text=summary,
                    source_type=f"memorizer_summary_{world_id[:8]}",
                )

            for fact in facts:
                fact_text = str(fact)
                if len(fact_text) > 20:
                    await self._vector_store.insert(
                        text=fact_text,
                        source_type=f"memorizer_fact_{world_id[:8]}",
                    )

            logger.debug(
                f"MemorizerWorker: 提炼完成 world={world_id[:8]} "
                f"summary={len(summary)}chars facts={len(facts)}"
            )

        except Exception as e:
            logger.warning(
                f"MemorizerWorker: 提炼/刷入失败 world={world_id[:8]}: {e}"
            )
