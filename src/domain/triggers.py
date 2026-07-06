# -*- coding: utf-8 -*-
"""
@File     :   triggers.py
@Desc     :   条件-动作触发器求值引擎 — 纯确定性 DSL，零 LLM 污染
@Note     :   依赖 _CONDITION_HANDLERS 映射表做原子匹配，支持 AND/OR/NOT 复合嵌套。
              evaluate_conditions 和 compile_patches 均无副作用，只做求值与 patch 组装。
              MAX_TRIGGERS_PER_TURN 防模组设计级联震荡死循环。
"""

from __future__ import annotations

import logging
from enum import Enum
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

logger = logging.getLogger(__name__)


# ====================================================================
# 常量
# ====================================================================

MAX_TRIGGERS_PER_TURN = 5
"""单轮最多触发 5 条触发器。超出的延迟至下轮评估，防级联死循环。"""


# ====================================================================
# 条件操作符枚举
# ====================================================================

class ConditionOp(Enum):
    """复合逻辑操作符，对应 conditions_json 顶层键"""
    AND = "AND"
    OR = "OR"
    NOT = "NOT"


# ====================================================================
# 求值上下文
# ====================================================================

@dataclass
class EvalContext:
    """触发器求值所需的全部外部数据

    state:          当前 GameState（或裁剪视图）
    session_id:     会话 ID（查 session_trigger_state 用）
    world_id:       世界 ID（查 static_triggers 用）
    read_store:     StaticReadStore 实例（可选，查 PG 读模型）
    knowledge_state: session_knowledge_state 表数据（可选）
    """
    state: dict
    session_id: str = ""
    world_id: str = ""
    read_store: Any = None
    knowledge_state: dict = field(default_factory=dict)


# ====================================================================
# 原子条件处理器映射表
# ====================================================================

def _check_has_item(ctx: EvalContext, params: dict) -> bool:
    """检查玩家背包是否持有指定物品"""
    item_key = params.get("key", "")
    if not item_key:
        return False
    players = ctx.state.get("players", {})
    for p in players.values():
        char = p.get("character") or {}
        inv = char.get("inventory") or []
        if item_key in inv:
            return True
    return False


def _check_at_location(ctx: EvalContext, params: dict) -> bool:
    """检查调查员是否在指定场景"""
    loc_key = params.get("key", "")
    if not loc_key:
        return False
    players = ctx.state.get("players", {})
    for p in players.values():
        if p.get("current_location", "") == loc_key:
            return True
    return False


def _check_sanity_below(ctx: EvalContext, params: dict) -> bool:
    """检查理智值是否低于阈值（含 max_sanity 缩放）"""
    threshold = params.get("threshold", 0)
    players = ctx.state.get("players", {})
    for p in players.values():
        char = p.get("character") or {}
        san = char.get("sanity", 99)
        max_san = char.get("max_sanity", 99)
        # 支持绝对值和百分比两种模式
        if params.get("mode") == "percent":
            ratio = san / max_san if max_san > 0 else 1.0
            if ratio < threshold / 100.0:
                return True
        else:
            if san < threshold:
                return True
    return False


def _check_knowledge_state(ctx: EvalContext, params: dict) -> bool:
    """检查指定的知识/线索是否已被发现"""
    knowledge_id = params.get("key", "")
    if not knowledge_id:
        return False
    discovered = ctx.knowledge_state.get("discovered", [])
    return knowledge_id in discovered


def _check_npc_alive(ctx: EvalContext, params: dict) -> bool:
    """检查 NPC 是否存活（entities 表中状态非 dead）"""
    npc_key = params.get("key", "")
    if not npc_key or not ctx.read_store:
        return False
    # 由调用方注入 read_store 的查询结果，此处不做异步查询
    npc_states = ctx.state.get("_npc_alive_cache", {})
    return npc_states.get(npc_key, True)


def _check_tag_active(ctx: EvalContext, params: dict) -> bool:
    """检查 active_tags 中是否含有指定标签"""
    tag = params.get("tag", "")
    if not tag:
        return False
    active_tags = ctx.state.get("active_tags", [])
    return tag in active_tags


def _check_combat_round_ge(ctx: EvalContext, params: dict) -> bool:
    """检查战斗轮次是否 ≥ 指定值"""
    n = params.get("n", 0)
    return ctx.state.get("combat_round", 0) >= n


def _check_global_flag(ctx: EvalContext, params: dict) -> bool:
    """检查模组级全局布尔锁"""
    flag_key = params.get("key", "")
    if not flag_key:
        return False
    flags = ctx.state.get("_global_flags", {})
    return flags.get(flag_key, False)


