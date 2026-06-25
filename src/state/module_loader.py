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
from src.state.game_state import GameState, create_initial_state

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
            from src.memory.event_store import EventStore
            self._event_store = EventStore()
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

        # 构建 world_data 快照供 WorldManager 后续使用
        # 以事件方式写入新会话，确保 WorldManager.load_location() 可访问
        locations = world_data.get("locations", {})
        if locations:
            await es.append(
                session_id=session_id,
                event_type="WorldInitialized",
                data={
                    "module_name": module_name,
                    "locations": locations,
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
