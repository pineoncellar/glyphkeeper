# -*- coding: utf-8 -*-
"""
@File     :   onebot.py
@Desc     :   OneBot 11 协议 WebSocket 适配器
@Note     :   TODO: 待实现

职责:
  - 通过 OneBot 11 反向 WebSocket 连接 QQ 机器人
  - 将 QQ 群聊/私聊消息解析为 InboundMessage
  - 将 OutboundMessage 转发为 OneBot 协议的消息发送

协议参考:
  https://github.com/botuniverse/onebot-11

连接方式:
  - 反向 WebSocket（由 OneBot 客户端主动连接）
  - 正向 WebSocket（由本服务主动连接，待定）

消息映射:
  QQ 群消息 → InboundMessage.player_input(text)
  OutboundMessage.narrative → QQ 群消息
  OutboundMessage.dice_request → QQ 群消息 + [CQ:at] 提醒
"""
