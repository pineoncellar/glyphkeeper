# -*- coding: utf-8 -*-
"""
@File     :   world_summarizer.py
@Desc     :   世界状态压缩后台任务 — 增量轮询固化模式
@Note     :   与 MemorizerWorker 共享同一份 LedgerManager 账本和判定引擎，
              但固化频率更低（更高 Token 阈值），产出为宏观编年史摘要而非微观事实。
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


class WorldSummarizerGatekeeper(Gatekeeper):
    """WorldSummarizer 专用的判定引擎 — 更高阈值、更长间隔

    世界摘要的产出频率应当低于微观记忆固化 —— 编年史不应急于每轮都写。
    """

    def __init__(
        self,
        token_threshold: int = 4000,
        min_flush_interval: int = 600,
        time_idle_threshold: int = 600,
    ):
        super().__init__(
            token_threshold=token_threshold,
            time_idle_threshold=time_idle_threshold,
            min_flush_interval=min_flush_interval,
        )


class WorldSummarizer:
    """世界状态摘要 Worker — 增量轮询固化模式

    与 MemorizerWorker 共享同一份 LedgerManager，但使用更高判定阈值。
    产出为第三人称宏观编年史，刷入 VectorStore 供 Narrator 检索世界大局。

    使用方式:
        summarizer = WorldSummarizer(event_store, vector_store, ledger)
        asyncio.create_task(summarizer.start())
    """

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        summarizer: Optional[Summarizer] = None,
        ledger: Optional[LedgerManager] = None,
        gatekeeper: Optional[WorldSummarizerGatekeeper] = None,
        interval: int = 60,
    ):
        self._event_store = event_store
        self._vector_store = vector_store
        self._summarizer = summarizer or Summarizer()
        self._ledger = ledger or LedgerManager()
        self._gatekeeper = gatekeeper or WorldSummarizerGatekeeper()
        self.interval = interval
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._flush_count = 0
        self._last_flush_at: Optional[float] = None
        self._checkpoint_store: Optional[StaticReadStore] = None

    # ---- 生命周期 ----

    async def start(self):
        """启动守护循环"""
        if self._running:
            logger.warning("WorldSummarizer 已在运行")
            return

        self._running = True
        self._checkpoint_store = StaticReadStore()
        logger.info(
            f"WorldSummarizer 启动: interval={self.interval}s, "
            f"token_threshold={self._gatekeeper.token_threshold}"
        )

        self._task = asyncio.current_task()
        try:
            while self._running:
                await asyncio.sleep(self.interval)
                try:
                    await self._evaluate_and_flush()
                except Exception as e:
                    logger.error(f"WorldSummarizer 异常: {e}", exc_info=True)
        finally:
            self._running = False
            logger.info("WorldSummarizer 已停止")

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
            "interval": self.interval,
            "flush_count": self._flush_count,
            "last_flush_at": datetime.fromtimestamp(self._last_flush_at).isoformat()
                if self._last_flush_at else None,
            "active_worlds": self._ledger.active_worlds,
        }

    # ---- 核心评估 ----

    async def _evaluate_and_flush(self):
        """遍历活跃世界，评估是否需要生成编年史摘要"""
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

    # ---- 增量收网 ----

    async def _flush_world(self, world_id: str):
        """单世界增量收网：捞取事件 -> 生成编年史 -> 刷入 VectorStore -> 推进分界线"""
        lock = await get_world_lock(world_id)
        async with lock:
            try:
                checkpoint = await self._checkpoint_store.get_checkpoint(world_id)
                bucket = await self._ledger.get_bucket(world_id)
                if bucket is None:
                    return

                target = bucket.target_version
                if target <= checkpoint:
                    return

                events = await self._event_store.get_events(
                    world_id, since_version=checkpoint,
                )
                if not events:
                    return

                summary = await self._build_world_summary(world_id, events)

                if summary and self._vector_store:
                    await self._vector_store.insert(
                        text=summary,
                        source_type=f"world_summary_{world_id[:8]}",
                    )

                self._last_flush_at = time.time()
                self._flush_count += 1

                logger.info(
                    f"WorldSummarizer: 编年史完成 world={world_id[:8]} "
                    f"version={checkpoint}->{target} "
                    f"events={len(events)}"
                )

            except Exception as e:
                logger.error(
                    f"WorldSummarizer: 刷新失败 world={world_id[:8]}: {e}",
                    exc_info=True,
                )

    # ---- 编年史生成 ----

    async def _build_world_summary(
        self,
        world_id: str,
        events: list[dict],
    ) -> Optional[str]:
        """从事件列表生成世界状态编年史

        产出为纯文本，包含阶段分布、NPC 交互统计、近期的叙事片段。
        不调用 LLM，纯结构化组装以控制成本。
        """
        relevant = []
        for e in events:
            etype = e.get("type", "")
            edata = e.get("data", {})
            patch = edata.get("patch", {})

            narrative = ""
            if etype == "NarrativeOutput":
                narrative = patch.get("narrative", "")[:200]

            relevant.append({
                "type": etype,
                "source": e.get("source_node", ""),
                "narrative": narrative,
            })

        if not relevant:
            return None

        lines = [f"世界 {world_id[:8]} 编年史"]
        lines.append(f"生成时间: {datetime.now(timezone.utc).isoformat()}")
        lines.append(f"事件跨度: {len(events)} 条")
        lines.append("")

        # 统计各事件类型分布
        type_counts: dict[str, int] = {}
        for r in relevant:
            t = r["type"] or "Unknown"
            type_counts[t] = type_counts.get(t, 0) + 1
        lines.append("事件类型分布:")
        for t, cnt in sorted(type_counts.items(), key=lambda x: -x[1]):
            bar = chr(0x258C) * min(cnt, 20)
            lines.append(f"  {t:>25}: {bar} ({cnt})")

        # 最近的叙事片段
        narratives = [r["narrative"] for r in relevant[-5:] if r["narrative"]]
        if narratives:
            lines.append("")
            lines.append("近期叙事片段:")
            for i, n in enumerate(narratives, 1):
                preview = n[:150].replace("\n", " ")
                if len(n) > 150:
                    preview += "..."
                lines.append(f"  [{i}] {preview}")

        # NPC 交互统计
        npc_counts: dict[str, int] = {}
        for e in events:
            if e.get("type") == "NPCDialogue":
                ed = e.get("data", {})
                nname = ed.get("npc_name", "unknown")
                npc_counts[nname] = npc_counts.get(nname, 0) + 1
        if npc_counts:
            lines.append("")
            lines.append("NPC 交互统计:")
            for nname, cnt in sorted(npc_counts.items(), key=lambda x: -x[1]):
                bar = chr(0x2588) * min(cnt, 15)
                lines.append(f"  {nname:>12}: {bar} ({cnt})")

        return "\n".join(lines)
