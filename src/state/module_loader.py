# -*- coding: utf-8 -*-
"""
@File     :   module_loader.py
@Desc     :   模组载入器 — 从 EventStore 读取已摄入模组，构建初始 GameState
@Note     :   在 CLI/WebSocket 启动时调用，加载场景世界观和开场配置

数据流:
    EventStore (模组模板会话)
        │  WorldInitialized 事件 → locations / entities / interactables
        │  OpeningTemplateSet 事件 → opening / intro / start_location
        ▼
    ModuleLoader.load(session_id)
        │
        ▼
    GameState (scenario_name / time_slot / world_context / active_tags)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.tools import get_logger
from src.memory.event_store import EventStore
from src.state.game_state import GameState, create_initial_state, get_current_player

logger = get_logger(__name__)

# 模组模板会话 ID（固定，与 ingestion 模块保持一致）
TEMPLATE_SESSION_ID = "00000000-0000-0000-0000-000000000000"


class ModuleLoader:
    """模组载入器

    从 EventStore 读取已摄入的模组数据，构建游戏初始状态。

    使用方式:
        loader = ModuleLoader(event_store)
        state = await loader.load("session-001", module_name="book")
        # 或自动检测已摄入的模组:
        names = await loader.list_modules()
        state = await loader.load("session-001", module_name=names[0])
    """

    def __init__(self, event_store: Optional[EventStore] = None):
        self._event_store = event_store

    # ── 属性（延迟导入） ──

    @property
    async def event_store(self) -> EventStore:
        if self._event_store is None:
            from src.memory.event_store import create_event_store
            self._event_store = await create_event_store()
        return self._event_store

    # ── 公开接口 ──

    async def list_modules(self) -> list[dict]:
        """列出所有已摄入的模组

        返回:
            [
                {"name": "book", "description": "...", "locations": 6, ...},
            ]
        """
        es = await self.event_store
        events = await es.get_events(TEMPLATE_SESSION_ID, since_version=0)

        modules: dict[str, dict] = {}
        for evt in events:
            data = evt.get("data", {})
            event_type = evt.get("type", "")
            if event_type == "WorldInitialized":
                name = data.get("module_name", "")
                if name:
                    modules[name] = {
                        "name": name,
                        "locations": len(data.get("locations", {})),
                        "found": True,
                    }
            elif event_type == "OpeningTemplateSet":
                name = data.get("module_name", "")
                opening = data.get("opening", {})
                if name in modules:
                    modules[name]["start_location"] = opening.get("start_location_key", "")
                    modules[name]["time_slot"] = opening.get("start_time_slot", "MORNING")

        result = list(modules.values())
        logger.debug(f"ModuleLoader: 发现 {len(result)} 个已摄入模组")
        return result

    async def load(
        self,
        session_id: str,
        module_name: str,
    ) -> Optional[GameState]:
        """加载指定模组，返回初始化的 GameState

        参数:
            session_id: 新游戏会话 ID
            module_name: 模组名称（与 ingestion 时的 module_name 一致）

        返回:
            初始化好的 GameState，模组未找到时返回 None
        """
        es = await self.event_store
        events = await es.get_events(TEMPLATE_SESSION_ID, since_version=0)

        # 从事件流中提取模组数据
        world_data = None
        opening_data = None

        for evt in events:
            data = evt.get("data", {})
            event_type = evt.get("type", "")
            if event_type == "WorldInitialized" and data.get("module_name") == module_name:
                world_data = data
            elif event_type == "OpeningTemplateSet" and data.get("module_name") == module_name:
                opening_data = data

        if not world_data:
            logger.error(f"模组 '{module_name}' 未找到（尚未摄入）")
            return None

        opening = opening_data.get("opening", {}) if opening_data else {}

        # 构建初始 GameState
        time_slot = opening.get("start_time_slot", "MORNING")
        required_tags = opening.get("required_tags", [])
        start_location_key = opening.get("start_location_key", "")
        intro_text = opening.get("intro_text_template", "")

        state = create_initial_state(
            session_id=session_id,
            scenario_name=module_name,
            time_slot=time_slot,
        )

        # 填充开场叙事
        if intro_text:
            state["narrative"] = intro_text
            # 注入开场文本作为 world_context，方便 LookupNode 检索
            state["world_context"] = f"故事开场：{intro_text}"

        # 设置全局标签
        state["active_tags"] = list(required_tags)

        # ── 修复：设置初始地点 ──
        if start_location_key:
            get_current_player(state)["current_location"] = start_location_key
            logger.info(
                f"ModuleLoader: 初始位置 '{start_location_key}' 已设置"
            )

        # 构建 world_data 快照供 WorldManager 后续使用
        # 以事件方式写入新会话，确保 WorldManager.load_location() 可访问
        # 同时拷贝 raw_locations + knowledge_registry 确保实体全量数据
        locations = world_data.get("locations", {})
        raw_locations = world_data.get("raw_locations", [])
        knowledge_registry = world_data.get("knowledge_registry", [])
        if locations:
            await es.append(
                session_id=session_id,
                event_type="WorldInitialized",
                data={
                    "module_name": module_name,
                    "locations": locations,
                    "raw_locations": raw_locations,
                    "knowledge_registry": knowledge_registry,
                    "start_location_key": start_location_key,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                source_node="module_loader",
            )

        logger.info(
            f"ModuleLoader: 模组 '{module_name}' 已载入 "
            f"session={session_id[:8]} "
            f"locations={len(locations)} "
            f"tags={required_tags}"
        )
        return state

    async def delete_module(self, module_name: str) -> bool:
        """彻底删除模组：事件 + 读模型 + LightRAG 种子工作区

        先从 EventStore 读取该模组的事件，提取场景/物品/实体/NPC 的 key，
        再依次清理：读模型行 → 事件 → LightRAG 种子目录及 PG 数据。
        重摄入同一模组后不会残留旧数据。
        """
        modules = await self.list_modules()
        names = [m["name"] for m in modules]
        if module_name not in names:
            logger.warning("delete_module: 模组 '%s' 不存在", module_name)
            return False

        es = await self.event_store
        # 先读事件提取 key，再删事件
        events = await es.get_events(TEMPLATE_SESSION_ID, since_version=0)
        loc_keys: list[str] = []
        entity_keys: list[str] = []
        interactable_keys: list[str] = []
        knowledge_ids: list[str] = []

        for evt in events:
            data = evt.get("data", {})
            if data.get("module_name") != module_name:
                continue
            if evt.get("type") == "WorldInitialized":
                for loc in data.get("locations", []):
                    loc_keys.append(loc.get("key", ""))
                for rl in data.get("raw_locations", []):
                    for ent in rl.get("entities", []):
                        entity_keys.append(ent.get("key", ""))
                    for it in rl.get("interactables", []):
                        interactable_keys.append(it.get("key", ""))
                for kr in data.get("knowledge_registry", []):
                    knowledge_ids.append(kr.get("knowledge_id", ""))

        # 清理读模型
        if loc_keys or entity_keys or interactable_keys or knowledge_ids:
            try:
                from src.state.read_models import StaticReadStore
                store = StaticReadStore()
                conn = await store._get_conn()
                async with conn.transaction():
                    # 按外键依赖顺序从子到父删除
                    if interactable_keys:
                        # clue_discoveries 通过 interactable_id UUID 关联，需先查 UUID
                        rows = await conn.fetch(
                            "SELECT id FROM interactables WHERE key = ANY($1::text[])",
                            interactable_keys,
                        )
                        interactable_ids = [r["id"] for r in rows]
                        if interactable_ids:
                            await conn.execute(
                                "DELETE FROM clue_discoveries WHERE interactable_id = ANY($1::uuid[])",
                                interactable_ids,
                            )
                        await conn.execute(
                            "DELETE FROM interactables WHERE key = ANY($1::text[])",
                            interactable_keys,
                        )
                    if entity_keys:
                        await conn.execute(
                            "DELETE FROM entities WHERE key = ANY($1::text[])",
                            entity_keys,
                        )
                    if knowledge_ids:
                        # clue_discoveries 也通过 knowledge_id UUID 关联
                        krows = await conn.fetch(
                            "SELECT id FROM knowledge_registry WHERE knowledge_id = ANY($1::text[])",
                            knowledge_ids,
                        )
                        kid_uuids = [r["id"] for r in krows]
                        if kid_uuids:
                            await conn.execute(
                                "DELETE FROM clue_discoveries WHERE knowledge_id = ANY($1::uuid[])",
                                kid_uuids,
                            )
                        await conn.execute(
                            "DELETE FROM knowledge_registry WHERE knowledge_id = ANY($1::text[])",
                            knowledge_ids,
                        )
                    if loc_keys:
                        await conn.execute(
                            "DELETE FROM locations WHERE key = ANY($1::text[])",
                            loc_keys,
                        )
                logger.info(
                    "读模型清理: locations=%d, interactables=%d, entities=%d, knowledge=%d",
                    len(loc_keys), len(interactable_keys),
                    len(entity_keys), len(knowledge_ids),
                )
            except Exception as e:
                logger.warning("读模型清理失败（可重摄入修复）: %s", e)

        # 清理 LightRAG 种子工作区
        try:
            from src.memory.vector_store import VectorStore
            from src.tools.pg_manager import PgManager

            # 删本地目录
            seed_ws = VectorStore.seed_workspace_name(module_name)
            import shutil
            from src.tools import PROJECT_ROOT
            seed_dir = PROJECT_ROOT / "data" / "worlds" / seed_ws
            if seed_dir.exists():
                shutil.rmtree(seed_dir)
                logger.info("种子目录已删除: %s", seed_dir)

            # 删 PG 中该 workspace 的 LightRAG 数据
            mgr = await PgManager.get_instance()
            if mgr.available:
                await mgr.start()
                import asyncpg
                conn2 = await asyncpg.connect(mgr.uri)
                try:
                    for tbl in (
                        "LIGHTRAG_VDB_ENTITY", "LIGHTRAG_VDB_RELATION",
                        "LIGHTRAG_VDB_CHUNKS", "LIGHTRAG_DOC_CHUNKS",
                        "LIGHTRAG_DOC_STATUS", "LIGHTRAG_LLM_CACHE",
                    ):
                        await conn2.execute(
                            f"DELETE FROM {tbl} WHERE workspace = $1", seed_ws,
                        )
                    logger.info("种子 PG 数据已清理: workspace=%s", seed_ws)
                finally:
                    await conn2.close()
        except Exception as e:
            logger.warning("种子工作区清理失败（可忽略）: %s", e)

        # 最后删事件
        ok = await es.delete_module_events(module_name)
        if not ok:
            logger.warning("delete_module: 事件删除返回空结果 (module=%s)", module_name)

        logger.info("模组已彻底删除: %s", module_name)
        return True

    async def load_opening_narrative(
        self, module_name: str
    ) -> Optional[str]:
        """仅获取模组的开场文本（不创建游戏会话）"""
        es = await self.event_store
        events = await es.get_events(TEMPLATE_SESSION_ID, since_version=0)

        for evt in events:
            if evt.get("type") == "OpeningTemplateSet":
                data = evt.get("data", {})
                if data.get("module_name") == module_name:
                    opening = data.get("opening", {})
                    return opening.get("intro_text_template", "")

        return None
