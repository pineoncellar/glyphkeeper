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

    从 state["intent"] 中读取理智检定参数，执行计算。

    intent.data 期望字段:
        source_type: str       — 损失来源类型（如 "seeing_mythos_creature"）
        loss_range: list[int]  — 自定义损失范围 [min, max]（可选）
        current_san: int       — 当前 SAN 值（可选，默认从角色数据读取）
        max_san: int           — 最大 SAN 值（可选，默认从角色数据读取）
        is_mythos: bool        — 是否神话相关（默认 False）
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}

    source_type = intent_data.get("source_type", "")

    # ── 获取 SAN 值 ──
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

    # ── 获取损失范围 ──
    loss_range_raw = intent_data.get("loss_range")
    if loss_range_raw and isinstance(loss_range_raw, (list, tuple)) and len(loss_range_raw) >= 2:
        loss_range = (int(loss_range_raw[0]), int(loss_range_raw[1]))
    else:
        loss_range = get_sanity_loss_bounds(source_type)

    is_mythos = intent_data.get("is_mythos", False)

    # ── 执行计算 ──
    try:
        # 先计算 SanityLoss（含 actual_loss）
        sanity_loss = calculate_sanity_loss(current_san, max_san, loss_range, is_mythos)
        # 再获取 InsanityResult（含症状类型和持续时间）
        insanity_result = roll_full_insanity(current_san, max_san, loss_range, source_type)
    except Exception as e:
        logger.error(f"sanity_node: 理智计算失败: {e}")
        return {
            "resolution": {
                "success": False,
                "error": str(e),
                "source_type": source_type,
            },
        }

    # ── 构建结果 ──
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
        "remaining_san": current_san - sanity_loss.actual_loss,
    }

    # 构建状态 patch
    new_san = current_san - resolution["actual_loss"]

    log_msg = (
        f"sanity_node: {source_type} "
        f"SAN {current_san} → {new_san} "
        f"(loss={resolution['actual_loss']})"
    )
    if resolution["insanity_type"] != "none":
        log_msg += f" [{resolution['insanity_type']}] {resolution['symptom']}"
    logger.info(log_msg)

    # 更新角色 SAN 值
    patch = {"resolution": resolution}
    if character_data:
        patch["character"] = {
            **character_data,
            "sanity": new_san,
        }

    return patch


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
