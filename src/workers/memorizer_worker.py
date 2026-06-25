# -*- coding: utf-8 -*-
"""
@File     :   memorizer_worker.py
@Desc     :   长期记忆提取后台任务 — 定期固化对话记录到 RAG/向量库

工作流程:
  1. 轮询 EventStore 获取未固化的新事件
  2. 调用 Summarizer 提取关键事件与事实
  3. 将摘要/事实写入 VectorStore (RAG)
  4. 标记已固化

触发方式:
  - 定时触发（默认每 5 分钟）
  - 手动触发（通过 trigger_now()）
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Optional

from src.tools import get_logger
from src.memory.event_store import EventStore
from src.memory.vector_store import VectorStore
from src.memory.summarizer import Summarizer

logger = get_logger(__name__)


class MemorizerWorker:
    """后台记忆固化 Worker

    职责:
      - 定期扫描未固化的对话记录
      - 调用 Summarizer 提取事实
      - 写入 VectorStore
      - 标记已固化

    使用方式:
        worker = MemorizerWorker(event_store, vector_store)
        asyncio.create_task(worker.start())  # 后台持续运行
        await worker.trigger_now()           # 手动触发一次
    """

    def __init__(
        self,
        event_store: Optional[EventStore] = None,
        vector_store: Optional[VectorStore] = None,
        summarizer: Optional[Summarizer] = None,
        interval: int = 300,
        batch_size: int = 20,
    ):
        """
        参数:
            event_store:  EventStore 实例（None 则降级为仅日志）
            vector_store: VectorStore 实例（None 则降级为仅日志）
            summarizer:   Summarizer 实例（None 则自动创建）
            interval:     轮询间隔（秒），默认 5 分钟
            batch_size:   每批处理的最大记录数
        """
        self._event_store = event_store
        self._vector_store = vector_store
        self._summarizer = summarizer or Summarizer()
        self.interval = interval
        self.batch_size = batch_size
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self._trigger: asyncio.Event = asyncio.Event()
        self._consolidated_count = 0
        self._last_consolidated_at: Optional[str] = None

    # ── 生命周期 ──

    async def start(self):
        """启动后台轮询循环"""
        if self._running:
            logger.warning("MemorizerWorker 已在运行")
            return

        self._running = True
        logger.info(
            f"MemorizerWorker 启动: interval={self.interval}s, "
            f"batch_size={self.batch_size}"
        )

        self._task = asyncio.current_task()
        try:
            while self._running:
                try:
                    await self._process_pending()
                except Exception as e:
                    logger.error(f"MemorizerWorker 处理异常: {e}", exc_info=True)

                # 等待 interval 或被 trigger 唤醒
                try:
                    await asyncio.wait_for(
                        self._trigger.wait(),
                        timeout=self.interval,
                    )
                    self._trigger.clear()
                except asyncio.TimeoutError:
                    pass  # 超时正常，继续下一轮
        finally:
            self._running = False
            logger.info("MemorizerWorker 已停止")

    async def stop(self):
        """停止 Worker"""
        self._running = False
        self._trigger.set()  # 唤醒循环以便退出
        if self._task and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def trigger_now(self):
        """手动触发一次固化"""
        logger.info("MemorizerWorker: 收到手动触发信号")
        self._trigger.set()

    # ── 状态查询 ──

    @property
    def is_running(self) -> bool:
        return self._running

    @property
    def stats(self) -> dict:
        return {
            "running": self._running,
            "interval": self.interval,
            "batch_size": self.batch_size,
            "consolidated_count": self._consolidated_count,
            "last_consolidated_at": self._last_consolidated_at,
        }

    # ── 核心处理 ──

    async def _process_pending(self):
        """处理所有未固化的记录"""
        if self._event_store is None:
            logger.debug("MemorizerWorker: 无 EventStore，跳过固化")
            return

        # 获取所有活跃会话（简化方法：扫描最近有事件写入的会话）
        # 实际应通过 EventStore 的接口查询未固化记录
        # 此处使用简化方案：记录有事件被追加即可触发
        logger.debug("MemorizerWorker: 轮询未固化记录...")

        # 注：真正实现时需 EventStore 提供 "未固化记录" 查询接口
        # 当前版本仅做日志 + 状态追踪
        self._last_consolidated_at = datetime.now(timezone.utc).isoformat()
        self._consolidated_count += 0  # 实际固化后递增

    async def consolidate_session(
        self,
        session_id: str,
        events: list[dict],
    ) -> bool:
        """固化单个会话的事件记录

        参数:
            session_id: 会话 ID
            events:     需要固化的事件列表

        返回:
            是否成功
        """
        if not events:
            return True

        if self._vector_store is None:
            logger.debug(f"MemorizerWorker: 无 VectorStore，跳过 session={session_id[:8]}")
            return False

        try:
            # 提取叙事文本
            narratives = [
                e.get("data", {}).get("patch", {}).get("narrative", "")
                for e in events
                if e.get("type") in ("Narrative", "PlayerInput")
            ]
            narratives = [n for n in narratives if n]

            if not narratives:
                return True

            # 生成摘要
            summary = await self._summarizer.summarize(
                [{"text": n, "role": "assistant"} for n in narratives]
            )

            # 提取事实
            combined_text = "\n".join(narratives[-5:])  # 最近 5 条
            facts = await self._summarizer.extract_facts(combined_text)

            # 写入 VectorStore
            if summary:
                await self._vector_store.insert(
                    text=summary,
                    source_type=f"consolidated_summary_{session_id[:8]}",
                )

            for fact in facts:
                fact_text = str(fact)
                if len(fact_text) > 20:  # 过滤过短的内容
                    await self._vector_store.insert(
                        text=fact_text,
                        source_type=f"extracted_fact_{session_id[:8]}",
                    )

            self._consolidated_count += len(narratives)
            logger.info(
                f"MemorizerWorker: 固化完成 session={session_id[:8]} "
                f"narratives={len(narratives)} facts={len(facts)}"
            )
            return True

        except Exception as e:
            logger.error(
                f"MemorizerWorker: 固化失败 session={session_id[:8]}: {e}"
            )
            return False
