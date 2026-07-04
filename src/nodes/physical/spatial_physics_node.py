"""
@File     :   spatial_physics_node.py
@Desc     :   空间与物理可行性仲裁节点 — 结合结构化数据判定动作是否实际可执行
@Note     :   读取 _skill_check_result 和 _scene_interactables 缓存，
              输出 _spatial_result（FactManifest），不直接追加 executed_actions。
              即兴降级：TARGET_NOT_FOUND 时走 Layer 3 常识 LLM 判断，
              批准则发放 IMPROMPTU 标识符，拒绝则维持 OUT_OF_REACH。
"""

from __future__ import annotations

import json
from typing import Any, Optional
from src.state.game_state import GameState, get_current_player
from src.tools import get_logger, get_settings
from src.tools.llm_client import call_llm as _call_llm

logger = get_logger(__name__)


# ====================================================================
# 开关常量
# ====================================================================

SPATIAL_PHYSICS_LLM_COMMON_SENSE = False
"""是否启用旧 Layer 3 动态常识推理（fast LLM）。

True 时在复杂开放物理行为时调用 fast 级 LLM 做二值可行性判断。
False 时仅执行 Layer 1 + Layer 2 纯规则校验。
"""

SPATIAL_PHYSICS_IMPROMPTU_JUDGMENT = True
"""是否启用即兴降级分支（Layer 3'）。

True 时当目标在 PG 静态表中不存在时，调用 fast LLM 判断是否
为合理的即兴交互（如捡石头、拨草等日常行为）。
False 时维持旧行为：TARGET_NOT_FOUND 直接 OUT_OF_REACH。
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


# ====================================================================
# 即兴降级分支
# ====================================================================

IMPTOMPTU_JUDGMENT_PROMPT = """你是 CoC 守密人助手 — 即兴交互仲裁官。
判断玩家在当前场景中是否能即兴产生该物品或进行该交互。
只需回答一个 JSON 对象，不要包含代码块标记。

当前场景描述:
{scene_desc}

玩家意图: {action_detail}

约束原则:
- 只有在场景中极其普遍、无秘密价值的背景物才算合理
  （如荒院碎石、路边野草、墙上灰尘、掉落的树枝）
- 模组未声明但具有明显价值的物品一律拒绝
  （如武器、珠宝、钥匙、关键道具、金钱、药品）
- 考虑场景氛围：废弃院子可以有碎石，但洁净的客厅不应该有

输出格式（纯 JSON）:
{{"approved": true/false, "item_name": "合理的物品名称（批准时填写，拒绝时填空字符串）", "reason": "简要原因"}}"""


async def _impromptu_judgment(
    state: GameState,
    target_str: str,
    action_detail: str,
) -> dict:
    """即兴降级裁决 — 判断玩家是否能即兴与环境交互产生物品

    只有 SPATIAL_PHYSICS_IMPROMPTU_JUDGMENT 为 True 时才真正调用 LLM。
    返回格式: {"approved": bool, "item_name": str, "reason": str}
    """
    if not SPATIAL_PHYSICS_IMPROMPTU_JUDGMENT:
        return {"approved": False, "item_name": "", "reason": "即兴降级已关闭"}

    physical_reality = state.get("physical_reality", "")
    scene_desc = physical_reality[:800] if physical_reality else "未知场景"

    try:
        result = await _call_llm("fast", [
            {"role": "user", "content": IMPTOMPTU_JUDGMENT_PROMPT.format(
                scene_desc=scene_desc,
                action_detail=action_detail or target_str,
            )},
        ])
        if result.is_ok and result.text:
            text = result.text.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                approved = bool(parsed.get("approved", False))
                item_name = str(parsed.get("item_name", ""))
                reason = str(parsed.get("reason", ""))
                logger.info(
                    f"spatial_physics[impromptu]: '{target_str}' -> "
                    f"{'approved' if approved else 'rejected'} ({reason})"
                )
                return {"approved": approved, "item_name": item_name, "reason": reason}
    except Exception as e:
        logger.debug(f"spatial_physics: 即兴裁决 LLM 调用失败（非阻塞）: {e}")

    return {"approved": False, "item_name": "", "reason": "LLM 调用失败，安全拒绝"}


# ====================================================================
# 背包物品操作倾向判定
# ====================================================================

INVENTORY_DISPOSITION_PROMPT = """你是 CoC 守密人助手 — 背包操作分类器。
判断玩家对背包里某件物品的操作倾向。
只需回答一个 JSON 对象，不要包含代码块标记。

玩家动作: {action_detail}
目标物品: {target_item}
场景描述: {scene_desc}

分类规则:
- CONSUME: 物品在动作后彻底消失/消耗（吃药、开枪、使用消耗品）
- DROP: 物品只是脱离玩家身体，仍留在当前场景中（扔地上、放桌上、插墙上）

