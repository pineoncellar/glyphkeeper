"""
世界状态管理器

职责:
  - 管理完整的游戏世界状态（场景、NPC、物品、线索）
  - 提供 Schema 级别的世界隔离（world_<name>）
  - 处理世界初始化、备份与恢复
  - 作为世界数据的单一查询入口

数据模型（PostgreSQL）:
  - Location: 场景及连接关系
  - Entity: NPC / 怪物
  - Interactable: 可交互物品
  - Knowledge: 知识注册表
  - ClueDiscovery: 线索发现中间层
"""
