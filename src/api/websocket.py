"""
WebSocket 实时通信接口

职责:
  - 处理玩家与系统的实时双向通信
  - 接收玩家输入文本
  - 推送叙事文本与检定请求
  - 管理连接生命周期与会话绑定

协议:
  Client -> Server: { "type": "player_input", "text": "..." }
  Server -> Client: { "type": "narrative", "text": "..." }
  Server -> Client: { "type": "dice_request", "reason": "..." }
  Client -> Server: { "type": "dice_result", "value": 42 }
"""
