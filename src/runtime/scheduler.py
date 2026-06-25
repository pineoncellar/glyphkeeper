"""
@File     :   scheduler.py
@Desc     :   多玩家输入调度器 — 管理多个并发游戏会话
@Note     :   每个会话有独立的状态和锁，公平调度避免阻塞

使用方式:
    scheduler = InputScheduler(engine)
    narrative = await scheduler.submit("session-1", "我打开门")
"""

from __future__ import annotations

import asyncio
import time
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from src.state.game_state import GameState
from src.runtime.engine import GraphEngine
from src.runtime.context import ExecutionContext
from src.tools import get_logger

logger = get_logger(__name__)


# ====================================================================
# 内部数据类
# ====================================================================


@dataclass
class SessionSlot:
    """会话槽 — 管理单个会话的执行状态

    state:    当前会话的 GameState
    ctx:      执行上下文
    lock:     异步锁（防止同一会话的并发输入）
    queue:    等待队列（当会话忙时暂存输入）
    created:  会话创建时间
    last_active: 最后活动时间
    turn_count:  已执行的轮次
    """

    session_id: str
    state: GameState
    ctx: ExecutionContext
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    queue: asyncio.Queue = field(default_factory=asyncio.Queue)
    created: float = field(default_factory=time.time)
    last_active: float = field(default_factory=time.time)
    turn_count: int = 0


# ====================================================================
# 调度器
# ====================================================================