def _check_has_tag(ctx: EvalContext, params: dict) -> bool:
    """检查当前场景是否带有指定标签（与 _check_tag_active 不同，此检查场景 tags）"""
    tag = params.get("tag", "")
    if not tag:
        return False
    current_loc = ""
    players = ctx.state.get("players", {})
    for p in players.values():
        current_loc = p.get("current_location", "")
        break
    if not current_loc or not ctx.read_store:
        return False
    scene_tags = ctx.state.get("_scene_tags_cache", {}).get(current_loc, [])
    return tag in scene_tags


# 原子条件映射：所有新增条件类型只需向此 dict 注册一个新函数
_CONDITION_HANDLERS: dict[str, Callable[[EvalContext, dict], bool]] = {
    "HAS_ITEM":           _check_has_item,
    "AT_LOCATION":        _check_at_location,
    "SANITY_BELOW":       _check_sanity_below,
    "KNOWLEDGE_STATE":    _check_knowledge_state,
    "NPC_ALIVE":          _check_npc_alive,
    "TAG_ACTIVE":         _check_tag_active,
    "COMBAT_ROUND_GE":    _check_combat_round_ge,
    "GLOBAL_FLAG":        _check_global_flag,
    "HAS_TAG":            _check_has_tag,
}


# ====================================================================
# 条件求值器
# ====================================================================

def _evaluate_node(node: Any, ctx: EvalContext) -> bool:
    """递归求值单棵条件节点

    支持三种复合节点 AND/OR/NOT，叶子节点通过 _CONDITION_HANDLERS 映射。
    """
    if isinstance(node, bool):
        return node
    if not isinstance(node, dict):
        logger.debug(f"triggers: 无法识别的条件节点类型 {type(node)}，视为 False")
        return False

    # AND — 全部子条件为真才真
    and_clauses = node.get(ConditionOp.AND.value)
    if and_clauses is not None:
        return all(_evaluate_node(c, ctx) for c in and_clauses)

    # OR — 任一子条件为真即真
    or_clauses = node.get(ConditionOp.OR.value)
    if or_clauses is not None:
        return any(_evaluate_node(c, ctx) for c in or_clauses)

    # NOT — 取反
    not_clause = node.get(ConditionOp.NOT.value)
    if not_clause is not None:
        return not _evaluate_node(not_clause, ctx)

    # 原子条件：{type: "XXX", params: {...}}
    cond_type = node.get("type", "")
    handler = _CONDITION_HANDLERS.get(cond_type)
    if handler is None:
        logger.debug(f"triggers: 未知条件类型 '{cond_type}'，视为 False")
        return False
    return handler(ctx, node.get("params", {}))


def evaluate_conditions(conditions: dict, ctx: EvalContext) -> bool:
    """顶层条件求值入口

    接收 conditions_json 中任意复合或原子结构，
    返回 True 表示满足条件，可触发对应动作。

    用法:
        ok = evaluate_conditions(trigger["conditions_json"], ctx)
    """
    return _evaluate_node(conditions, ctx)


# ====================================================================
# 动作编译器
# ====================================================================

# Action type 常量
ACTION_MODIFY_LOCATION_DESC = "MODIFY_LOCATION_DESC"
ACTION_SPAWN_ITEM = "SPAWN_ITEM"
ACTION_GRANT_TAG = "GRANT_TAG"
ACTION_SET_GLOBAL_FLAG = "SET_GLOBAL_FLAG"
ACTION_APPEND_ECHO = "APPEND_ECHO"
ACTION_TRIGGER_ENDING = "TRIGGER_ENDING"
ACTION_GRANT_KNOWLEDGE = "GRANT_KNOWLEDGE"
ACTION_REMOVE_ITEM = "REMOVE_ITEM"


