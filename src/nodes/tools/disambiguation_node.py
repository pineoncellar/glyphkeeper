# -*- coding: utf-8 -*-
"""
@File     :   disambiguation_node.py
@Desc     :   实体对齐节点 - 将玩家模糊称呼映射为系统唯一 ID
@Note     :   按意图类型选择策略路由，三级降级匹配：精确文本/向量余弦/LLM 兜底
              消费 intent.data.target / detail，写入 state.resolved_targets
"""

from __future__ import annotations

import json
import re
from typing import Optional, Any
import numpy as np

from src.state.game_state import GameState
from src.tools import get_logger, get_settings
from src.tools.llm_client import call_llm as _call_llm, LLMResult

logger = get_logger(__name__)


# ====================================================================
# 全局缓存 — 向量嵌入函数懒加载
# ====================================================================

_embedding_func: Optional[Any] = None


async def _get_embedding_func():
    """获取与 LightRAG 共享的 embedding 函数（复用同一管道确保维度一致）"""
    global _embedding_func
    if _embedding_func is None:
        from src.memory.vector_store import VectorStore
        vs = await VectorStore.get_instance(domain="world")
        _embedding_func = vs._create_embedding_func()
    return _embedding_func


# ====================================================================
# 意图→目标类型映射表
# ====================================================================

_INTENT_TARGET_MAP: dict[str, str] = {
    "PHYSICAL_INTERACT": "item",         # 调查/交互 → 当前场景 interactables
    "MOVE": "location",                  # 移动 → locations.exits 映射表
    "COMBAT_ACTION": "npc",             # 战斗 → recent_actors 焦点栈 + 场景 NPC
    "SOCIAL_INTERACT": "npc",           # 对话 → 同上
    "USE_ITEM": "inventory_item",       # 使用物品 → 玩家背包
    "META": "none",                     # 元操作无需消歧
}


def _resolve_target_type(intent_type: str) -> str:
    """根据意图类型返回目标 ID 类型"""
    return _INTENT_TARGET_MAP.get(intent_type, "none")


# ====================================================================
# 对比域构建 — 按类型拉取候选实体列表
# ====================================================================


async def _build_candidates(
    target_type: str,
    state: GameState,
) -> list[dict]:
    """根据目标类型构建候选实体列表"""
    candidates: list[dict] = []

    if target_type == "npc":
        # 候选 NPC 来源：当前场景 NPC + 对话历史 NPC
        scene_npcs = state.get("scene_npcs") or []
        npc_relations = state.get("npc_relations") or {}
        entity_name_map = state.get("entity_name_map") or {}

        seen = set()
        for entity_key in scene_npcs:
            if entity_key not in seen:
                # 优先使用 entity_name_map 中的显示名（如"托马斯·金博尔"），
                # 兜底使用系统 key——确保 Level 1/2 匹配能命中用户自然语言输入
                display_name = entity_name_map.get(entity_key, entity_key)
                candidates.append({"id": entity_key, "name": display_name, "source": "scene"})
                seen.add(entity_key)
        for name in npc_relations:
            if name not in seen:
                # npc_relations 中可能已经是显示名或 key，不做转换
                candidates.append({"id": name, "name": name, "source": "history"})
                seen.add(name)

        # 如果 attention_focus 有 actors，优先排在前面
        focus = state.get("attention_focus") or {}
        recent = focus.get("recent_actors") or []
        if recent:
            ranked = []
            focus_set = set(recent)
            ranked.extend(c for c in candidates if c["id"] in focus_set)
            ranked.extend(c for c in candidates if c["id"] not in focus_set)
            candidates = ranked

    elif target_type == "item":
        # 从 physical_reality XML 中解析场景物品
        pr = state.get("physical_reality") or state.get("world_context") or ""
        names = re.findall(r"<items>(.*?)</items>", pr)
        if names:
            items = [n.strip() for n in names[0].split(";") if n.strip()]
            for item in items:
                # 去除可能的 (含线索) 后缀
                clean = re.sub(r"\s*\(含线索\)", "", item)
                candidates.append({"id": clean, "name": clean, "source": "scene"})

    elif target_type == "location":
        # 从 physical_reality XML 中解析出口映射
        pr = state.get("physical_reality") or state.get("world_context") or ""
        exits_match = re.findall(r"<exits>(.*?)</exits>", pr)
        if exits_match:
            # 格式: north(loc_01), east(loc_02)
            parts = exits_match[0].split(",")
            for part in parts:
                part = part.strip()
                m = re.match(r"(\w+)\((\w+)\)", part)
                if m:
                    dir_name = m.group(1)
                    loc_key = m.group(2)
                    candidates.append({"id": loc_key, "name": f"{dir_name}({loc_key})", "source": "exit"})

    elif target_type == "inventory_item":
        # 从角色背包获取（当前未实现完整背包系统）
        character = state.get("character") or {}
        inventory = character.get("inventory") or []
        for item in inventory:
            candidates.append({"id": item, "name": item, "source": "inventory"})

    return candidates


