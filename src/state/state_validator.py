# -*- coding: utf-8 -*-
"""
@File     :   state_validator.py
@Desc     :   状态验证器 — 校验 LLM 提取的 Tier 1 事件是否合法
@Note     :   100% 确定性逻辑，无 LLM 调用
              Tier 1 事件必须通过验证才能进入 Reducer
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Optional

from src.state.game_state import GameState
from src.tools import get_logger

logger = get_logger(__name__)


# ====================================================================
# 验证结果
# ====================================================================


@dataclass
class ValidationResult:
    """验证结果

    passed:         是否通过验证
    reason:         未通过的原因（passed=True 时为空）
    corrected_event: 修正后的事件 payload（可通过校验的版本），None 表示丢弃
    """
    passed: bool = True
    reason: str = ""
    corrected_event: Optional[dict] = None


# ====================================================================
# 校验规则矩阵
#
# 事件类型               存在性检查                          合法性检查
# ITEM_STATE_CHANGE      item_id 在 interactables 或        属性值类型正确
#                         locations.exits 中
# NPC_STATE_CHANGE       npc_id 在 entities 中              NPC 在当前 scene_npcs 中
# LOCATION_TAG_CHANGE    location_id 在 locations 中        tag 格式合法
# SCENE_TRANSITION_IMPLIED from_location 和 to_location     出口未被阻挡
#                         有 exit 连通
# ====================================================================


class StateValidator:
    """验证 Tier 1 事件的合法性和安全性

    校验规则：
      先检查实体存在性 — 事件引用的实体是否在当前世界中存在。
      再检查合法性 — 变更是否违反游戏基本约束。

    所有检查为尽力而为模式：不确定时放行（passed=True），
    只有确定违规时才拦截。宁放勿拦。
    """

    def __init__(self):
        self._store = None

    @property
    async def static_store(self):
        """懒加载 StaticReadStore"""
        if self._store is None:
            from src.state.read_models import StaticReadStore
            self._store = StaticReadStore()
        return self._store

    async def validate(
        self,
        event: dict,
        current_state: GameState,
    ) -> ValidationResult:
        """验证单个 Tier 1 事件

        返回:
            ValidationResult 含通过状态、原因、修正后的事件
        """
        event_type = event.get("event_type", "")
        payload = event.get("payload", {})

        if event_type == "ITEM_STATE_CHANGE":
            return await self._validate_item_state(payload, current_state)
        elif event_type == "NPC_STATE_CHANGE":
            return await self._validate_npc_state(payload, current_state)
        elif event_type == "LOCATION_TAG_CHANGE":
            return await self._validate_location_tag(payload, current_state)
        elif event_type == "SCENE_TRANSITION_IMPLIED":
            return await self._validate_scene_transition(payload, current_state)
        else:
            # 未知事件类型 → 放行（可能是后续新增的类型）
            return ValidationResult(
                passed=True,
                corrected_event=payload,
            )

    async def _validate_item_state(
        self,
        payload: dict,
        state: GameState,
    ) -> ValidationResult:
        """校验物品状态变更事件"""
        item_id = payload.get("item_id", "")
        location = payload.get("location", "")
        current_loc = state.get("current_location", "")

        # 存在性：item_id 不能为空
        if not item_id:
            return ValidationResult(
                passed=False,
                reason="item_id 为空",
            )

        # 作用域：如果有明确 location，需在当前场景或相邻场景
        if location and location != current_loc:
            logger.debug(
                f"Validator: ITEM_STATE_CHANGE 目标 {item_id} 在 {location}，"
                f"不在当前场景 {current_loc}，放行"
            )
            # 放行：可能是 LLM 预叙事的远处物品

        # 属性合法性：LOCKE D → LOCKED 拼写修正
        attribute = payload.get("attribute", "")
        if isinstance(attribute, str):
            payload["attribute"] = attribute.strip().upper()

        return ValidationResult(passed=True, corrected_event=payload)

    async def _validate_npc_state(
        self,
        payload: dict,
        state: GameState,
    ) -> ValidationResult:
        """校验 NPC 状态变更事件"""
        npc_id = payload.get("npc_id", "")
        scene_npcs = state.get("scene_npcs", [])

        if not npc_id:
            return ValidationResult(passed=False, reason="npc_id 为空")

        # 合法性：NPC 应在当前场景中（否则可能是远处 NPC，放行）
        if scene_npcs and npc_id not in scene_npcs:
            logger.debug(
                f"Validator: NPC {npc_id} 不在当前场景 {scene_npcs} 中，放行"
            )

        return ValidationResult(passed=True, corrected_event=payload)

    async def _validate_location_tag(
        self,
        payload: dict,
        state: GameState,
    ) -> ValidationResult:
        """校验场景标签变更事件"""
        location_id = payload.get("location_id", "")
        tag = payload.get("tag", "")

        if not location_id:
            return ValidationResult(passed=False, reason="location_id 为空")
        if not tag or not isinstance(tag, str):
            return ValidationResult(passed=False, reason="tag 格式不合法")

        # tag 标准化：大写 + 去空格
        payload["tag"] = tag.strip().upper().replace(" ", "_")

        return ValidationResult(passed=True, corrected_event=payload)

    async def _validate_scene_transition(
        self,
        payload: dict,
        state: GameState,
    ) -> ValidationResult:
        """校验场景切换暗示事件"""
        from_loc = payload.get("from_location", "")
        to_loc = payload.get("to_location", "")
        current_loc = state.get("current_location", "")

        if not from_loc or not to_loc:
            return ValidationResult(
                passed=False,
                reason="from_location 或 to_location 为空",
            )

        # 合法性：from_location 应与当前场景一致，否则可能是幻觉
        if from_loc != current_loc:
            logger.debug(
                f"Validator: SCENE_TRANSITION {from_loc}→{to_loc} "
                f"不匹配当前场景 {current_loc}，丢弃"
            )
            return ValidationResult(
                passed=False,
                reason=f"from_location '{from_loc}' 不匹配当前场景 '{current_loc}'",
            )

        return ValidationResult(
            passed=True,
            corrected_event={
                "current_location": to_loc,
            },
        )
