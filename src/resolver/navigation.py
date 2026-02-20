"""
移动组件，负责处理实体的移动请求，包括短距离的基于地图连接的移动和长距离的基于路径寻找的旅行。
在场景切换时调用，更新实体所在位置，并返回移动结果。
"""
from typing import List, Optional, Deque, Dict, Any, Tuple
from collections import deque
from uuid import UUID

from .base import BaseComponent
from ..core.events import ResolutionResult
from ..core.logger import get_logger
from ..memory.database import db_manager
from ..memory.repositories import EntityRepository, LocationRepository

logger = get_logger(__name__)

class NavigationComponent(BaseComponent):
    def initialize(self):
        self.db_manager = db_manager

    async def move(self, target: list, destination: str) -> ResolutionResult:
        """
        统一移动接口。
        target: 实体名称列表
        destination: 目标地点名称，或者方向关键词（如 'north', 'door'）
        """
        results = []
        for entity_name in target:
            # 1. 尝试作为方向移动（短距离）
            # 由于方向词通常较短且具体，优先匹配方向
            res = await self.move_entity_direction(entity_name, destination)
            if res.success:
                results.append(res.outcome_desc)
            else:
                # 2. 如果方向不明或失败，尝试作为地点名长途旅行（长距离/快速旅行）
                res_travel = await self.travel_to_location(entity_name, destination)
                if res_travel.success:
                    results.append(res_travel.outcome_desc)
                else:
                     # 若两者皆败，返回较具体的短距离移动失败原因（通常包含方向错误信息）
                    results.append(f"{entity_name}: 移动失败 ({res.outcome_desc}) | 导航失败 ({res_travel.outcome_desc})")

        # 只要有一个成功就算成功
        success = any("移动到了" in r or "快速旅行" in r or "抵达" in r for r in results)
        
        return ResolutionResult(
            success=success,
            outcome_desc="; ".join(results)
        )

    async def move_entity_direction(self, entity_name: str, direction: str) -> ResolutionResult:
        """短距离移动实体，受地图连接状态影响"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 获取人与当前位置
            entity = await entity_repo.get_by_name(entity_name)
            if not entity or not entity.location_id:
                return ResolutionResult(success=False, outcome_desc="未找到实体或实体不在任何地点")

            current_loc = await location_repo.get_by_id(entity.location_id)
            if not current_loc:
                return ResolutionResult(success=False, outcome_desc="当前地点数据异常")

            # 解析出口
            target_ref = None # 可能是 Name，也可能是 Key
            direction_key = None
            
            # 遍历exits
            exits = current_loc.exits or {}
            for exit_dir, target in exits.items():
                if exit_dir.lower() == direction.lower():
                    direction_key = exit_dir
                    target_ref = target
                    break
            
            if not target_ref:
                return ResolutionResult(success=False, outcome_desc=f"方向 '{direction}' 没有通路")

            # 先尝试 Key 查找
            target_loc = await location_repo.get_by_key(target_ref)
            
            # 如果没找到，再尝试当做中文名查找
            if not target_loc:
                target_loc = await location_repo.get_by_name(target_ref)
                
            if not target_loc:
                 return ResolutionResult(success=False, outcome_desc=f"地图连接错误：找不到目标 '{target_ref}'")

            # 执行移动
            old_location_name = current_loc.name
            entity.location_id = target_loc.id
            session.add(entity) # 确保更新被追踪
            await session.commit() # 显式提交

            return ResolutionResult(
                success=True,
                outcome_desc=f"从 {old_location_name} 向 {direction_key} 移动到了 {target_loc.name}。"
            )

    async def travel_to_location(self, entity_name: str, target_ref: str) -> ResolutionResult:
        """长距离移动实体，计算路径并自动移动"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 出发点
            entity = await entity_repo.get_by_name(entity_name)
            if not entity or not entity.location_id:
                return ResolutionResult(success=False, outcome_desc="未找到实体或实体不在地图上")
            
            start_loc_id = entity.location_id

            # 构建导航图
            raw_locs = await location_repo.get_navigation_graph_data()
            
            graph = {}
            key_to_id: Dict[str, UUID] = {}   # 辅助索引: Key -> ID
            name_to_id: Dict[str, UUID] = {}  # 辅助索引: Name -> ID

            for row in raw_locs:
                loc_id = row.id
                graph[loc_id] = {
                    "key": row.key,
                    "name": row.name,
                    "exits": row.exits or {},
                    "tags": row.tags or []
                }
                if row.key: key_to_id[row.key] = loc_id
                if row.name: name_to_id[row.name] = loc_id

            # 解析目标 ID
            target_id = None
            if target_ref in key_to_id:
                target_id = key_to_id[target_ref]
            elif target_ref in name_to_id:
                target_id = name_to_id[target_ref]
            
            if not target_id:
                return ResolutionResult(success=False, outcome_desc=f"地图上不存在名为 '{target_ref}' 的地点")

            if start_loc_id == target_id:
                return ResolutionResult(success=False, outcome_desc="你已经在这里了")

            # BFS 寻路算法
            # queue 元素: (current_id, path_list)
            # path_list 元素: {"id": next_id, "dir": direction_str}
            queue = deque([(start_loc_id, [])]) 
            visited = {start_loc_id}
            found_path = None

            while queue:
                curr_id, path = queue.popleft()
                
                if curr_id == target_id:
                    found_path = path
                    break
                
                # 获取当前节点的邻居
                curr_node = graph.get(curr_id)
                if not curr_node: continue

                for direction, neighbor_key in curr_node["exits"].items():
                    # 注意：Exits 里存的是 Key，我们需要转成 ID
                    neighbor_id = key_to_id.get(neighbor_key)
                    
                    if neighbor_id and neighbor_id not in visited:
                        visited.add(neighbor_id)
                        new_path = list(path)
                        new_path.append({"id": neighbor_id, "dir": direction})
                        queue.append((neighbor_id, new_path))

            if found_path is None:
                return ResolutionResult(success=False, outcome_desc=f"你无法到达 '{target_ref}'（无路径或隔离区域）")

            # 模拟行走与阻挡检查
            final_loc_id = start_loc_id
            travel_log = [] # 记录路过的地名
            interrupted_reason = None
            
            # 当前所在位置的各种信息，一开始是起点
            current_sim_node = graph[start_loc_id]

            for step in found_path:
                next_id = step["id"]
                direction = step["dir"]
                next_node = graph[next_id]
                
                # 检查该地点是否有 "locked", "blocked", "police_line" 等等 Tag
                block_tags = {"blocked", "sealed", "locked", "police_line"} 
                if set(next_node["tags"]) & block_tags:
                    interrupted_reason = f"试图前往 {next_node['name']} 时受阻（状态: 被封锁/锁住）"
                    break # 停止移动，停在上一个节点
                
                # 通过检查，更新当前位置
                final_loc_id = next_id
                travel_log.append(f"{direction} -> {next_node['name']}")
                current_sim_node = next_node

            # 执行最终移动
            if final_loc_id != start_loc_id:
                entity.location_id = final_loc_id
                session.add(entity)
                await session.commit()

            final_loc_name = graph[final_loc_id]['name']
            path_desc = " -> ".join(travel_log)
            
            if interrupted_reason:
                return ResolutionResult(
                    success=True, 
                    outcome_desc=f"旅行中断！{interrupted_reason}。当前停留在: {final_loc_name}"
                )
            else:
                return ResolutionResult(
                    success=True,
                    outcome_desc=f"快速旅行完成。抵达 {final_loc_name}。路径: [{path_desc}]"
                )
