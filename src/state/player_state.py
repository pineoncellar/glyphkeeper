"""
玩家/调查员状态

职责:
  - 管理单个调查员的完整状态（属性、技能、物品、位置）
  - 处理状态变更事件的校验与应用
  - 提供状态查询接口（供 Nodes 读取）

数据模型（PostgreSQL）:
  - Entity: name, location_id, stats(JSONB), attacks, tags
  - InvestigatorProfile: occupation, backstory, assets
  - Interactable: inventory 物品清单
"""
