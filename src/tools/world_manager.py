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


async def seed_world_lightrag(world_id: str, module_name: str) -> bool:
    """将模组的叙事知识（右脑数据）播种到新世界的 LightRAG

    /start 新建世界后调用，确保新世界拥有模组基线知识，
    而不含任何旧游戏的运行时状态。
    """
    from src.tools.ingestion import load_json, find_module_files

    # 找到对应模组的 JSON 文件
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

    # 复用 ModuleIngestor 的右脑管线逻辑
    from src.tools.ingestion import ModuleIngestor
    from src.memory.vector_store import VectorStore

    vs = await VectorStore.get_instance(domain="world", world_id=world_id)
    ingestor = ModuleIngestor(vector_store=vs)
    ok = await ingestor._ingest_right_brain(data, module_name)
    if ok:
        logger.info(f"seed_world_lightrag: 模组 '{module_name}' 已播种到 world '{world_id}'")
    else:
        logger.error(f"seed_world_lightrag: 播种失败 world={world_id} module={module_name}")
    return ok
