# -*- coding: utf-8 -*-
"""
@File     :   world_manager.py
@Desc     :   多世界隔离管理 — 世界创建/切换/列表/删除 与 LightRAG 播种
@Note     :   每局 /start 自动生成新 world_id，确保数据完全隔离

使用方式:
    from src.tools.world_manager import generate_world_id, create_world, seed_world_lightrag, set_active_world
    wid = generate_world_id("mtest")
    await create_world(wid)
    await seed_world_lightrag(wid, "mtest")
    set_active_world(wid)
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from src.tools import get_logger, get_settings, PROJECT_ROOT

logger = get_logger(__name__)


# ------- 世界 ID 生成 -------


def generate_world_id(module_name: str) -> str:
    """从模组名 + 时间戳生成唯一世界 ID

    格式: {module_name}_{YYYYMMDD}_{HHMMSS}
    例: mtest_20260703_235959
    """
    now = datetime.now()
    return f"{module_name}_{now.strftime('%Y%m%d_%H%M%S')}"


# ------- 世界目录管理 -------


async def create_world(world_id: str) -> bool:
    """创建世界目录 data/worlds/{world_id}/，返回是否成功"""
    world_dir = PROJECT_ROOT / "data" / "worlds" / world_id
    try:
        world_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"World '{world_id}' 目录已创建: {world_dir}")
        return True
    except OSError as e:
        logger.error(f"World '{world_id}' 目录创建失败: {e}")
        return False


def list_worlds() -> list[str]:
    """列出所有已存在的世界目录（按名称排序）"""
    worlds_dir = PROJECT_ROOT / "data" / "worlds"
    if not worlds_dir.exists():
        return []
    return sorted(
        d.name for d in worlds_dir.iterdir()
        if d.is_dir() and not d.name.startswith(".")
    )


def set_active_world(world_id: str):
    """将 settings 中的 active_world 切换为目标世界

    运行时生效，不写回 config.yaml 文件。
    """
    settings = get_settings()
    old = settings.project.active_world
    settings.project.active_world = world_id
    logger.info(f"Active world: {old} → {world_id}")


async def delete_world(world_id: str) -> bool:
    """删除世界目录及所有数据（不可恢复）"""
    import shutil
    world_dir = PROJECT_ROOT / "data" / "worlds" / world_id
    if not world_dir.exists():
        logger.warning(f"World '{world_id}' 目录不存在")
        return False
    try:
        shutil.rmtree(world_dir)
        logger.info(f"World '{world_id}' 已删除")
        return True
    except OSError as e:
        logger.error(f"World '{world_id}' 删除失败: {e}")
        return False


# ------- LightRAG 播种 -------


async def _check_seed_exists(seed_ws: str) -> bool:
    """检查种子工作区在 PG 中是否有数据（查第一张表的行数）"""
    try:
        from src.tools.pg_manager import PgManager
        mgr = await PgManager.get_instance()
        if not mgr.available:
            await mgr.start()
        if not mgr.available:
            return False
        import asyncpg
        conn = await asyncpg.connect(mgr.uri)
        row = await conn.fetchval(
            "SELECT COUNT(*) FROM LIGHTRAG_VDB_ENTITY WHERE workspace=$1",
            seed_ws,
        )
        await conn.close()
        return (row or 0) > 0
    except Exception as e:
        logger.debug(f"_check_seed_exists({seed_ws}): {e}")
        return False


async def seed_world_lightrag(world_id: str, module_name: str) -> bool:
    """将模组的叙事知识播种到新世界的 LightRAG

    优先从种子工作区高速复制（PG 级 INSERT...SELECT，秒级完成），
    种子不存在时回退到从 JSON 重插 LLM 管线（约 5 分钟）。
    """
    from src.memory.vector_store import VectorStore

    target_vs = await VectorStore.get_instance(knowledge_space="world", world_id=world_id)
    seed_ws = VectorStore.seed_workspace_name(module_name)

    # 先检查种子工作区在 PG 中是否有数据
    seed_exists = await _check_seed_exists(seed_ws)

    if seed_exists:
        # 高速路径：PG 级跨 workspace 复制（几秒完成）
        logger.info(
            f"seed_world_lightrag: 从种子 '{seed_ws}' 高速复制到 '{world_id}'"
        )
        ok = await target_vs.copy_workspace_from(seed_ws)
        if ok:
            logger.info(
                f"seed_world_lightrag: 种子复制完成 "
                f"{seed_ws} → {world_id}"
            )
            return True
        logger.warning("seed_world_lightrag: 种子复制返回失败，回退到 JSON 重插")

    # 回退路径：从 JSON 重插 LLM 管线（慢速，约 5 分钟）
    logger.info(
        f"seed_world_lightrag: 种子不存在，回退到 JSON 重插 "
        f"world={world_id} module={module_name}"
    )
    from src.tools.ingestion import load_json, find_module_files, ModuleIngestor

    target_name = module_name.lower()
    json_path: Optional[Path] = None
    for fp in find_module_files():
        if fp.stem.lower() == target_name:
            json_path = fp
            break
    if json_path is None:
        logger.warning(f"seed_world_lightrag: 未找到模组 '{module_name}' 的 JSON 文件")
        return False

    data = load_json(json_path)
    if data is None:
        return False

    ingestor = ModuleIngestor(vector_store=target_vs)
    ok = await ingestor._ingest_right_brain(data, module_name)
    if ok:
        logger.info(f"seed_world_lightrag: JSON 重插完成 world='{world_id}'")
    else:
        logger.error(f"seed_world_lightrag: JSON 重插失败 world={world_id}")
    return ok


async def copy_static_data_to_world(world_id: str, module_name: str) -> dict[str, int]:
    """将种子工作区的全部静态蓝图数据复制到目标世界

    包含 locations/interactables/entities/clue_discoveries/knowledge_registry/static_triggers。
    在 /start 时调用，确保新世界拥有完整的模组数据。
    """
    from src.memory.vector_store import VectorStore
    from src.state.read_models import StaticReadStore

    seed_ws = VectorStore.seed_workspace_name(module_name)
    store = StaticReadStore()
    counts = await store.copy_static_data_to_world(seed_ws, world_id)
    if sum(counts.values()):
        logger.info(f"copy_static_data_to_world: {seed_ws} → {world_id} ({counts})")
    else:
        logger.debug(f"copy_static_data_to_world: 种子 '{seed_ws}' 无蓝图数据")
    return counts


async def copy_triggers_to_world(world_id: str, module_name: str) -> int:
    """将种子工作区的 static_triggers 复制到目标世界

    在 /start 时调用，确保新世界拥有模组预设的触发器定义。
    返回复制的触发器数量。
    """
    from src.memory.vector_store import VectorStore
    from src.state.read_models import StaticReadStore

    seed_ws = VectorStore.seed_workspace_name(module_name)
    store = StaticReadStore()
    count = await store.copy_triggers_to_world(seed_ws, world_id)
    if count:
        logger.info(f"copy_triggers_to_world: {seed_ws} → {world_id} ({count} 条)")
    else:
        logger.debug(f"copy_triggers_to_world: 种子 '{seed_ws}' 无触发器数据")
    return count