class InputScheduler:
    """多玩家输入调度器

    职责:
      - 管理多个并发游戏会话
      - 每个会话有独立的 GameState + ExecutionContext
      - 同一会话的输入串行执行（通过 asyncio.Lock）
      - 不同会话的输入并行执行
      - 提供会话生命周期管理（创建/查询/删除）
      - 自动清理过期会话

    使用方式:
        scheduler = InputScheduler(engine)
        narrative = await scheduler.submit("session-1", "我打开门")
    """

    def __init__(
        self,
        engine: GraphEngine,
        session_timeout: float = 3600.0,  # 1 小时无活动自动清理
        cleanup_interval: float = 300.0,  # 每 5 分钟检查过期会话
    ):
        """
        Args:
            engine:           Graph 执行引擎
            session_timeout:  会话超时秒数（超时后自动清理）
            cleanup_interval: 清理检查间隔秒数
        """
        self.engine = engine
        self.session_timeout = session_timeout
        self.cleanup_interval = cleanup_interval
        self._sessions: dict[str, SessionSlot] = OrderedDict()
        self._lock = asyncio.Lock()  # 保护 _sessions 的并发访问
        self._cleanup_task: Optional[asyncio.Task] = None

    # ── 核心操作 ──

    async def submit(
        self,
        session_id: str,
        player_input: str,
        auto_snapshot: bool = False,
    ) -> str:
        """提交玩家输入到指定会话

        如果会话不存在，自动创建新会话。
        同一会话的输入串行处理（等待前一个输入完成）。

        Args:
            session_id:    会话 ID
            player_input:  玩家输入文本
            auto_snapshot: 是否在处理后自动创建快照

        Returns:
            narrative: 叙事文本
        """
        slot = await self._get_or_create_session(session_id)

        async with slot.lock:
            slot.last_active = time.time()
            slot.turn_count += 1

            # 创建执行上下文
            ctx = ExecutionContext(session_id=session_id)

            logger.info(
                f"Scheduler.submit: session={session_id[:8]} "
                f"turn={slot.turn_count} "
                f"input={player_input[:40]}..."
            )

            narrative, new_state = await self.engine.run(
                player_input=player_input,
                session_id=session_id,
                previous_state=slot.state,
                context=ctx,
                auto_snapshot=auto_snapshot,
            )

            # 保存返回的新状态，保证多轮对话的 state 持续累积
            slot.state = new_state
            slot.state["player_input"] = player_input
            slot.ctx = ctx

            return narrative

    async def submit_with_queue(
        self,
        session_id: str,
        player_input: str,
    ) -> str:
        """带队列的提交 — 当会话忙时将输入排入队列

        与 submit() 不同，此方法不会等待前一个输入完成。
        而是将输入放入队列，由后台任务逐条处理。

        适用于 HTTP 接口（不需要实时等待回复的场景）。

        Args:
            session_id:    会话 ID
            player_input:  玩家输入文本

        Returns:
            队列接受确认信息
        """
        slot = await self._get_or_create_session(session_id)

        if slot.lock.locked():
            # 会话忙，排入队列
            await slot.queue.put(player_input)
            logger.debug(
                f"Scheduler.submit_with_queue: session={session_id[:8]} "
                f"已入队 (queue_size≈{slot.queue.qsize()})"
            )
            return f"（输入已排入队列，位置 #{slot.queue.qsize()}）"

        async with slot.lock:
            slot.last_active = time.time()
            slot.turn_count += 1

            ctx = ExecutionContext(session_id=session_id)
            narrative, new_state = await self.engine.run(
                player_input=player_input,
                session_id=session_id,
                previous_state=slot.state,
                context=ctx,
            )
            slot.state = new_state
            slot.state["player_input"] = player_input
            slot.ctx = ctx

        # 处理队列中的剩余输入
        asyncio.create_task(self._drain_queue(slot))

        return narrative

    async def _drain_queue(self, slot: SessionSlot):
        """处理会话队列中的累积输入"""
        while not slot.queue.empty():
            async with slot.lock:
                try:
                    player_input = await asyncio.wait_for(
                        slot.queue.get(), timeout=1.0
                    )
                except asyncio.TimeoutError:
                    break

                slot.last_active = time.time()
                slot.turn_count += 1

                ctx = ExecutionContext(session_id=slot.session_id)
                _, new_state = await self.engine.run(
                    player_input=player_input,
                    session_id=slot.session_id,
                    previous_state=slot.state,
                    context=ctx,
                )
                slot.state = new_state
                slot.state["player_input"] = player_input
                slot.ctx = ctx

            await asyncio.sleep(0)  # 让出事件循环

    # ── 会话管理 ──

    async def _get_or_create_session(self, session_id: str) -> SessionSlot:
        """获取现有会话，或创建新会话"""
        async with self._lock:
            if session_id in self._sessions:
                return self._sessions[session_id]

            # 创建新会话
            from src.state.game_state import create_initial_state

            state = create_initial_state(session_id=session_id)
            slot = SessionSlot(
                session_id=session_id,
                state=state,
                ctx=ExecutionContext(session_id=session_id),
            )
            self._sessions[session_id] = slot
            logger.info(
                f"Scheduler: 创建新会话 session={session_id[:8]} "
                f"total_sessions={len(self._sessions)}"
            )
            return slot

    def get_session(self, session_id: str) -> Optional[SessionSlot]:
        """查询会话信息"""
        return self._sessions.get(session_id)

    def get_session_state(self, session_id: str) -> Optional[GameState]:
        """获取会话的 GameState"""
        slot = self._sessions.get(session_id)
        return slot.state if slot else None

    async def remove_session(self, session_id: str) -> bool:
        """删除一个会话及其状态"""
        async with self._lock:
            if session_id in self._sessions:
                del self._sessions[session_id]
                logger.info(
                    f"Scheduler: 删除会话 session={session_id[:8]} "
                    f"remaining={len(self._sessions)}"
                )
                return True
            return False

    async def clear_all_sessions(self):
        """清空所有会话"""
        async with self._lock:
            count = len(self._sessions)
            self._sessions.clear()
            logger.info(f"Scheduler: 清空所有会话 ({count} 个)")

    # ── 统计 ──

    @property
    def active_session_count(self) -> int:
        """当前活跃会话数"""
        return len(self._sessions)

    @property
    def session_ids(self) -> list[str]:
        """所有会话 ID 列表"""
        return list(self._sessions.keys())

    def get_stats(self) -> dict:
        """获取调度器统计信息"""
        now = time.time()
        total_turns = sum(s.turn_count for s in self._sessions.values())
        active_sessions = sum(
            1 for s in self._sessions.values()
            if (now - s.last_active) < self.session_timeout
        )

        return {
            "total_sessions": len(self._sessions),
            "active_sessions": active_sessions,
            "total_turns": total_turns,
            "session_timeout": self.session_timeout,
            "cleanup_interval": self.cleanup_interval,
            "engine_mode": getattr(self.engine, "mode", "unknown"),
        }

    # ── 自动清理 ──

    async def start_cleanup_task(self):
        """启动后台会话清理任务"""
        if self._cleanup_task is not None:
            logger.warning("Scheduler: 清理任务已在运行")
            return

        self._cleanup_task = asyncio.create_task(self._cleanup_loop())
        logger.info(
            f"Scheduler: 清理任务已启动 "
            f"(timeout={self.session_timeout}s, interval={self.cleanup_interval}s)"
        )

    async def stop_cleanup_task(self):
        """停止后台清理任务"""
        if self._cleanup_task:
            self._cleanup_task.cancel()
            try:
                await self._cleanup_task
            except asyncio.CancelledError:
                pass
            self._cleanup_task = None
            logger.info("Scheduler: 清理任务已停止")

    async def _cleanup_loop(self):
        """后台清理循环"""
        try:
            while True:
                await asyncio.sleep(self.cleanup_interval)
                await self._cleanup_expired_sessions()
        except asyncio.CancelledError:
            pass

    async def _cleanup_expired_sessions(self):
        """清理过期会话"""
        now = time.time()
        expired = [
            sid
            for sid, slot in self._sessions.items()
            if (now - slot.last_active) > self.session_timeout
        ]

        if not expired:
            return

        async with self._lock:
            for sid in expired:
                if sid in self._sessions:
                    del self._sessions[sid]

        logger.info(
            f"Scheduler: 清理了 {len(expired)} 个过期会话 "
            f"remaining={len(self._sessions)}"
        )

    # ── 生命周期 ──

    async def close(self):
        """关闭调度器，释放所有资源"""
        await self.stop_cleanup_task()
        await self.clear_all_sessions()
        await self.engine.close()
        logger.info("Scheduler: 已关闭")
