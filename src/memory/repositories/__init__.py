"""
Repositories 模块

仓库模式封装，提供统一的数据访问接口：
- EntityRepository: 实体/角色数据操作
- LocationRepository: 地点数据操作
- ClueRepository: 线索/物品数据操作（待实现）
- RelationshipRepository: 关系数据操作（待实现）

用法示例：
    from .repositories import EntityRepository, LocationRepository
    
    async with get_db() as session:
        entity_repo = EntityRepository(session)
        entity = await entity_repo.get_entity_by_id(1)
"""
