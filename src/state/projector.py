# -*- coding: utf-8 -*-
"""
@File     :   projector.py
@Desc     :   StateProjector — 事件投影器，CQRS 写端到读端的唯一桥梁
@Note     :   静态事件事务耦合，运行时事件事务解耦（尽力而为）
"""

from __future__ import annotations

from typing import Optional

from src.tools import get_logger

logger = get_logger(__name__)


class StateProjector:
    """事件投影器

    监听 EventStore 中的事件类型，将其投影到 CQRS 读模型表。
    是唯一能写入读模型表的入口。

    使用方式:
        projector = StateProjector()
        await projector.handle(event)
    """

    def __init__(self, static_store=None, session_state=None):
        self._static_store = static_store
        self._session_state = session_state

    # ── 懒加载属性 ──

    @property
    async def static_store(self):
        if self._static_store is None:
            from src.state.read_models import StaticReadStore
            self._static_store = StaticReadStore()
        return self._static_store

    @property
    async def session_state(self):
        if self._session_state is None:
            from src.state.session_state import SessionKnowledgeState
            self._session_state = SessionKnowledgeState()
        return self._session_state

    # ── 主入口 ──

    async def handle(
        self,
        event: dict,
        *,
        shared_conn=None,
    ) -> bool:
        """处理单条事件并投影到读模型表

        传入 shared_conn 时使用该连接（事务耦合，投影失败会抛异常触发回滚），
        否则使用独立连接（事务解耦，投影失败仅记警告）。

        返回是否投影成功。
        """
        event_type = event.get("type", "")
        event_data = event.get("data", {})

        try:
            if event_type == "WorldInitialized":
                await self._on_world_initialized(event_data, conn=shared_conn)
                return True

            elif event_type == "ClueDiscovered":
                await self._on_clue_discovered(event_data)
                return True

            else:
                # 其他事件类型跳过投影
                return True

        except Exception as e:
            logger.error(f"投影失败 (event_type={event_type}): {e}")
            if shared_conn is not None:
                raise  # 事务耦合：抛异常触发回滚
            return False  # 事务解耦：返回 False 不抛异常

    # ── 静态事件投影（事务耦合） ──

    async def _on_world_initialized(self, data: dict, conn=None):
        """将 WorldInitialized 事件展开写入所有静态读模型表

        先写 knowledge_registry 和 locations，再遍历 raw_locations 中的
        物品和 NPC 数据提取线索关联，最后统一写入 interactables 和 clue_discoveries。
        """
        store = await self.static_store

        # 先写知识注册表和场景表 — 它们是线索表的外键依赖
        knowledge_list = data.get("knowledge_registry", [])
        if knowledge_list:
            await store.bulk_insert_knowledge(knowledge_list)

        locations_data = data.get("locations", [])
        if locations_data:
            await store.bulk_insert_locations(locations_data)

        # 遍历原始场景数据，拆解物品和 NPC 中的线索
        all_interactables = []
        all_clues = []

        loc_id_map = {}  # location_key → location_id 供外键引用
        if locations_data:
            for loc in locations_data:
                loc_id_map[loc["key"]] = loc.get("id")

        for loc_data in data.get("raw_locations", []):
            loc_key = loc_data.get("key", "")
            location_id = loc_id_map.get(loc_key)

            for item_data in loc_data.get("interactables", []):
                item_id = item_data.get("id", "")
                all_interactables.append({
                    "id": item_id,
                    "key": item_data.get("key", ""),
                    "name": item_data.get("name", ""),
                    "location_id": location_id,
                    "tags": item_data.get("tags", []),
                })

                # 物品的 clues 字段含 required_check 和 target_knowledge
                item_clues = item_data.get("clues", [])
                for clue in item_clues:
                    target_knowledge = clue.get("target_knowledge")
                    knowledge_id = None
                    if target_knowledge:
                        knowledge_id = self._find_knowledge_id(knowledge_list, target_knowledge)
                    # 无论 target_knowledge 是否为 null，都录入库中
                    # null 表示纯 flavor_text 线索（不关联知识），但文本仍保留
                    all_clues.append({
                        "interactable_id": item_id,
                        "entity_key": None,
                        "knowledge_id": knowledge_id,
                        "required_check": clue.get("required_check", {}),
                        "flavor_text": clue.get("flavor_text", ""),
                    })

            # NPC 的 dialogue_clues 字段同理
            for entity_data in loc_data.get("entities", []):
                entity_key = entity_data.get("key", "")
                dialogue_clues = entity_data.get("dialogue_clues", [])
                for clue in dialogue_clues:
                    target_knowledge = clue.get("target_knowledge")
                    knowledge_id = None
                    if target_knowledge:
                        knowledge_id = self._find_knowledge_id(knowledge_list, target_knowledge)
                    all_clues.append({
                        "interactable_id": None,
                        "entity_key": entity_key,
                        "knowledge_id": knowledge_id,
                        "required_check": clue.get("required_check", {}),
                        "flavor_text": clue.get("flavor_text", ""),
                    })

        if all_interactables:
            await store.bulk_insert_interactables(all_interactables)

        if all_clues:
            await store.bulk_insert_clues(all_clues)

        loc_count = len(locations_data)
        item_count = len(all_interactables)
        clue_count = len(all_clues)
        kn_count = len(knowledge_list)
        logger.info(
            f"projector: WorldInitialized 投影完成 "
            f"(locations={loc_count}, interactables={item_count}, "
            f"clues={clue_count}, knowledge={kn_count})"
        )

    # ── 运行时事件投影（事务解耦，尽力而为） ──

    async def _on_clue_discovered(self, data: dict):
        """投影 ClueDiscovered 事件到 session_knowledge_state

        此方法使用独立连接，与 EventStore 事务解耦。
        失败时只记录警告，不抛异常。
        """
        ss = await self.session_state
        await ss.record_discovery(
            session_id=data.get("session_id", "default"),
            knowledge_id=data.get("knowledge_id", ""),
            source=data.get("source", "auto"),
            character_name=data.get("character_name", ""),
        )

    # ── 辅助方法 ──

    @staticmethod
    def _find_knowledge_id(
        knowledge_list: list[dict],
        target_knowledge_id: str,
    ) -> Optional[str]:
        """在知识列表中查找 knowledge_id 对应的 UUID

        通过 knowledge_id（逻辑标识符）查找对应记录的 UUID。
        """
        for k in knowledge_list:
            if k.get("knowledge_id") == target_knowledge_id:
                return k.get("id")
        return None
