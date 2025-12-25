"""
Archivist Agent - 档案员代理

职责：
- 查询游戏规则和设定资料
- 检索历史记录和剧情日志
- 提供准确的游戏数据（属性、技能、物品）
- 辅助 Narrator 和玩家查找信息

特点：
- 非 LLM 代理，主要是逻辑封装
- 直接调用 memory 层和 RAG 引擎
- 结构化数据返回，便于其他代理使用

核心功能：
- search_rules(keyword): 搜索规则条目
- get_entity_stats(entity_id): 获取实体属性
- get_location_info(location_id): 获取地点信息
- list_game_sessions(): 列出历史会话
- export_game_log(session_id): 导出游戏日志

用法示例：
    archivist = Archivist()
    rule = await archivist.search_rules("战斗规则")
    stats = await archivist.get_entity_stats(npc_id=42)
"""
