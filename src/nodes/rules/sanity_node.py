"""
@File     :   sanity_node.py
@Desc     :   理智规则节点 — 执行理智检定与疯狂判定
@Note     :   100% 确定性逻辑，调用 domain/sanity_rules.py

Node 签名:
    async def sanity_node(state: GameState) -> dict:
        从 intent 提取 SAN 损失参数 → 计算损失 → 检查疯狂 → 返回 resolution
"""

from __future__ import annotations

from typing import Any
from src.state.game_state import GameState
from src.domain.sanity_rules import (
    calculate_sanity_loss,
    roll_full_insanity,
    get_sanity_loss_bounds,
    SanityLoss,
    InsanityResult,
)
from src.tools import get_logger

logger = get_logger(__name__)


async def sanity_node(state: GameState) -> dict:
    """
    理智检定节点。

    从 intent_queue 读取当前理智检定参数，执行计算。
    结果以 executed_actions 追加，SAN 变更写入 deterministic_changes。
    """
    idx = state.get("current_intent_idx", 0)
    queue = state.get("intent_queue", [])
    current_intent = queue[idx] if idx < len(queue) else {}
    intent_data = current_intent.get("data", {})

    source_type = intent_data.get("source_type", "")
    character_data = state.get("character")

    current_san = intent_data.get("current_san")
    if current_san is None and character_data:
        current_san = character_data.get("sanity")

    max_san = intent_data.get("max_san")
    if max_san is None and character_data:
        max_san = character_data.get("max_sanity")

    if current_san is None:
        current_san = intent_data.get("current_san", 60)
    if max_san is None:
        max_san = intent_data.get("max_san", 60)

    loss_range_raw = intent_data.get("loss_range")
    if loss_range_raw and isinstance(loss_range_raw, (list, tuple)) and len(loss_range_raw) >= 2:
        loss_range = (int(loss_range_raw[0]), int(loss_range_raw[1]))
    else:
        loss_range = get_sanity_loss_bounds(source_type)

    is_mythos = intent_data.get("is_mythos", False)

    try:
        sanity_loss = calculate_sanity_loss(current_san, max_san, loss_range, is_mythos)
        insanity_result = roll_full_insanity(current_san, max_san, loss_range, source_type)
    except Exception as e:
        logger.error(f"sanity_node: 理智计算失败: {e}")
        return {
            "executed_actions": [{
                "intent_id": f"intent_{idx}",
                "intent_type": current_intent.get("type", "META"),
                "rule_context": {"success": False, "error": str(e), "source_type": source_type},
                "deterministic_changes": {},
                "raw_fixed_text": "",
                "flavor_context": current_intent.get("flavor_context", ""),
            }],
        }

    new_san = max(0, current_san - sanity_loss.actual_loss)

    resolution = {
        "success": True,
        "node_type": "sanity_check",
        "source_type": source_type,
        "current_san": current_san,
        "max_san": max_san,
        "loss_range": list(loss_range),
        "actual_loss": sanity_loss.actual_loss,
        "max_possible_loss": sanity_loss.max_possible_loss,
        "is_temporary_insanity": sanity_loss.is_temporary_insanity,
        "is_indefinite_insanity": sanity_loss.is_indefinite_insanity,
        "insanity_type": insanity_result.insanity_type,
        "duration_hours": insanity_result.duration_hours,
        "symptom": insanity_result.symptom,
        "remaining_san": new_san,
    }

    log_msg = (
        f"sanity_node: {source_type} "
        f"SAN {current_san} → {new_san} "
        f"(loss={resolution['actual_loss']})"
    )
    if resolution["insanity_type"] != "none":
        log_msg += f" [{resolution['insanity_type']}] {resolution['symptom']}"
    logger.info(log_msg)

    changes = {}
    if character_data:
        changes["character"] = {**character_data, "sanity": new_san}

    return {
        "executed_actions": [{
            "intent_id": f"intent_{idx}",
            "intent_type": current_intent.get("type", "META"),
            "rule_context": resolution,
            "deterministic_changes": changes,
            "raw_fixed_text": "",
            "flavor_context": current_intent.get("flavor_context", ""),
        }],
    } | ({"character": changes.get("character")} if changes else {})


async def simple_sanity_check(
    current_san: int,
    max_san: int,
    source_type: str = "seeing_mythos_creature",
    loss_range: tuple[int, int] | None = None,
) -> dict:
    """
    快捷理智检定 — 不依赖 GameState。
    """
    if loss_range is None:
        loss_range = get_sanity_loss_bounds(source_type)
    result = roll_full_insanity(current_san, max_san, loss_range, source_type)
    return {
        "success": True,
        "source_type": source_type,
        "current_san": current_san,
        "actual_loss": result.actual_loss if hasattr(result, 'actual_loss') else 0,
        "insanity_type": getattr(result, 'insanity_type', 'none'),
        "symptom": getattr(result, 'symptom', ''),
        "remaining_san": current_san - (result.actual_loss if hasattr(result, 'actual_loss') else 0),
    }