输出格式（纯 JSON）:
{{"disposition": "CONSUME" 或 "DROP", "reason": "简要原因"}}"""


async def _inventory_disposition_judgment(
    state: GameState,
    target_str: str,
    action_detail: str,
) -> str:
    """判断背包物品的操作倾向：CONSUME（消耗消失）或 DROP（掉落场景）

    调用 fast LLM 做二值分类，不走即兴合理性判断（背包物品已确认持有）。
    默认安全值为 CONSUME（宁可消失不可凭空产生）。
    """
    if not SPATIAL_PHYSICS_IMPROMPTU_JUDGMENT:
        return "CONSUME"

    physical_reality = state.get("physical_reality", "")
    scene_desc = physical_reality[:500] if physical_reality else "未知场景"

    try:
        result = await _call_llm("fast", [
            {"role": "user", "content": INVENTORY_DISPOSITION_PROMPT.format(
                action_detail=action_detail or target_str,
                target_item=target_str,
                scene_desc=scene_desc,
            )},
        ])
        if result.is_ok and result.text:
            text = result.text.strip()
            text = text.removeprefix("```json").removeprefix("```").removesuffix("```").strip()
            parsed = json.loads(text)
            if isinstance(parsed, dict):
                disposition = str(parsed.get("disposition", "CONSUME")).upper()
                if disposition in ("CONSUME", "DROP"):
                    logger.info(
                        f"spatial_physics[disposition]: '{target_str}' -> {disposition}"
                    )
                    return disposition
    except Exception as e:
        logger.debug(f"spatial_physics: 倾向判定 LLM 失败（非阻塞，默认 CONSUME）: {e}")

    return "CONSUME"


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
    """基于校验结果输出最终执行判定

    即兴降级批准时 spatial_reason=IMPROMPTU_APPROVED，
    跳过锁定/已搜索检查直接标记执行阶段为 IMPROMPTU。
    """
    if not fact.spatial_valid:
        fact.physical_executed = False
        fact.execution_phase = "OUT_OF_REACH"
        return fact

    # 背包物品路径：玩家自有物品，直接放行
    # INVENTORY_ITEM → 消耗消失，INVENTORY_DROP → 掉落场景
    if fact.spatial_reason in ("INVENTORY_ITEM", "INVENTORY_DROP"):
        fact.physical_executed = True
        fact.execution_phase = "INVENTORY_CONSUME" if fact.spatial_reason == "INVENTORY_ITEM" else "INVENTORY_DROP"
        return fact

    # 即兴路径：已由 LLM 直接批准，不再检查状态依赖
    if fact.spatial_reason == "IMPROMPTU_APPROVED":
        fact.physical_executed = True
        fact.execution_phase = "IMPROMPTU"
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

    读取 _skill_check_result 和 _scene_interactables 缓存，执行仲裁。
    输出 _spatial_result（FactManifest 字典），不追加 executed_actions。

    仲裁流程分两条互斥路径:
      路径 A（静态物品）: Layer 1 命中 PG 物品 → Layer 2 状态依赖 → 旧 Layer 3
      路径 B（即兴降级）: Layer 1 未命中 → 即兴 LLM 判断 → 批准则 IMPROMPTU
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

    # 路径 A: 静态物品 — 目标在 PG 中存在
    if fact.spatial_valid:
        # Layer 2: 状态依赖
        inventory = _get_inventory(state)
        fact.is_locked, fact.is_searched, fact.has_key, fact.target_state = \
            _check_state_dependency(matched_key, interactables, inventory)

        # 旧 Layer 3: 常识推理（可选）
        intent = _current_intent(state)
        intent_data = intent.get("data", {})
        action_detail = intent_data.get("detail", "")
        flavor_context = intent.get("flavor_context", "")
        fact.spatial_valid, fact.spatial_reason = await _check_common_sense(
            fact, state, action_detail, flavor_context,
        )

    # 路径 B: 目标不在 PG 中 — 先查背包，再走即兴降级
    elif fact.spatial_reason == "TARGET_NOT_FOUND":
        intent = _current_intent(state)
        intent_data = intent.get("data", {})
        target_str = intent_data.get("target", target_key)
        action_detail = intent_data.get("detail", "")

        # 优先检查背包：玩家自己的物品不需要场景存在性验证
        inventory = _get_inventory(state)
        if target_str in inventory:
            fact.spatial_valid = True
            fact.spatial_reason = "INVENTORY_ITEM"
            fact._target_key = target_str
            # 调用 LLM 判断操作倾向（消耗消失 vs 掉落场景）
            disposition = await _inventory_disposition_judgment(
                state, target_str, action_detail,
            )
            if disposition == "DROP":
                fact.spatial_reason = "INVENTORY_DROP"
            logger.info(
                f"spatial_physics: 背包命中 '{target_str}' -> {disposition}"
            )
        else:
            # 背包也没有，走即兴 LLM 判断是否可从环境中获得
            judgment = await _impromptu_judgment(state, target_str, action_detail)
            if judgment.get("approved"):
                fact.spatial_valid = True
                fact.spatial_reason = "IMPROMPTU_APPROVED"
                fact._target_key = f"impromptu_{judgment.get('item_name', target_str)}"
                logger.info(
                    f"spatial_physics: 即兴批准 '{target_str}' -> "
                    f"{fact._target_key}"
                )
            else:
                logger.debug(
                    f"spatial_physics: 即兴拒绝 '{target_str}': "
                    f"{judgment.get('reason', '')}"
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
