# -*- coding: utf-8 -*-
"""
@File     :   base.py
@Desc     :   抽象适配器基类 — 所有 adapter 必须实现的接口
@Note     :   CLI / WebSocket / HTTP 都继承此类

核心流程:
    External Input
        ↓   parse()
    InboundMessage
        ↓   handle()
    OutboundMessage
        ↓   send()
    External Output
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Optional

from src.tools import get_logger
from src.runtime.engine import GraphEngine, ENGINE_MODE_LANGGRAPH
from src.runtime.scheduler import InputScheduler
from src.graph.keeper_graph import keeper_graph
from src.adapter.protocol import InboundMessage, OutboundMessage, MessageType

logger = get_logger(__name__)


class AbstractAdapter(ABC):
    """统一接入层基类

    子类需实现:
      - parse()      外部输入 → InboundMessage
      - send()       OutboundMessage → 外部输出
      - run_impl()   启动适配器主循环

    子类可直接使用:
      - handle()     InboundMessage → OutboundMessage（核心路由逻辑）
      - engine / scheduler
    """

    def __init__(
        self,
        engine: Optional[GraphEngine] = None,
        scheduler: Optional[InputScheduler] = None,
        session_id: str = "default",
    ):
        self.session_id = session_id
        self._engine = engine
        self._scheduler = scheduler
        self._running = False

    # ── 子类需实现的接口 ──

    @abstractmethod
    async def parse(self, raw_input: Any) -> InboundMessage:
        """将原始外部输入解析为统一 InboundMessage"""
        ...

    @abstractmethod
    async def send(self, message: OutboundMessage):
        """将统一 OutboundMessage 发送到外部"""
        ...

    @abstractmethod
    async def run_impl(self):
        """启动适配器的主循环/服务"""
        ...

    # ── 生命周期 ──

    async def run(self):
        """启动适配器（模板方法）"""
        self._ensure_engine()

        try:
            await self.run_impl()
        finally:
            await self._cleanup()

    async def stop(self):
        """停止适配器"""
        self._running = False
        logger.info(f"{type(self).__name__}: 已停止")

    @property
    def is_running(self) -> bool:
        return self._running

    # ── 核心处理 ──

    async def handle(self, msg: InboundMessage) -> OutboundMessage:
        """统一消息处理入口：InboundMessage → OutboundMessage

        路由规则:
          SYSTEM_CMD   → 系统命令处理
          DICE_RESULT  → 掷骰结果提交
          PLAYER_INPUT → 引擎执行
        """
        self._ensure_engine()

        # 确保 session_id 传递
        session_id = msg.session_id or self.session_id

        if msg.type == MessageType.SYSTEM_CMD:
            return await self._handle_system_cmd(msg.text, session_id)

        if msg.type == MessageType.DICE_RESULT:
            return await self._handle_dice_result(msg, session_id)

        # PLAYER_INPUT 或未知类型 → 送入引擎
        return await self._handle_player_input(msg.text, session_id)

    # ── 内部路由 ──

    async def _handle_system_cmd(self, cmd: str, session_id: str) -> OutboundMessage:
        """处理系统命令"""
        cmd = cmd.lower().strip()

        if cmd == "/status":
            state = await self._scheduler.get_session_state(session_id)
            if state is None:
                return OutboundMessage.system_msg("当前无活跃会话", level="warn", session_id=session_id)
            return OutboundMessage.session_info(
                {
                    "session_id": session_id,
                    "game_phase": state.get("game_phase", "unknown"),
                    "combat_active": state.get("combat_active", False),
                    "active_tags": state.get("active_tags", []),
                    "pending_dice": state.get("pending_dice"),
                },
                session_id=session_id,
            )

        if cmd == "/reset":
            await self._scheduler.remove_session(session_id)
            # 重建会话
            await self._scheduler.submit(session_id, "")
            return OutboundMessage.system_msg("✅ 会话已重置", session_id=session_id)

        if cmd in ("/help", "/h"):
            return OutboundMessage.system_msg(
                "可用命令: /help, /status, /reset, /quit\n"
                "直接输入文本开始游戏。",
                session_id=session_id,
            )

        return OutboundMessage.system_msg(f"未知命令: {cmd}", level="warn", session_id=session_id)

    async def _handle_dice_result(self, msg: InboundMessage, session_id: str) -> OutboundMessage:
        """处理掷骰结果"""
        value = msg.data.get("value")
        if value is None:
            try:
                value = int(msg.text)
            except (ValueError, TypeError):
                return OutboundMessage.error("无效的掷骰值", session_id=session_id)

        # 注入 pending_dice 到 state
        state = await self._scheduler.get_session_state(session_id)
        if state and state.get("pending_dice"):
            state["pending_dice"]["roll_value"] = value
        return OutboundMessage.system_msg(f"掷骰结果: {value}", session_id=session_id)

    async def _handle_player_input(self, text: str, session_id: str) -> OutboundMessage:
        """处理玩家输入 → 引擎执行"""
        if not text.strip():
            return OutboundMessage.system_msg("输入不能为空", level="warn", session_id=session_id)

        try:
            narrative = await self._scheduler.submit(session_id, text)
            state = await self._scheduler.get_session_state(session_id)
            game_phase = state.get("game_phase", "") if state else ""

            if narrative and narrative != "（系统异常：...）":
                return OutboundMessage.narrative(
                    text=narrative,
                    session_id=session_id,
                    game_phase=game_phase,
                )
            return OutboundMessage.system_msg(
                "系统未返回叙事文本（可能缺少 LLM 配置）",
                level="warn",
                session_id=session_id,
            )

        except Exception as e:
            logger.error(f"引擎执行异常: {e}", exc_info=True)
            return OutboundMessage.error(f"执行失败: {type(e).__name__}: {e}", session_id=session_id)

    # ── 工具方法 ──

    def _ensure_engine(self):
        """确保 engine 和 scheduler 已初始化（懒加载）"""
        if self._engine is None:
            self._engine = GraphEngine(keeper_graph, mode=ENGINE_MODE_LANGGRAPH)
        if self._scheduler is None:
            self._scheduler = InputScheduler(self._engine)

    async def _cleanup(self):
        """清理资源"""
        try:
            if self._scheduler:
                await self._scheduler.close()
            if self._engine:
                await self._engine.close()
        except Exception as e:
            logger.warning(f"清理异常: {e}")
