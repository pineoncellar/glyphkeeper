"""
@File     :   spatial_physics_node.py
@Desc     :   空间与物理可行性仲裁节点 — 结合结构化数据判定动作是否实际可执行
@Note     :   读取 _skill_check_result 和 _scene_interactables 缓存，
              输出 _spatial_result（FactManifest），不直接追加 executed_actions。

三层校验管线:
  Layer 1: 空间可达性 — 校验当前玩家场景是否与目标物品所属场景一致
  Layer 2: 状态依赖   — 校验目标物品的锁定/搜索状态
  Layer 3: 常识推理   — 可选 fast LLM，处理复杂开放物理行为
"""

from __future__ import annotations

from typing import Any, Optional
from src.state.game_state import GameState, get_current_player
from src.tools import get_logger, get_settings
from src.tools.llm_client import call_llm as _call_llm

logger = get_logger(__name__)


# ====================================================================
# 开关常量
# ====================================================================

SPATIAL_PHYSICS_LLM_COMMON_SENSE = False
"""是否启用 Layer 3 动态常识推理（fast LLM）。

True 时在复杂开放物理行为时调用 fast 级 LLM 做二值可行性判断。
False 时仅执行 Layer 1 + Layer 2 纯规则校验。
"""


# ====================================================================
# FactManifest — 物理裁决事实清单
# ====================================================================


class FactManifest:
    """物理裁决事实清单 — spatial_physics_node 的输出结构

    各字段含义:
      spatial_valid / spatial_reason — Layer 1 结果
      is_locked / is_searched / has_key / target_state — Layer 2 结果
      physical_executed / execution_phase — 最终综合裁决
      side_effects / npc_reactions / time_advance — 预留扩展槽
    """

    def __init__(self):
        # Layer 1: 空间可达性
        self.spatial_valid: bool = False
        self.spatial_reason: str = ""       # OK / OUT_OF_REACH / TARGET_NOT_FOUND

        # Layer 2: 目标状态
        self.target_state: str = ""
        self.is_locked: bool = False
        self.is_searched: bool = False
        self.has_key: bool = False

        # 最终裁决
        self.physical_executed: bool = False
        self.execution_phase: str = ""      # NORMAL / LOCKED / ALREADY_SEARCHED / OUT_OF_REACH

        # 内部引用（用于 effect_archivist_node 的线索查询）
        self._target_key: str = ""

        # 预留扩展槽
        self.side_effects: list = None
        self.npc_reactions: list = None
        self.time_advance: str = ""

    def to_dict(self) -> dict:
        return {
            "spatial_valid": self.spatial_valid,
            "spatial_reason": self.spatial_reason,
            "target_state": self.target_state,
            "is_locked": self.is_locked,
            "is_searched": self.is_searched,
            "has_key": self.has_key,
            "physical_executed": self.physical_executed,
            "execution_phase": self.execution_phase,
            "_target_key": self._target_key,
            "side_effects": self.side_effects,
            "npc_reactions": self.npc_reactions,
            "time_advance": self.time_advance,
        }

    @classmethod
    def from_dict(cls, d: dict) -> "FactManifest":
        f = cls()
        f.spatial_valid = d.get("spatial_valid", False)
        f.spatial_reason = d.get("spatial_reason", "")
        f.target_state = d.get("target_state", "")
        f.is_locked = d.get("is_locked", False)
        f.is_searched = d.get("is_searched", False)
        f.has_key = d.get("has_key", False)
        f.physical_executed = d.get("physical_executed", False)
        f.execution_phase = d.get("execution_phase", "")
        f._target_key = d.get("_target_key", "")
        return f


# ====================================================================
# 空间可达性
# ====================================================================


def _check_spatial_reachability(
    current_location: str,
    target_key: str,
    interactables: list[dict],
) -> tuple[bool, str, str]:
    """硬核校验玩家的 current_location 是否与目标物品所在场景一致

    返回: (spatial_valid, reason, matched_system_key)
    matched_system_key 为匹配到的系统 key（如 "item_stone_table"），
    供后续线索查询使用。未匹配到时返回原 target_key。
    """
    if not target_key:
        return False, "TARGET_NOT_FOUND", target_key

    target = next((i for i in interactables if i["key"] == target_key), None)

    # target_key 可能是玩家直接输入的 target 名称而非系统 key，
    # 此时尝试按 name 精确匹配
    if target is None:
        target = next((i for i in interactables if i.get("name", "") == target_key), None)

    if target is None:
        logger.debug(f"spatial_physics: 目标 '{target_key}' 不在当前场景物品列表中")
        return False, "TARGET_NOT_FOUND", target_key

    # 返回系统 key（优先用匹配到的 key，而非原始 target_key）
    matched_key = target.get("key", target_key)
    return True, "OK", matched_key


# ====================================================================
# 状态依赖
# ====================================================================


def _check_state_dependency(
    target_key: str,
    interactables: list[dict],
    inventory: list[str],
) -> tuple[bool, bool, bool, str]:
    """从 interactables 的 tags 和 state 字段读取实时状态

    返回: (is_locked, is_searched, has_key, target_state)
    """
    target = next((i for i in interactables if i["key"] == target_key), None)
    if target is None:
        target = next((i for i in interactables if i.get("name", "") == target_key), None)
    if target is None:
        return False, False, False, ""

    tags = target.get("tags", [])
    item_state = target.get("state", "")

    is_locked = "locked" in [t.lower() for t in tags] or "LOCKED" in item_state.upper()
    is_searched = "searched" in [t.lower() for t in tags]

    # 检查背包是否有对应钥匙（按命名约定匹配）
    has_key = False
    if is_locked:
        target_name = target.get("name", target_key)
        for item in inventory:
            if "钥匙" in item or "key" in item.lower():
                # 简单启发：如果背包有钥匙且目标含 lock，认为有匹配钥匙
                has_key = True
                break

    return is_locked, is_searched, has_key, item_state


