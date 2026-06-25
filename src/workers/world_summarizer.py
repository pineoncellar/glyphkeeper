# -*- coding: utf-8 -*-
"""
@File     :   world_summarizer.py
@Desc     :   世界状态压缩后台任务 — 周期生成世界简报与 NPC 关系摘要

工作流程:
  1. 读取最新 Event Log
  2. 分析状态变更模式（NPC 移动、对话、战斗）
  3. 生成结构化/自然语言摘要
  4. 写入 VectorStore 供 Narrator 检索

输出:
  - world_summary.txt: 世界状态简报
  - npc_relations.json: NPC 关系变更（可选）
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Optional

from src.tools import get_logger
from src.memory.event_store import EventStore
from src.memory.vector_store import VectorStore
from src.memory.summarizer import Summarizer

logger = get_logger(__name__)


class WorldSummarizer:
    """世界状态压缩后台任务

    职责:
      - 定期读取 Event Log，分析世界状态变更
      - 生成世界状态简报（world summary）
      - 检测 NPC 行为模式与关系变化
      - 写入 VectorStore 供后续检索

    使用方式:
        summarizer = WorldSummarizer(event_store, vector_store)
        asyncio.create_task(summarizer.start())
    """

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        summarizer: Optional[Summarizer] = None,
        interval: int = 600,
        summary_window: int = 50,
    ):
        """
        参数:
            event_store:    EventStore 实例（None 则降级为仅日志）
            vector_store:   VectorStore 实例（None 则降级为仅日志）
            summarizer:     Summarizer 实例（None 则自动创建）
            interval:       轮询间隔（秒），默认 10 分钟
            summary_window: 每次摘要分析的事件窗口大小
        """
        self._event_store = event_store
        self._vector_store = vector_store
        self._summarizer = summarizer or Summarizer()
        self.interval = interval
        self.summary_window = summary_window
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._last_version: dict[str, int] = {}  # session_id → last_version
        self._summary_count = 0
        self._last_summary_at: Optional[str] = None

    # ── 生命周期 ──

    async def start(self):
        """启动后台轮询循环"""
        if self._running:
            logger.warning("WorldSummarizer 已在运行")
            return

        self._running = True
        logger.info(
            f"WorldSummarizer 启动: interval={self.interval}s, "
            f"window={self.summary_window}"
        )

        try:
            while self._running:
                try:
                    await self._generate_summaries()
                except Exception as e:
                    logger.error(f"WorldSummarizer 异常: {e}", exc_info=True)

                await asyncio.sleep(self.interval)
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
            "summary_window": self.summary_window,
            "summary_count": self._summary_count,
            "last_summary_at": self._last_summary_at,
        }

    # ── 核心处理 ──

    async def _generate_summaries(self):
        """遍历活跃会话，生成世界摘要"""
        if self._event_store is None:
            logger.debug("WorldSummarizer: 无 EventStore，跳过")
            return

        logger.debug("WorldSummarizer: 开始生成世界摘要...")
        self._last_summary_at = datetime.now(timezone.utc).isoformat()
        self._summary_count += 0

    async def summarize_session(self, session_id: str, events: list[dict]) -> Optional[str]:
        """为单个会话生成世界状态摘要

        参数:
            session_id: 会话 ID
            events:     事件列表

        返回:
            摘要文本，或 None（无需摘要）
        """
        if not events:
            return None

        try:
            # 提取关键事件用于摘要
            relevant = []
            for e in events:
                etype = e.get("type", "")
                edata = e.get("data", {})
                patch = edata.get("patch", {})

                entry = {
                    "type": etype,
                    "source": e.get("source_node", ""),
                    "phase": patch.get("game_phase", ""),
                    "narrative": patch.get("narrative", "")[:200],
                }
                relevant.append(entry)

            if not relevant:
                return None

            # 拼接为摘要文本
            lines = [f"【会话 {session_id[:8]}】世界状态摘要"]
            lines.append(f"生成时间: {datetime.now(timezone.utc).isoformat()}")
            lines.append(f"事件总数: {len(events)}")
            lines.append("")

            # 统计各阶段分布
            phases = {}
            for r in relevant:
                p = r["phase"] or "unknown"
                phases[p] = phases.get(p, 0) + 1

            lines.append("游戏阶段分布:")
            for phase, count in sorted(phases.items()):
                bar = "▓" * min(count, 20)
                lines.append(f"  {phase:>15}: {bar} ({count})")

            # 最近的叙事片段
            narratives = [r["narrative"] for r in relevant[-5:] if r["narrative"]]
            if narratives:
                lines.append("")
                lines.append("最近叙事片段:")
                for i, n in enumerate(narratives, 1):
                    lines.append(f"  [{i}] {n[:150]}...")

            summary = "\n".join(lines)

            # 写入 VectorStore
            if self._vector_store:
                await self._vector_store.insert(
                    text=summary,
                    source_type=f"world_summary_{session_id[:8]}",
                )

            self._summary_count += 1
            logger.info(
                f"WorldSummarizer: 摘要已生成 session={session_id[:8]} "
                f"events={len(events)}"
            )
            return summary

        except Exception as e:
            logger.error(
                f"WorldSummarizer: 摘要失败 session={session_id[:8]}: {e}"
            )
            return None
