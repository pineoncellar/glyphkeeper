# -*- coding: utf-8 -*-
"""
@File     :   module_loader.py
@Desc     :   模组载入器 — 从 StaticReadStore 读取已摄入模组，构建初始 GameState
@Note     :   不再使用 EventStore + TEMPLATE_SESSION_ID，改查 module_meta + 读模型表。

数据流:
    module_meta 表 → 开场配置 / 时间 / 标签
    locations + entities + interactables 表 → 世界蓝图
        ↓
    ModuleLoader.load(world_id, module_name)
        ↓
    GameState (scenario_name / time_slot / world_context / active_tags)
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from src.tools import get_logger, PROJECT_ROOT
from src.state.game_state import GameState, create_initial_state, get_current_player

logger = get_logger(__name__)


class ModuleLoader:
    """模组载入器 — 从 StaticReadStore 读取模组数据，构建 GameState

    使用方式:
        loader = ModuleLoader()
        modules = await loader.list_modules()
        state = await loader.load(world_id="session-001", module_name="book")
    """

    async def list_modules(self) -> list[dict]:
        """列出所有已摄入的模组（查 module_meta 表）"""
        from src.state.read_models import StaticReadStore
        store = StaticReadStore()
        try:
            return await store.list_module_metas()
        finally:
            await store.close()

    async def load(
        self,
        world_id: str,
        module_name: str,
    ) -> Optional[GameState]:
        """加载指定模组，返回初始化的 GameState

        参数:
            world_id:    游戏世界 ID（新会话标识）
            module_name: 模组名称

        返回:
            初始化好的 GameState，模组未找到时返回 None
        """
        from src.state.read_models import StaticReadStore
        store = StaticReadStore()
        try:
            meta = await store.get_module_meta(module_name)
        finally:
            await store.close()

        if not meta:
            logger.error(f"模组 '{module_name}' 未找到（尚未摄入）")
            return None

        time_slot = meta.get("time_slot", "MORNING")
        required_tags = meta.get("required_tags", [])
        start_location_key = meta.get("start_location", "")
        intro_text = meta.get("intro_text", "")

        state = create_initial_state(
            scenario_name=module_name,
            time_slot=time_slot,
            world_id=world_id,
        )

        if intro_text:
            state["narrative"] = intro_text
            state["world_context"] = f"故事开场：{intro_text}"

        state["active_tags"] = list(required_tags)

        if start_location_key:
            get_current_player(state)["current_location"] = start_location_key
            logger.info(f"ModuleLoader: 初始位置 '{start_location_key}' 已设置")

        # 为新世界写入 WorldInitialized 事件（WorldManager.load_location 依赖事件流）
        try:
            from src.memory.event_store import create_event_store
            from src.state.read_models import StaticReadStore
            es = await create_event_store()
            store = StaticReadStore()
            locations = await store.get_all_locations(world_id=meta["world_id"])
            await es.append(
                world_id=world_id,
                event_type="WorldInitialized",
                data={
                    "module_name": module_name,
                    "locations": locations,
                    "start_location_key": start_location_key,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                source_node="module_loader",
            )
            await es.close()
            await store.close()
        except Exception as e:
            logger.warning(f"ModuleLoader: WorldInitialized 事件写入失败 (可手动补齐): {e}")

        logger.info(
            f"ModuleLoader: 模组 '{module_name}' 已载入 "
            f"world={world_id[:8]} tags={required_tags}"
        )
        return state

    async def delete_module(self, module_name: str) -> bool:
        """彻底删除模组：读模型行 + LightRAG 种子工作区"""
        from src.state.read_models import StaticReadStore
        from src.memory.vector_store import VectorStore
        from src.memory.event_store import EventStore

        meta = await StaticReadStore().get_module_meta(module_name)
        if not meta:
            logger.warning("delete_module: 模组 '%s' 不存在", module_name)
            return False

        seed_ws = meta["world_id"]

        # 清理读模型表（按 world_id 批量删）
        try:
            store = StaticReadStore()
            await store.clear_all(world_id=seed_ws)
            # 额外删 module_meta
            conn = await store._get_conn()
            await conn.execute(
                "DELETE FROM module_meta WHERE module_name = $1", module_name,
            )
            await store.close()
            logger.info("读模型已清理: world_id=%s", seed_ws)
        except Exception as e:
            logger.warning(f"读模型清理失败: {e}")

        # 清理 EventStore 中该 world 残留事件
        try:
            es = EventStore()
            await es.clear_world(seed_ws)
            logger.info("EventStore 已清理: world_id=%s", seed_ws)
        except Exception as e:
            logger.warning(f"EventStore 清理跳过: {e}")

        # 清理 LightRAG 种子工作区目录
        try:
            import shutil
            seed_dir = PROJECT_ROOT / "data" / "worlds" / seed_ws
            if seed_dir.exists():
                shutil.rmtree(seed_dir)
            logger.info("LightRAG 种子目录已清理: %s", seed_ws)
        except Exception as e:
            logger.warning(f"LightRAG 清理跳过: {e}")

        logger.info("模组已彻底删除: %s", module_name)
        return True

    async def load_opening_narrative(self, module_name: str) -> Optional[str]:
        """仅获取模组的开场文本（不创建游戏会话）"""
        from src.state.read_models import StaticReadStore
        store = StaticReadStore()
        try:
            meta = await store.get_module_meta(module_name)
            return meta.get("intro_text", "") if meta else None
        finally:
            await store.close()
