"""
GlyphKeeper 的逻辑核心
以原子化操作的形式向Narrator暴露工具接口。
这里只包含轻量级的原语；更高级的故事逻辑不在此处。
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from collections import deque

from ..core import get_logger
from ..memory.models import Entity
from ..memory.repositories import EntityRepository
from ..memory.repositories import LocationRepository
from ..memory.repositories import InteractableRepository
from ..memory.repositories import ClueDiscoveryRepository
from ..memory.repositories import KnowledgeRepository
from ..memory.repositories import SessionRepository
from ..memory.repositories import InvestigatorProfileRepository
from ..memory import RAGEngine
from ..memory.database import db_manager

logger = get_logger(__name__)


class Archivist:
    """以原子化操作的形式向Narrator暴露工具接口的核心逻辑类"""

    def __init__(self):
        self.db_manager = db_manager

    async def set_investigator_location(self, investigator_id: str, target_location_key: str) -> bool:
        """强制传送实体，仅供程序调用，不提供给LLM使用"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            entity = await entity_repo.get_by_id(investigator_id)
            if not entity:
                logger.error(f"Investigator {investigator_id} not found.")
                return False
            
            location = await location_repo.get_by_key(target_location_key)
            if not location:
                logger.error(f"Location {target_location_key} not found.")
                return False
            
            entity.location_id = location.id
            session.add(entity)
            await session.commit()
            return True
    
    async def get_entity_id_by_name(self, name:str) -> Optional[UUID]:
        """由实体名称获取实体ID，不提供LLM使用"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            entity = await entity_repo.get_by_name(name)
            if entity:
                return entity.id
            return None
    
    async def get_location_stat_by_key(self, location_key: str) -> Dict[str, Any]:
        """由地点ID获取地点状态摘要，不提供LLM使用"""
        async with self.db_manager.session_factory() as session:
            location_repo = LocationRepository(session)
            location = await location_repo.get_by_key(location_key)
            if not location:
                return {"ok": False, "reason": f"未找到场景: {location_key}"}
            
            return {
                "ok": True,
                "location_id": str(location.id),
                "location_key": location.key,
                "location_name": location.name,
                "description": location.base_desc,
                "exits": list(location.exits.keys()) if location.exits else [],
                "exits_detail": location.exits or {},  # 包含完整的出口映射
                "environment_tags": location.tags or [],
            }

    async def get_all_investigator_id(self) -> List[str]:
        """从InvestigatorProfile获取所有调查员ID列表，不提供LLM使用"""
        async with self.db_manager.session_factory() as session:
            investigator_repo = InvestigatorProfileRepository(session)
            investigators = await investigator_repo.list_all_profiles()
            return [str(inv.id) for inv in investigators]


    async def get_game_session_stat(self, session_id: UUID) -> Dict[str, Any]:
        """获取当前游戏会话的状态摘要"""
        async with self.db_manager.session_factory() as session:
            session_repo = SessionRepository(session)
            game_session = await session_repo.get_by_id(session_id)
            if not game_session:
                return {"ok": False, "reason": f"找不到会话: {session_id}"}
            
            return {
                "ok": True,
                "session_id": str(game_session.id),
                "time_slot": game_session.time_slot.value,
                "beat_counter": game_session.beat_counter,
                "active_global_tags": game_session.active_global_tags or [],
            }
    
    async def list_investigators(self, session_id: UUID) -> Dict[str, Any]:
        """列出当前游戏会话中的所有调查员名称"""
        async with self.db_manager.session_factory() as session:
            session_repo = SessionRepository(session)
            entity_repo = EntityRepository(session)
            
            game_session = await session_repo.get_by_id(session_id)
            if not game_session:
                return {"ok": False, "reason": f"找不到会话: {session_id}"}
            
            investigator_names = []
            for inv_id_str in game_session.investigator_ids:
                try:
                    inv_id = UUID(inv_id_str)
                    entity = await entity_repo.get_by_id(inv_id)
                    if entity:
                        investigator_names.append(entity.name)
                except Exception as e:
                    logger.warning(f"Invalid investigator ID in session: {inv_id_str} ({e})")
            
            return {
                "ok": True,
                "investigators": investigator_names
            }

    async def recall_knowledge(self, entity_name: str, query: str) -> Dict[str, Any]:
        """回忆以前解锁的知识、剧情或模组背景。"""
        try:
            # 获取 RAG 引擎单例
            engine = await RAGEngine.get_instance()
            
            # 执行检索 (Hybrid 模式能同时查到关键词和语义)
            # 这里可以根据 entity_name 做进一步的权限过滤，目前先不做
            results = await engine.query(query, mode="hybrid", top_k=3)
            
            if not results:
                return {"ok": True, "results": "没有找到相关记忆。"}
                
            return {
                "ok": True, 
                "results": results, 
                "system_note": "这是从右脑(LightRAG)检索到的相关记忆，请据此生成剧情。"
            }
        except Exception as e:
            logger.error(f"Recall failed: {e}")
            return {"ok": False, "error": str(e)}

    async def get_location_stat(self, entity_name: str) -> Dict[str, Any]:
        """获取指定实体当前场景的完整信息（包括物品、NPC、线索等）"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            interactable_repo = InteractableRepository(session)
            
            # 找人
            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"Entity not found: {entity_name}"}

            # 找地点
            if not entity.location_id:
                return {
                    "ok": True, 
                    "location_name": "Unknown", 
                    "description": "你在一片虚空之中。", 
                    "exits": [],
                    "interactables": [],
                    "entities": [],
                    "environment_tags": []
                }
                
            location = await location_repo.get_by_id(entity.location_id)
            if not location:
                return {"ok": False, "reason": "Location data corruption."}

            # 获取当前地点的所有物品/可交互对象
            interactables = await interactable_repo.get_by_location(entity.location_id)
            interactables_info = []
            for item in interactables:
                interactables_info.append({
                    "name": item.name,
                    "key": item.key,
                    "state": item.state,
                    "tags": item.tags or []
                })
            
            # 获取当前地点的所有其他实体（NPC 或其他角色）
            all_entities = await entity_repo.get_by_location(entity.location_id)
            entities_info = []
            for ent in all_entities:
                # 排除查询者自己
                if ent.name != entity_name:
                    entities_info.append({
                        "name": ent.name,
                        "tags": ent.tags or [],
                        "stats": ent.stats or {}
                    })

            # 构造完整场景数据
            return {
                "ok": True,
                "entity": entity.name,
                "location_id": str(location.id),
                "location_key": location.key,
                "location_name": location.name,
                "description": location.base_desc,
                "exits": list(location.exits.keys()) if location.exits else [],
                "exits_detail": location.exits or {},  # 包含完整的出口映射
                "environment_tags": location.tags or [],
                "interactables": interactables_info,  # 当前场景的所有物品
                "entities": entities_info,  # 当前场景的所有其他实体
                "system_note": f"场景中有 {len(interactables_info)} 个可交互对象和 {len(entities_info)} 个其他实体，请特别注意物品是否直接可见。"
            }

    async def inspect_target(self, viewer_name: str, target_name: str) -> Dict[str, Any]:
        """详细检查目标物品，可能触发线索发现。"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            investigator_profile_repo = InvestigatorProfileRepository(session)
            interactable_repo = InteractableRepository(session)
            clue_discovery_repo = ClueDiscoveryRepository(session)
            clue_repo = KnowledgeRepository(session)
            
            viewer = await entity_repo.get_by_name(viewer_name)
            viewer_profile = await investigator_profile_repo.get_by_entity_id(viewer.id) if viewer else None
            if not viewer_profile:
                return {"ok": False, "reason": f"找不到观察者: {viewer_name}"}
            
            # 获取目标
            target = await interactable_repo.get_by_name(target_name)
            if not target:
                return {"ok": False, "reason": f"找不到目标: {target_name}"}
            
            # 检查目标是否在观察者当前地点
            if target.location_id != viewer.location_id:
                return {"ok": False, "reason": f"{target_name} 不在你当前所在的地点。"}
            
            clue_list = []
            
            # 获取所有关联的线索发现逻辑
            related_clue = await clue_discovery_repo.get_by_interactable(target.id)
            if related_clue:
                for clue in related_clue:
                    # 取线索自身属性
                    raw_clue = await clue_repo.get_by_id(clue.knowledge_id)
                    clue_list.append({
                        "id": raw_clue.tags_granted,
                        "required_check": clue.required_check or {},
                        "discovery_flavor_text": clue.discovery_flavor_text
                    })
            
            # 返回检查结果
            return {
                "ok": True,
                "target_name": target.name,
                "state": target.state,
                "tags": target.tags or [],
                "clue_discovered": clue_list,
                "system_note": "这是对目标的详细检查结果，请据此生成后续剧情。若发现了线索，请特别注意触发条件。"
            }

    async def get_entity_status(self, entity_name: str) -> Dict[str, Any]:
        """返回实体的属性值，用于决策判断。"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"找不到实体: {entity_name}"}
            
            # 获取位置信息
            location_name = "未知"
            if entity.location_id:
                location = await location_repo.get_by_id(entity.location_id)
                if location:
                    location_name = location.name
            
            # 构建状态摘要
            stats = entity.stats or {}
            return {
                "ok": True,
                "entity": entity.name,
                "location": location_name,
                "hp": stats.get("hp", 0),
                "san": stats.get("san", 0),
                "mp": stats.get("mp", 0),
                "tags": entity.tags or [],
                "stats": stats,  # 完整属性
                "system_note": "以上是实体的当前状态。"
            }

    async def move_entity(self, entity_name: str, direction: str) -> Dict[str, Any]:
        """短距离移动实体，受地图连接状态影响"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 获取人与当前位置
            entity = await entity_repo.get_by_name(entity_name)
            if not entity or not entity.location_id:
                return {"ok": False, "reason": "无法移动：找不到实体或不在任何地点。"}

            current_loc = await location_repo.get_by_id(entity.location_id)
            
            # 解析出口
            target_ref = None # 可能是 Name，也可能是 Key
            direction_key = None
            
            # 遍历exits
            for exit_dir, target in (current_loc.exits or {}).items():
                if exit_dir.lower() == direction.lower():
                    direction_key = exit_dir
                    target_ref = target
                    break
            
            if not target_ref:
                return {"ok": False, "reason": f"方向 '{direction}' 没有路。"}

            # 先尝试 Key 查找
            target_loc = await location_repo.get_by_key(target_ref)
            
            # 如果没找到，再尝试当做中文名查找
            if not target_loc:
                target_loc = await location_repo.get_by_name(target_ref)
                
            if not target_loc:
                 return {"ok": False, "reason": f"地图连接错误：找不到目标 '{target_ref}'。"}

            # 执行移动
            entity.location_id = target_loc.id
            await entity_repo.save(entity)

            # 返回新地点的视野
            # 这样 Narrator 可以在一轮对话中完成 "移动+描述"
            new_view = await self.get_location_stat(entity_name)
            new_view["system_note"] = f"已成功向 {direction_key} 移动到 {target_loc.name}。"
            return new_view

    async def travel_to_location(self, entity_name: str, target_ref: str) -> Dict[str, Any]:
        """长距离移动实体，计算路径并自动移动"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            
            # 出发点
            entity = await entity_repo.get_by_name(entity_name)
            if not entity or not entity.location_id:
                return {"ok": False, "reason": "找不到实体或实体不在地图上。"}
            
            start_loc_id = entity.location_id

            # 构建导航图
            raw_locs = await location_repo.get_navigation_graph_data()
            
            graph = {}
            key_to_id = {}   # 辅助索引: Key -> ID
            name_to_id = {}  # 辅助索引: Name -> ID

            for row in raw_locs:
                # { loc_id: { "key": key, "name": name, "exits": {dir: target_key}, "tags": [] } }
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
                return {"ok": False, "reason": f"地图上不存在名为 '{target_ref}' 的地点。"}

            if start_loc_id == target_id:
                return {"ok": False, "reason": "你已经在这里了。"}

            # BFS 寻路算法，随便加的
            queue = deque([(start_loc_id, [])]) # (current_id, path_of_ids)
            visited = {start_loc_id}
            found_path = None

            while queue:
                curr_id, path = queue.popleft()
                
                if curr_id == target_id:
                    found_path = path
                    break
                
                # 获取当前节点的邻居
                curr_node = graph[curr_id]
                for direction, neighbor_key in curr_node["exits"].items():
                    # 注意：Exits 里存的是 Key，我们需要转成 ID
                    neighbor_id = key_to_id.get(neighbor_key)
                    
                    if neighbor_id and neighbor_id not in visited:
                        visited.add(neighbor_id)
                        # 记录路径：路径中存的是 (target_id, direction_used)
                        new_path = path + [{"id": neighbor_id, "dir": direction}]
                        queue.append((neighbor_id, new_path))

            if found_path is None:
                return {"ok": False, "reason": f"你无法到达 '{target_ref}'，这似乎是个完全封闭或隔离的区域。"}

            # 模拟行走与阻挡检查，看有没有哪个房间被封锁了
            final_loc_id = start_loc_id
            travel_log = [] # 记录路过的地名，给 Narrator 做素材
            interrupted_reason = None

            for step in found_path:
                next_id = step["id"]
                direction = step["dir"]
                next_node = graph[next_id]
                
                # 检查该地点是否有 "locked", "blocked", "police_line" 等等 Tag
                block_tags = {"blocked", "sealed", "locked", "police_line"} 
                # 求交集，如果不为空，说明有阻挡
                if set(next_node["tags"]) & block_tags:
                    interrupted_reason = f"试图前往 {next_node['name']} 时受阻（状态: 被封锁/锁住）。"
                    break # 停止移动，停在上一个节点
                
                # 通过检查，更新当前位置
                final_loc_id = next_id
                travel_log.append(f"{direction} -> {next_node['name']}")

            # 执行最终移动
            if final_loc_id != start_loc_id:
                entity.location_id = final_loc_id
                await entity_repo.save(entity)

            final_loc_node = graph[final_loc_id]
            
            # 获取标准视野
            view = await self.get_location_stat(entity_name)
            
            # 注入旅行摘要
            path_desc = " -> ".join(travel_log)
            if interrupted_reason:
                view["system_note"] = f"旅行中断！{interrupted_reason} 当前停留在: {final_loc_node['name']}。"
            else:
                view["system_note"] = f"快速旅行完成。路径: [{path_desc}]。"
                
            return view

    async def transfer_item(self, item_name: str, from_container: str, to_container: str) -> Dict[str, Any]:
        """在携带者/地点之间转移物品。"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            location_repo = LocationRepository(session)
            interactable_repo = InteractableRepository(session)
            
            # 查找物品
            item = await interactable_repo.get_by_name(item_name)
            if not item:
                return {"ok": False, "reason": f"物品不存在: {item_name}"}
            
            # 解析来源容器
            from_entity = await entity_repo.get_by_name(from_container)
            from_location = await location_repo.get_by_name(from_container) if not from_entity else None
            
            if not from_entity and not from_location:
                return {"ok": False, "reason": f"来源容器不存在: {from_container}"}
            
            # 验证物品当前位置
            if from_entity and item.carrier_id != from_entity.id:
                return {"ok": False, "reason": f"{item_name} 不在 {from_container} 的物品栏中。"}
            if from_location and item.location_id != from_location.id:
                return {"ok": False, "reason": f"{item_name} 不在 {from_container} 中。"}
            
            # 解析目标容器
            to_entity = await entity_repo.get_by_name(to_container)
            to_location = await location_repo.get_by_name(to_container) if not to_entity else None
            
            if not to_entity and not to_location:
                return {"ok": False, "reason": f"目标容器不存在: {to_container}"}
            
            # 执行转移
            if to_entity:
                item.carrier_id = to_entity.id
                item.location_id = None
            elif to_location:
                item.location_id = to_location.id
                item.carrier_id = None
            
            await interactable_repo.save(item)
            
            return {
                "ok": True,
                "item": item_name,
                "from": from_container,
                "to": to_container,
                "system_note": f"已将 {item_name} 从 {from_container} 转移到 {to_container}。"
            }

    async def update_entity_resource(self, entity_name: str, resource: str, delta: int) -> Dict[str, Any]:
        """
        改变通用资源（HP、SAN、MP）。
        当超过阈值时自动更新状态标记。
        """
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            
            resource_key = resource.lower()
            if resource_key not in {"hp", "mp", "san"}:
                return {"ok": False, "reason": f"资源不存在: {resource}"}

            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"实体不存在: {entity_name}"}
            stats = dict(entity.stats or {})
            # 如果目标没有该资源，则设默认值hp=10, mp=5, san=50
            if resource_key not in stats:
                default_values = {"hp": 10, "mp": 5, "san": 50}
                stats[resource_key] = default_values.get(resource_key)
            before = int(stats.get(resource_key, 0))
            # mp 扣减时判断是否足够
            if resource_key == "mp" and delta < 0 and before < abs(delta):
                return {
                    "ok": False,
                    "reason": "MP 不足，无法扣除。",
                    "entity": entity.name,
                    "resource": resource_key,
                    "before": before,
                    "delta": delta,
                    "after": before,
                    "status_flags": [],
                }
            after = before + delta
            stats[resource_key] = after
            tags = entity.tags or []

            status_flags: List[str] = []
            if resource_key == "hp":
                if after <= 0: # 血量小于0，立即死亡
                    stats["hp"] = 0
                    tags.add("DEAD")
                    status_flags.append("DEAD")
                elif delta < 0 and abs(delta) >= before / 2: # 受到大于等于最大生命值一半的伤害，进入重伤状态
                    tags.add("SERIOUSLY_INJURED")
                    status_flags.append("SERIOUSLY_INJURED")
                elif delta > 0 and "SERIOUSLY_INJURED" in tags and "DEAD" not in tags: # 得到医学处理，重伤状态解除
                    # TODO: 这里暂时无法判断是急救还是医学处理，以后再细化
                    tags.remove("SERIOUSLY_INJURED")
                    status_flags.append("SERIOUSLY_INJURED_CLEARED")

            if resource_key == "san":
                if after <= 0: # SAN归零，进入永久疯狂状态
                    stats["san"] = 0
                    tags.add("PERMANENT_INSANITY")
                    status_flags.append("PERMANENT_INSANITY")
                elif delta < 0 and abs(delta) >= 5: # 受到大于等于5点的精神伤害，进入临时疯狂状态
                    # TODO: 潜在疯狂判断以后再加
                    tags.add("TEMPORARY_INSANITY")
                    status_flags.append("TEMPORARY_INSANITY")
                else: # 当前san值小于等于最大san值的五分之四，进入不定性疯狂
                    # TODO: 以后再优化好了
                    max_san = stats.get("pow") * 5 or stats.get("POW") * 5 or 50
                    if after <= max_san * 4 / 5 :
                        tags.add("UNSTABLE_INSANITY")
                        status_flags.append("UNSTABLE_INSANITY")
                    elif after > max_san * 4 / 5 and "UNSTABLE_INSANITY" in tags:
                        tags.remove("UNSTABLE_INSANITY")
                        status_flags.append("UNSTABLE_INSANITY_CLEARED")
                

            entity.stats = stats
            entity.tags = tags

            await entity_repo.save(entity)

            return {
                "ok": True,
                "entity": entity.name,
                "resource": resource_key,
                "before": before,
                "delta": delta,
                "after": after,
                "status_flags": status_flags,
            }

    async def add_entity_tag(self, entity_name: str, value: list) -> Dict[str, Any]:
        """添加实体tags，不重复添加，支持批量添加"""
        async with self.db_manager.session_factory() as session:
            entity_repo = EntityRepository(session)
            
            entity = await entity_repo.get_by_name(entity_name)
            if not entity:
                return {"ok": False, "reason": f"实体不存在: {entity_name}"}
            
            tags = set(entity.tags or [])
            initial_tag_count = len(tags)
            for tag in value:
                tags.add(tag)
            entity.tags = list(tags)
            
            await entity_repo.save(entity)

            added_count = len(tags) - initial_tag_count
            return {
                "ok": True,
                "entity": entity.name,
                "added_tags": value,
                "current_tags": entity.tags,
                "total_tags": len(tags),
                "new_tags_added": added_count,
            }

    # --- 工具模式生成 ---
    def get_openai_tools_schema(self) -> List[dict]:
        """返回 OpenAI 可调用工具的列表。"""
        return [
            {
                "type": "function",
                "function": {
                    "name": "get_location_stat",
                    "description": "获取当前房间的描述和可见的物品。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string", "description": "观察者的名称。"}
                        },
                        "required": ["entity_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "inspect_target",
                    "description": "检查目标以获取详情和潜在线索。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "viewer_name": {"type": "string"},
                            "target_name": {"type": "string"},
                        },
                        "required": ["viewer_name", "target_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "get_entity_status",
                    "description": "获取实体的当前属性和物品栏摘要。",
                    "parameters": {
                        "type": "object",
                        "properties": {"entity_name": {"type": "string"}},
                        "required": ["entity_name"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "move_entity",
                    # 关键点：强调“相邻”、“一步”
                    "description": "【战术移动】移动到当前房间的直接相邻出口（exits）。适用于密室探索、逃跑或具体的每一步行动。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "direction": {"type": "string"},
                        },
                        "required": ["entity_name", "direction"],
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "travel_to_location",
                    # 关键点：强调“远距离”、“已知地点”、“跳过过程”
                    "description": "【叙事旅行】前往一个已知的、非相邻的远距离地点。系统会自动处理中间的路程。适用于城市内赶路或跳过无聊的移动过程。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string", "description": "移动者的名称"},
                            "target_ref": {"type": "string", "description": "目标地点的名称或Key，如'图书馆'或'loc_library'"},
                        },
                        "required": ["entity_name", "target_ref"],
                    }
                }
            },
            {
                "type": "function",
                "function": {
                    "name": "transfer_item",
                    "description": "在容器（实体或位置）之间转移物品。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "item_name": {"type": "string"},
                            "from_container": {"type": "string"},
                            "to_container": {"type": "string"},
                        },
                        "required": ["item_name", "from_container", "to_container"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "update_entity_resource",
                    "description": "更新实体的状态 HP/SAN/MP 并处理死亡/疯狂逻辑。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "resource": {"type": "string", "enum": ["hp", "san", "mp"]},
                            "delta": {"type": "integer", "description": "改变量（负数表示伤害）。"},
                        },
                        "required": ["entity_name", "resource", "delta"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "add_entity_tag",
                    "description": "为实体添加状态标签（tags），支持批量添加，自动去重。用于添加持久状态标记如'中毒'、'隐身'、'知识解锁'等。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string", "description": "实体的名称"},
                            "value": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": "要添加的标签列表，如 ['poisoned', 'Sprawled']"
                            },
                        },
                        "required": ["entity_name", "value"],
                    },
                },
            },
            {
                "type": "function",
                "function": {
                    "name": "recall_knowledge",
                    "description": "回忆以前解锁的知识并搜索 LightRAG 范围。",
                    "parameters": {
                        "type": "object",
                        "properties": {
                            "entity_name": {"type": "string"},
                            "query": {"type": "string"},
                        },
                        "required": ["entity_name", "query"],
                    },
                },
            },
        ]