# ====================================================================
# 三级降级匹配
# ====================================================================


def _fuzzy_text_match(raw: str, candidates: list[dict]) -> Optional[dict]:
    """第一级：精确/子串文本匹配

    先检查精确匹配，再检查原始名称是否在候选名的中间或尾部出现。
    优先选最长的匹配项（更完整）。
    """
    if not raw or not candidates:
        return None

    # 精确匹配
    for c in candidates:
        if c["name"] == raw or c["id"] == raw:
            return {**c, "confidence": 1.0, "method": "exact"}

    # 子串包含匹配 — 选最长的候选名
    best: Optional[dict] = None
    best_len = 0
    for c in candidates:
        if raw in c["name"] or raw in c["id"]:
            match_len = max(len(c.get("name", "")), len(c.get("id", "")))
            if match_len > best_len:
                best = {**c, "confidence": 0.9, "method": "substring"}
                best_len = match_len
        elif c["name"] in raw or c["id"] in raw:
            # 反向匹配：玩家提到了更完整的名称（如"金博尔先生"），
            # 而候选名是缩写（如"金博尔"）→ 以玩家为准
            return {**c, "confidence": 0.95, "method": "substring_reverse"}

    return best


async def _vector_match(raw: str, candidates: list[dict]) -> Optional[dict]:
    """第二级：向量余弦相似度匹配

    对玩家原始输入做 embedding，与候选名在向量空间中比较。
    复用 LightRAG 的 embedding 管道，确保全局维度一致。
    超过阈值即锁定。
    """
    if not raw or not candidates:
        return None

    try:
        embed_func = await _get_embedding_func()
        raw_vec = await embed_func([raw])
        raw_vec = np.array(raw_vec).flatten()

        # 对每个候选名计算余弦相似度
        best: Optional[dict] = None
        best_score = -1.0
        threshold = 0.75  # 余弦相似度阈值，可在 config.yaml 中调整

        for c in candidates:
            c_vec = await embed_func([c["name"]])
            c_vec = np.array(c_vec).flatten()
            dot = np.dot(raw_vec, c_vec)
            norm = np.linalg.norm(raw_vec) * np.linalg.norm(c_vec)
            score = float(dot / norm) if norm > 0 else 0.0

            if score > best_score:
                best_score = score
                if score >= threshold:
                    best = {**c, "confidence": round(score, 4), "method": "vector"}

        return best

    except Exception as e:
        logger.debug(f"向量匹配失败: {e}")
        return None


_ENTITY_LLM_PROMPT = """你是 TRPG 系统中的实体链接器。请将玩家口中的模糊称呼精确映射到当前候选实体。

<player_input>
{player_input}
</player_input>

<extracted_target>
{extracted_target}
</extracted_target>

<target_type>
{target_type}
</target_type>

<candidates>
{candidates}
</candidates>

<attention_focus>
{attention_focus}
</attention_focus>

从候选列表中选择最匹配的实体 ID 并返回。若完全无法确定，返回空字符串。
仅输出实体 ID，不要附加任何解释或格式标记。"""


async def _llm_fallback(
    raw: str,
    candidates: list[dict],
    state: GameState,
    target_type: str,
) -> Optional[dict]:
    """第三级：轻量 LLM 推理兜底

    携带 attention_focus 上下文处理复杂指代（如"刚才那个坏蛋"）。
    使用 fast 模型，强制输出单实体 ID。
    """
    if not raw or not candidates:
        return None

    try:
        candidate_lines = "\n".join(
            f"  - {c['id']} ({c['name']})" for c in candidates[:10]
        )
        focus = state.get("attention_focus") or {}
        focus_text = json.dumps(focus, ensure_ascii=False) if focus else "（无焦点）"

        messages = [
            {"role": "system", "content": _ENTITY_LLM_PROMPT.format(
                player_input=state.get("player_input", ""),
                extracted_target=raw,
                target_type=target_type,
                candidates=candidate_lines,
                attention_focus=focus_text,
            )},
            {"role": "user", "content": f"请将【{raw}】映射为候选列表中的实体 ID。"},
        ]
        result = await _call_llm("fast", messages)
        if result.is_ok and result.text:
            resolved = result.text.strip().strip('"').strip("'").strip("。")
            for c in candidates:
                if c["id"] == resolved or c["name"] == resolved:
                    logger.info(f"LLM 消歧: {raw} → {resolved}")
                    return {**c, "confidence": 0.85, "method": "llm"}

    except Exception as e:
        logger.debug(f"LLM 消歧失败: {e}")

    return None