def compile_patches(actions: list[dict], trigger_id: str) -> dict:
    """将动作 JSON 列表编译为标准 state_patch

    返回结构:
      patch: dict     — 供 reduce_iter_node 消费的 state_patch
      echo_text: str  — 追加到 executed_actions[-1] 的风味文本
      ending_id: str  — 非空表示触发结团

    参数:
      actions:    actions_json 数组
      trigger_id: 来源触发器 ID（追踪溯源用）
    """
    patch: dict = {}
    echo_text = ""
    ending_id = ""

    for action in actions:
        atype = action.get("type", "")
        params = action.get("params", {})

        if atype == ACTION_MODIFY_LOCATION_DESC:
            # 记录位置描述变更，由 engine 层或 projector 写回 PG
            loc_key = params.get("location_key", "")
            suffix = params.get("new_desc_suffix", "")
            if loc_key and suffix:
                pending = patch.setdefault("_pending_desc_changes", {})
                pending[loc_key] = suffix

        elif atype == ACTION_SPAWN_ITEM:
            # 向指定场景掉落物品
            item_key = params.get("item_key", "")
            loc_key = params.get("location_key", "")
            if item_key and loc_key:
                spawns = patch.setdefault("_pending_item_spawns", {})
                spawns.setdefault(loc_key, []).append(item_key)

        elif atype == ACTION_GRANT_TAG:
            tag = params.get("tag", "")
            if tag:
                existing = patch.get("active_tags", [])
                if tag not in existing:
                    patch["active_tags"] = existing + [tag]

        elif atype == ACTION_SET_GLOBAL_FLAG:
            flag_key = params.get("key", "")
            if flag_key:
                flags = dict(patch.get("_global_flags", {}))
                flags[flag_key] = True
                patch["_global_flags"] = flags

        elif atype == ACTION_APPEND_ECHO:
            text = params.get("text", "")
            if text:
                echo_text = text

        elif atype == ACTION_TRIGGER_ENDING:
            ending_id = params.get("ending_id", trigger_id)
            patch["control"] = "SUSPEND_ENDING"
            patch["_ending_id"] = ending_id

        elif atype == ACTION_GRANT_KNOWLEDGE:
            knowledge_id = params.get("knowledge_id", "")
            if knowledge_id:
                granted = patch.setdefault("_pending_knowledge_grants", [])
                if knowledge_id not in granted:
                    granted.append(knowledge_id)

        elif atype == ACTION_REMOVE_ITEM:
            item_key = params.get("key", "")
            if item_key:
                patch["_inventory_remove"] = item_key

        else:
            logger.debug(f"triggers: 未知动作类型 '{atype}' (trigger={trigger_id})")

    return {
        "patch": patch,
        "echo_text": echo_text,
        "ending_id": ending_id,
    }


# ====================================================================
# 顶层触发器评估调度
# ====================================================================

def evaluate_triggers(
    triggers: list[dict],
    trigger_states: dict[str, dict],
    ctx: EvalContext,
) -> dict:
    """评估一批触发器，返回聚合后的 patch 和控制标记

    先按 priority 降序排序（高优先级的先求值），
    跳过已禁用和本轮已达触发次数的触发器，
    最后对命中的动作做 compile_patches 汇总。

    参数:
      triggers:      静态触发器列表（来自 static_triggers 表）
      trigger_states: 当前会话的触发器状态（来自 session_trigger_state）
      ctx:           求值上下文

    返回:
      {
        "patch": {...},                    # 汇总后的 state_patch
        "echo_text": "...",                # 风味文本（追加到本轮最后一条 action）
        "ending_id": "..." or "",          # 非空表示游戏结束
        "fired_triggers": ["id1", "id2"],  # 本轮触发的 trigger_id 列表
      }
    """
    patch: dict = {}
    echo_texts: list[str] = []
    ending_id = ""
    fired: list[str] = []
    triggered_count = 0

    # 按 priority 降序排序，高优先级先求值
    sorted_triggers = sorted(triggers, key=lambda t: t.get("priority", 0), reverse=True)

    for trigger in sorted_triggers:
        # 熔断：单轮触发已达上限
        if triggered_count >= MAX_TRIGGERS_PER_TURN:
            logger.warning(
                f"triggers: 单轮触发已达上限 {MAX_TRIGGERS_PER_TURN}，"
                f"剩余触发器延迟至下轮"
            )
            break

        tid = trigger.get("trigger_id", "")
        tstate = trigger_states.get(tid, {})

        # 跳过已禁用的
        if tstate.get("is_disabled", False):
            continue

        # 跳过本轮已触发的（防单轮同触发器重复触发）
        if tstate.get("fired_this_turn", 0) > 0:
            continue

        # 求值条件
        conditions = trigger.get("conditions_json", {})
        if not conditions:
            continue

        if not evaluate_conditions(conditions, ctx):
            continue

        # 条件满足，编译动作
        result = compile_patches(trigger.get("actions_json", []), tid)

        # 合并 patch
        for k, v in result["patch"].items():
            if k in patch:
                # 列表字段追加合并
                if isinstance(patch[k], list) and isinstance(v, list):
                    patch[k].extend(v)
                elif isinstance(patch[k], dict) and isinstance(v, dict):
                    patch[k].update(v)
                else:
                    patch[k] = v
            else:
                patch[k] = v

        if result["echo_text"]:
            echo_texts.append(result["echo_text"])

        if result["ending_id"]:
            ending_id = result["ending_id"]

        fired.append(tid)
        triggered_count += 1
        logger.info(f"triggers: 触发器 '{tid}' 命中，累计触发 {triggered_count}")

    return {
        "patch": patch,
        "echo_text": "\n".join(echo_texts),
        "ending_id": ending_id,
        "fired_triggers": fired,
    }
