"""
Entity 仓库模块

封装实体/NPC/角色相关的 CRUD 操作：

查询操作：
- get_entity_by_id(entity_id) -> Entity
- get_entity_by_name(name) -> Entity
- list_entities(filters) -> List[Entity]
- search_entities_by_tag(tag) -> List[Entity]

修改操作：
- create_entity(data) -> Entity
- update_entity(entity_id, data) -> Entity
- delete_entity(entity_id) -> bool
- update_entity_location(entity_id, location_id)

关系操作：
- get_entity_relationships(entity_id)
- add_relationship(entity1_id, entity2_id, relation_type)
"""
