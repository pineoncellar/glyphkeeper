"""
Location 仓库模块

封装游戏地点相关的 CRUD 操作：

查询操作：
- get_location_by_id(location_id) -> Location
- get_location_by_name(name) -> Location
- get_location_state(location_id) -> dict
- list_locations() -> List[Location]
- get_connected_locations(location_id) -> List[Location]

修改操作：
- create_location(data) -> Location
- update_location(location_id, data) -> Location
- update_location_state(location_id, state) -> Location
- delete_location(location_id) -> bool

地点关系：
- add_location_connection(from_id, to_id, direction)
- get_location_graph() -> Dict
"""
