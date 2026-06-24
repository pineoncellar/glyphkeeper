# -*- coding: utf-8 -*-
"""
@File     :   adapter/__init__.py
@Desc     :   adapter 包 — 统一接入层
@Note     :   所有外部输入统一为 InboundMessage，经 handle() 后输出 OutboundMessage

职责:
  - 定义统一的 InboundMessage / OutboundMessage 消息协议
  - 提供 AbstractAdapter 基类（所有接入方式的共同接口）
  - CliAdapter: 终端交互式 REPL（当前唯一实现）
  - OneBotAdapter: OneBot 11 协议 WebSocket（待实现）

核心概念:
  外部输入 → InboundMessage → Adapter.handle() → OutboundMessage → 外部输出
"""

from src.adapter.protocol import InboundMessage, OutboundMessage, MessageType
from src.adapter.base import AbstractAdapter
from src.adapter.cli import CliAdapter

__all__ = [
    # 协议
    "InboundMessage",
    "OutboundMessage",
    "MessageType",
    # 基类
    "AbstractAdapter",
    # 实现
    "CliAdapter",
]
