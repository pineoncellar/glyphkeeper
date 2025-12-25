"""
SQLAlchemy ORM 模型定义

定义游戏业务相关的数据表结构：
- Location: 游戏地点（名称、描述、状态、连接关系）
- Entity: 实体/NPC/角色（属性、位置、关系）
- Clue: 线索/物品（描述、发现条件、关联实体）
- Relationship: 实体间关系（好感度、敌对度）
- GameSession: 游戏会话（进度、时间线、状态）

用法示例：
    from .models import Location, Entity
    location = Location(name="神秘森林", description="阴暗潮湿")
"""
