# -*- coding: utf-8 -*-
"""
@File     :   protocol.py
@Desc     :   统一消息协议 — 所有 adapter 共用的输入/输出消息格式
@Note     :   CLI / WebSocket / HTTP 均使用此协议

消息流:
  External Input  ──→  InboundMessage  ──→  Adapter.handle()  ──→  OutboundMessage  ──→  External Output

消息类型 (MessageType):
  ── 入站 ──
  PLAYER_INPUT   玩家输入的文本动作
  DICE_RESULT    玩家返回的掷骰结果
  SYSTEM_CMD     系统命令 (/help /reset /status 等)

  ── 出站 ──
  NARRATIVE      叙事文本
  DICE_REQUEST   请求玩家掷骰
  SYSTEM_MSG     系统消息（通知 / 错误 / 状态）
  SESSION_INFO   会话状态信息
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


# ====================================================================
# 消息类型枚举
# ====================================================================


class MessageType:
    """统一消息类型常量"""

    # ── 入站 ──
    PLAYER_INPUT = "player_input"       # 玩家输入文本
    DICE_RESULT = "dice_result"         # 玩家掷骰结果
    SYSTEM_CMD = "system_cmd"           # 系统命令

    # ── 出站 ──
    NARRATIVE = "narrative"             # 叙事文本
    DICE_REQUEST = "dice_request"       # 请求掷骰
    SYSTEM_MSG = "system_message"       # 系统消息
    SESSION_INFO = "session_info"       # 会话状态


# ====================================================================
# 入站消息
# ====================================================================


@dataclass
class InboundMessage:
    """统一入站消息 — 来自外部（玩家/客户端）的消息

    字段:
        type:       消息类型 (MessageType 常量)
        text:       消息文本正文
        session_id: 会话 ID（可选，由 adapter 自动填充）
        data:       附加结构化数据
        raw:        原始消息（协议无关的原始数据）
    """
    type: str
    text: str = ""
    session_id: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    raw: Any = None

    # ── 便捷工厂 ──

    @classmethod
    def player_input(cls, text: str, session_id: str = "") -> "InboundMessage":
        """创建玩家输入消息"""
        return cls(type=MessageType.PLAYER_INPUT, text=text, session_id=session_id)

    @classmethod
    def dice_result(cls, value: int, session_id: str = "") -> "InboundMessage":
        """创建掷骰结果消息"""
        return cls(
            type=MessageType.DICE_RESULT,
            text=str(value),
            session_id=session_id,
            data={"value": value},
        )

    @classmethod
    def system_cmd(cls, command: str, session_id: str = "") -> "InboundMessage":
        """创建系统命令消息"""
        return cls(type=MessageType.SYSTEM_CMD, text=command, session_id=session_id)

    @classmethod
    def from_dict(cls, d: dict) -> "InboundMessage":
        """从字典构建（用于 API 反序列化）"""
        return cls(
            type=d.get("type", ""),
            text=d.get("text", ""),
            session_id=d.get("session_id", ""),
            data=d.get("data", {}),
            raw=d,
        )

    def to_dict(self) -> dict:
        """序列化为字典"""
        return {k: v for k, v in asdict(self).items() if k != "raw"}


# ====================================================================
# 出站消息
# ====================================================================


@dataclass
class OutboundMessage:
    """统一出站消息 — 发往外部（玩家/客户端）的消息

    字段:
        type:       消息类型 (MessageType 常量)
        text:       消息文本正文
        session_id: 会话 ID
        game_phase: 当前游戏阶段
        data:       附加结构化数据
        timestamp:  消息时间戳
    """
    type: str
    text: str = ""
    session_id: str = ""
    game_phase: str = ""
    data: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())

    # ── 便捷工厂 ──

    @classmethod
    def narrative(cls, text: str, session_id: str = "", game_phase: str = "") -> "OutboundMessage":
        """创建叙事消息"""
        return cls(
            type=MessageType.NARRATIVE,
            text=text,
            session_id=session_id,
            game_phase=game_phase,
        )

    @classmethod
    def dice_request(
        cls,
        reason: str,
        skill_name: str = "",
        difficulty: str = "",
        session_id: str = "",
    ) -> "OutboundMessage":
        """创建掷骰请求消息"""
        return cls(
            type=MessageType.DICE_REQUEST,
            text=reason,
            session_id=session_id,
            data={
                "skill_name": skill_name,
                "difficulty": difficulty,
            },
        )

    @classmethod
    def system_msg(
        cls,
        text: str,
        level: str = "info",
        session_id: str = "",
    ) -> "OutboundMessage":
        """创建系统消息"""
        return cls(
            type=MessageType.SYSTEM_MSG,
            text=text,
            session_id=session_id,
            data={"level": level},
        )

    @classmethod
    def session_info(cls, state: dict, session_id: str = "") -> "OutboundMessage":
        """创建会话状态消息"""
        return cls(
            type=MessageType.SESSION_INFO,
            text="",
            session_id=session_id,
            data=state,
        )

    @classmethod
    def error(cls, message: str, session_id: str = "") -> "OutboundMessage":
        """创建错误消息"""
        return cls(
            type=MessageType.SYSTEM_MSG,
            text=message,
            session_id=session_id,
            data={"level": "error"},
        )

    def to_dict(self) -> dict:
        """序列化为字典（用于 API / WebSocket 序列化）"""
        return {k: v for k, v in asdict(self).items()}

    def to_json_safe(self) -> dict:
        """序列化为 JSON-safe 字典"""
        result = {}
        for k, v in asdict(self).items():
            if isinstance(v, datetime):
                result[k] = v.isoformat()
            else:
                result[k] = v
        return result