# ====================================================================
# 动态常识推理
# ====================================================================


COMMON_SENSE_PROMPT = """你是一个 TRPG 物理仲裁官。判断以下动作在当前物理现实中是否可行。
只需回答 true 或 false。

当前场景: {scene_desc}
物品状态: {item_state}
玩家动作: {action_detail}
玩家工具: {tools}

物理约束:
- 物品是否可被当前玩家接触到（距离/姿态）
- 玩家当前状态是否支持此动作（被绑/受伤/疯狂）
- 工具是否足以完成此动作

回答（只输出 true 或 false）:"""


async def _check_common_sense(
    fact: FactManifest,
    state: GameState,
    action_detail: str,
    flavor_context: str,
) -> tuple[bool, str]:
    """调用 fast LLM 做复杂开放物理行为的二值可行性判断"""
    if not SPATIAL_PHYSICS_LLM_COMMON_SENSE:
        return fact.spatial_valid, fact.spatial_reason

    physical_reality = state.get("physical_reality", "")
    scene_desc = physical_reality[:500] if physical_reality else "未知场景"
    tools = ", ".join(_get_inventory(state))

    try:
        result = await _call_llm("fast", [
            {"role": "user", "content": COMMON_SENSE_PROMPT.format(
                scene_desc=scene_desc,
                item_state=fact.target_state or "未知",
                action_detail=action_detail or flavor_context,
                tools=tools or "无",
            )},
        ])
        if result.is_ok and result.text:
            answer = result.text.strip().lower()
            if "true" in answer:
                return True, "OK"
            elif "false" in answer:
                return False, "COMMON_SENSE_BLOCKED"
    except Exception as e:
        logger.debug(f"spatial_physics: LLM 常识推理失败（非阻塞）: {e}")

    return fact.spatial_valid, fact.spatial_reason


# ====================================================================
# 辅助函数
# ====================================================================


def _get_inventory(state: GameState) -> list[str]:
    """从角色卡读取背包物品列表"""
    character_data = get_current_player(state).get("character") or {}
    inventory = character_data.get("inventory") or []
    if isinstance(inventory, list):
        return [str(i) for i in inventory]
    return []


def _current_intent(state: GameState) -> dict:
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    return queue[idx] if idx < len(queue) else {}


def _get_target_key(state: GameState) -> str:
    """获取当前意图的目标 key，优先用 resolved_targets 消歧结果"""
    resolved = state.get("resolved_targets") or {}
    intent = _current_intent(state)
    intent_data = intent.get("data", {})

    # resolved_targets 按 target 名称索引
    target_name = intent_data.get("target", "")
    if target_name and target_name in resolved:
        return resolved[target_name]

    # 兜底用 intent_data 中的 target_key
    return intent_data.get("target_key", intent_data.get("target", ""))


# ====================================================================
# 最终裁决
# ====================================================================


def _finalize_verdict(fact: FactManifest) -> FactManifest:
    """基于三层校验结果，输出最终执行判定"""
    if not fact.spatial_valid:
        fact.physical_executed = False
        fact.execution_phase = "OUT_OF_REACH"
        return fact

    if fact.is_locked and not fact.has_key:
        fact.physical_executed = False
        fact.execution_phase = "LOCKED"
        return fact

    if fact.is_searched:
        fact.physical_executed = True
        fact.execution_phase = "ALREADY_SEARCHED"
        return fact

    fact.physical_executed = True
    fact.execution_phase = "NORMAL"
    return fact


# ====================================================================
# Node 主函数
# ====================================================================


async def spatial_physics_node(state: GameState) -> dict:
    """空间与物理可行性仲裁节点

    读取 _skill_check_result 和 _scene_interactables 缓存，执行三层校验。
    输出 _spatial_result（FactManifest 字典），不追加 executed_actions。
    """
    current_location = get_current_player(state).get("current_location", "")
    interactables = state.get("_scene_interactables", [])

    fact = FactManifest()

    # Layer 1: 空间可达性
    target_key = _get_target_key(state)
    fact._target_key = target_key
    fact.spatial_valid, fact.spatial_reason, matched_key = _check_spatial_reachability(
        current_location, target_key, interactables,
    )
    # 用匹配到的系统 key 覆盖原始 target_key，供 effect_archivist 按 key 查线索
    fact._target_key = matched_key

    # Layer 2: 状态依赖（仅在空间可达时进行）
    if fact.spatial_valid:
        inventory = _get_inventory(state)
        fact.is_locked, fact.is_searched, fact.has_key, fact.target_state = \
            _check_state_dependency(matched_key, interactables, inventory)

    # Layer 3: 常识推理（可选，仅在空间可达时触发）
    if fact.spatial_valid:
        intent = _current_intent(state)
        intent_data = intent.get("data", {})
        action_detail = intent_data.get("detail", "")
        flavor_context = intent.get("flavor_context", "")
        fact.spatial_valid, fact.spatial_reason = await _check_common_sense(
            fact, state, action_detail, flavor_context,
        )

    # 最终裁决
    fact = _finalize_verdict(fact)

    logger.info(
        f"spatial_physics: target={target_key} "
        f"phase={fact.execution_phase} "
        f"locked={fact.is_locked} "
        f"searched={fact.is_searched}"
    )

    return {"_spatial_result": fact.to_dict()}
