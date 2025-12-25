"""
Database Tools - 数据库工具封装

将 memory 层的复杂接口封装成简单的工具函数，供 LLM Agent 调用：

实体相关：
- get_entity_info(entity_id) -> dict
  返回: {"name": "...", "hp": 100, "location": "..."}
  
- update_entity_hp(entity_id, new_hp) -> bool
  更新实体生命值

- get_entity_relationships(entity_id) -> list
  返回实体的关系列表

地点相关：
- get_location_state(location_id) -> dict
  返回: {"name": "...", "description": "...", "npcs": [...]}
  
- update_location_state(location_id, state) -> bool
  更新地点状态（如灯光、天气）

游戏状态：
- get_current_game_state(session_id) -> dict
  获取当前游戏状态快照
  
- save_game_action(session_id, action, result) -> bool
  保存玩家行动和结果到日志

设计原则：
- 返回简单的 dict/list，便于 LLM 理解
- 所有函数都有详细的 docstring（用于 Function Calling Schema）
- 异常转换为友好的错误消息字符串

用法示例：
    entity = await get_entity_info(entity_id=5)
    success = await update_entity_hp(entity_id=5, new_hp=80)
"""