# ====================================================================
# 三级降级主入口
# ====================================================================


async def _resolve_entity(
    raw: str,
    target_type: str,
    candidates: list[dict],
    state: GameState,
) -> Optional[dict]:
    """
    三级降级匹配，任意一级命中即返回。
    先精确文本匹配，再向量余弦相似度，最后 LLM 兜底。
    """
    # 第一级：文本匹配
    result = _fuzzy_text_match(raw, candidates)
    if result:
        logger.debug(f"消歧 [{target_type}]: {raw} → {result['id']} (method={result['method']})")
        return result

    # 第二级：向量匹配
    result = await _vector_match(raw, candidates)
    if result:
        logger.debug(f"消歧 [{target_type}]: {raw} → {result['id']} (method=vector, conf={result['confidence']})")
        return result

    # 第三级：LLM 兜底
    result = await _llm_fallback(raw, candidates, state, target_type)
    if result:
        logger.debug(f"消歧 [{target_type}]: {raw} → {result['id']} (method=llm)")
        return result

    return None


# ====================================================================
# attention_focus 维护
# ====================================================================


def _push_attention_focus(
    current_focus: Optional[dict],
    entity_id: str,
    target_type: str,
    max_actors: int = 5,
    max_objects: int = 5,
) -> dict:
    """向 attention_focus LIFO 栈推入最新交互实体

    recent_actors 和 recent_objects 各自维护一个定长栈，
    后进先出：最近交互过的实体优先排在栈顶。
    """
    focus = dict(current_focus or {})
    actors = list(focus.get("recent_actors") or [])
    objects = list(focus.get("recent_objects") or [])

    if target_type == "npc":
        if entity_id in actors:
            actors.remove(entity_id)  # 去重后移到栈顶
        actors.insert(0, entity_id)
        actors[:] = actors[:max_actors]
    elif target_type in ("item", "inventory_item"):
        if entity_id in objects:
            objects.remove(entity_id)
        objects.insert(0, entity_id)
        objects[:] = objects[:max_objects]

    return {"recent_actors": actors, "recent_objects": objects}


# ====================================================================
# Node 主函数
# ====================================================================


async def disambiguation_node(state: GameState) -> dict:
    """
    实体对齐节点 — 主入口。

    执行流：
      从 intent 提取玩家目标称呼和意图类型
      根据意图类型确定目标 ID 类型（item/npc/location/inventory_item）
      从 state 构建候选实体列表（按类型就近拉取）
      三级降级匹配找到系统唯一 ID
      写入 resolved_targets + attention_focus

    返回 dict:
      - resolved_targets: {primary_id, secondary_id, target_type}
      - attention_focus: 更新后的焦点栈
      - scene_npcs: 透传 db_lookup 已有的场景 NPC 信息
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    intent_type = intent.get("type", "")

    # 提取原始称呼
    raw_target = intent_data.get("target", "")
    if not raw_target:
        raw_target = intent_data.get("detail", "")
    if not raw_target:
        raw_target = intent_data.get("action", "")

    target_type = _resolve_target_type(intent_type)

    patch: dict = {}

    if target_type == "none" or not raw_target:
        # 无需消歧 — 清空 resolved_targets
        patch["resolved_targets"] = {"primary_id": raw_target, "secondary_id": None, "target_type": target_type}
        return patch

    # 构建候选列表
    candidates = await _build_candidates(target_type, state)

    if not candidates:
        logger.debug(f"disambiguation_node: 无候选实体 (type={target_type})")
        patch["resolved_targets"] = {"primary_id": raw_target, "secondary_id": None, "target_type": target_type}
        return patch

    # 三级降级匹配
    resolved = await _resolve_entity(raw_target, target_type, candidates, state)

    if resolved:
        primary_id = resolved["id"]
        confidence = resolved.get("confidence", 0.0)
        method = resolved.get("method", "unknown")

        patch["resolved_targets"] = {
            "primary_id": primary_id,
            "secondary_id": None,
            "target_type": target_type,
            "disambiguation_meta": {
                "method": method,
                "confidence": confidence,
            },
        }

        # 更新 attention_focus
        new_focus = _push_attention_focus(
            state.get("attention_focus"),
            primary_id,
            target_type,
        )
        patch["attention_focus"] = new_focus

        logger.info(
            f"disambiguation: {raw_target} → {primary_id} "
            f"(type={target_type}, method={method}, conf={confidence})"
        )
    else:
        # 全部降级失败 — 保底返回原始称呼
        patch["resolved_targets"] = {
            "primary_id": raw_target,
            "secondary_id": None,
            "target_type": target_type,
            "disambiguation_meta": {
                "method": "fallback",
                "confidence": 0.0,
            },
        }
        logger.info(f"disambiguation: {raw_target} 无法消歧，保持原样")

    return patch
