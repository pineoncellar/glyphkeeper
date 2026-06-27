# -*- coding: utf-8 -*-
"""
@File     :   archivist_node.py
@Desc     :   线索查询节点 — 技能检定成功后解析目标并查线索
@Note     :   在 investigation_subgraph 中紧接 skill_node 运行

解析目标 key 的优先级:
  ① intent_node 已提供的 target_key（来自 LLM 映射）
  ② 按物品名（name）精确匹配 PG
  ③ LLM 模糊匹配（将玩家输入与场景物品列表比对）

Node 签名:
    async def archivist_node(state: GameState) -> dict:
        读取 intent + resolution → 解析 target key → 查线索 → 返回 archivist_result
"""

from __future__ import annotations

import json
from typing import Optional

from src.state.game_state import GameState
from src.tools import get_logger, get_settings
from src.tools.llm_client import call_llm as _call_llm, LLMResult

logger = get_logger(__name__)


# ── 开关常量 ──

ARCHIVIST_NODE_LLM_FALLBACK = True
"""LLM 模糊匹配目标 key 的开关。

True 时 name 精确匹配失败后会调用 fast 级 LLM 做语义模糊匹配。
False 时 name 匹配不上就直接返回，节省一次 LLM 调用。
"""


# ====================================================================
# LLM 提示词 — 目标解析
# ====================================================================

ARCHIVIST_LLM_PROMPT = """你是 CoC 守密人助手 — 目标解析器。
请将玩家输入的目标描述与当前场景中的物品列表进行匹配。

当前场景中可交互的物品:
{scene_items}

玩家目标: {target}

从以上列表中选择最匹配的一项。
如果玩家的目标明显是对应非物品目标（如 NPC、地点等），输出空 key。

输出格式（纯 JSON，不要包含代码块标记）:
{{"matched_key": "匹配到的系统 key，无匹配则填空字符串", "matched_name": "匹配到的物品名，无匹配则填空字符串"}}"""


# ====================================================================
# 读模型缓存
# ====================================================================

_store_cache: Optional["StaticReadStore"] = None  # noqa: F821


async def _get_store():
    global _store_cache
    if _store_cache is None:
        from src.state.read_models import StaticReadStore
        _store_cache = StaticReadStore()
    return _store_cache


# ====================================================================
# LLM 模糊匹配
# ====================================================================


async def _llm_resolve_target(target: str, scene_items: list[dict]) -> Optional[str]:
    """调用 fast 级 LLM 做目标模糊匹配

    scene_items: [{"key": "item_study_desk", "name": "旧书桌"}, ...]
    返回匹配到的 key，无匹配返回 None。
    """
    if not scene_items:
        return None

    item_lines = "\n".join(f"  - {i['name']} ({i['key']})" for i in scene_items)
    user_content = ARCHIVIST_LLM_PROMPT.format(scene_items=item_lines, target=target)

    try:
        result = await _call_llm("fast", [
            {"role": "user", "content": user_content},
        ])
        if result.is_ok and result.text:
            text = result.text.strip()
            # 清理可能的 markdown 代码块标记
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(text)
            matched_key = parsed.get("matched_key", "")
            if matched_key:
                logger.info(f"archivist_node[LLM]: '{target}' → '{matched_key}'")
                return matched_key
    except Exception as e:
        logger.debug(f"archivist_node: LLM 解析失败（非阻塞）: {e}")

    return None


# ====================================================================
# 目标解析
# ====================================================================


async def _resolve_target(
    target_key: str,
    target_name: str,
    current_location: str,
) -> Optional[str]:
    """将玩家目标解析为系统 key

    优先级:
      ① intent_node 已提供的 target_key（验证存在性）
      ② 按物品名（name）精确匹配 PG
      ③ LLM 模糊匹配（仅当有 target 描述且场景有物品时）
    """
    store = await _get_store()

    # 优先用 intent_node 给的 key
    if target_key:
        item = await store.get_interactable(target_key)
        if item:
            return target_key

    # 按 name 精确匹配
    if target_name and current_location:
        items = await store.get_interactables_by_location(current_location)
        for item in items:
            if item["name"] == target_name:
                logger.debug(f"archivist_node: name 精确匹配 '{target_name}' → '{item['key']}'")
                return item["key"]

        # name 也没匹配上 → LLM 模糊匹配（开关控制）
        if items and ARCHIVIST_NODE_LLM_FALLBACK:
            llm_key = await _llm_resolve_target(target_name, items)
            if llm_key:
                return llm_key

    return None


# ====================================================================
# Node 主函数
# ====================================================================


async def archivist_node(state: GameState) -> dict:
    """线索查询节点

    从 intent 获取 target 信息，在 resolution 显示检定成功时，
    解析目标 key 并调用 Archivist 查线索。结果写入 archivist_result。
    """
    resolution = state.get("resolution") or {}
    if not resolution.get("is_success"):
        return {"archivist_result": None}  # 状态：检定失败，跳过查线索

    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    session_id = state.get("session_id", "")
    current_location = state.get("current_location", "")
    character_data = state.get("character") or {}
    character_name = character_data.get("name", "")

    # 读取 target 信息
    target_key = intent_data.get("target_key", "")
    target_name = intent_data.get("target", "")
    skill_name = resolution.get("skill_name", "")
    roll_value = resolution.get("roll_value", 0)

    if not target_key and not target_name:
        logger.debug("archivist_node: 无目标信息")
        return {"archivist_result": None}

    # 解析目标 key
    resolved_key = await _resolve_target(target_key, target_name, current_location)
    if not resolved_key:
        logger.debug(f"archivist_node: 无法解析目标 '{target_name or target_key}'")
        return {"archivist_result": None}

    # 调用 Archivist 查线索
    try:
        from src.tools.archivist import Archivist
        archivist = Archivist()
        clue_result = await archivist.inspect_target(
            session_id=session_id,
            target_key=resolved_key,
            skill_name=skill_name,
            roll_value=roll_value,
            character_name=character_name,
        )
        if clue_result:
            logger.info(
                f"archivist_node: 线索发现! "
                f"knowledge={clue_result.get('knowledge_id')}"
            )
            return {"archivist_result": clue_result}
    except Exception as e:
        logger.warning(f"archivist_node: Archivist 调用失败: {e}")

    return {"archivist_result": None}
