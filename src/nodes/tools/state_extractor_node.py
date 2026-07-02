# -*- coding: utf-8 -*-
"""
@File     :   state_extractor_node.py
@Desc     :   状态提取节点 — 将叙事文本按三级系统提取结构化状态变更
@Note     :   使用 fast tier LLM + JSON mode，产出 Tier 1 / Tier 2 / Tier 3
              位置在 keeper_graph 中 narrate_node 之后

三级判定矩阵:
  Tier 1 — 交互标签: 改变物理法则、检定难度或允许新动作的逻辑属性
  Tier 2 — 正典事实: 不改变规则，但下次改了就吃书的客观真理
  Tier 3 — 氛围润色: 瞬时的感官体验，5分钟后不重要
"""

from __future__ import annotations

import json
from typing import Any

from src.state.game_state import GameState
from src.tools import get_logger
from src.tools.llm_client import call_llm as _call_llm

logger = get_logger(__name__)


# ====================================================================
# 提取系统提示词 — 硬性判定准则
# ====================================================================

EXTRACTOR_SYSTEM_PROMPT = """你是一个严格的三级信息提取器。阅读【叙事文本】，对照【物理现实上下文】，
提取以下三类信息：

## Tier 1 — 交互标签（最高优先级）
改变物理法则、检定难度或允许新动作的逻辑属性。
核心判定：玩家能利用它改变检定结果或可用动作吗？
输出格式：{event_type, payload}
支持的事件类型：
- ITEM_STATE_CHANGE: {item_id, attribute, value, location}
- NPC_STATE_CHANGE: {npc_id, attribute, value}
- LOCATION_TAG_CHANGE: {location_id, tag, active: bool}
- SCENE_TRANSITION_IMPLIED: {from_location, to_location, via_exit}

## Tier 2 — 正典事实（中等优先级）
不改变规则，但在叙事上必须保持前后一致的客观真理。
核心判定：如果下次描述变了，玩家会觉得出戏（吃书）吗？
输出格式：纯文本短句列表

## Tier 3 — 氛围润色（忽略）
瞬时的感官体验，随时间流逝自动失效。
核心判定：五分钟后这个描述还成立/重要吗？
输出：丢弃，不进入任何持久化存储

## 约束
- 不确定时不提取，宁缺毋滥
- Tier 2 事实每条不超过 50 字
- items/npcs 的 identifier 优先使用物理现实中的系统 key
- 只输出 JSON，不附加任何解释"""


# ====================================================================
# 节点主逻辑
# ====================================================================


async def state_extractor_node(state: GameState) -> dict[str, Any]:
    """从 executed_actions 链 + 叙事文本中提取 Tier 1 事件和 Tier 2 事实

    两条提取路径：
      路径 A: 从 executed_actions 中获取 deterministic_changes 作为确定性 Tier 1 事件
      路径 B: 从 narrative_output 中用 LLM 提取 Tier 2 事实

    返回:
        dict with keys:
          - pending_tier1_events: list[dict] 确定性事件
          - pending_tier2_facts:   list[str]   LLM 提取的正典事实
    """
    actions = state.get("executed_actions", [])
    narrative = state.get("narrative_output", "")

    # ── 路径 A: 从 executed_actions 提取确定性变更 ──
    tier1_from_actions = []
    for action in actions:
        changes = action.get("deterministic_changes", {})
        if changes:
            tier1_from_actions.append({
                "event_type": "STATE_CHANGE_BATCH",
                "payload": changes,
                "source_intent": action.get("intent_id", ""),
            })

    # ── 路径 B: 从叙事文本提取 Tier 2 事实 ──
    tier2 = []
    if narrative:
        world_context = state.get("world_context", "")
        current_loc = state.get("current_location", "")
        scene_npcs = state.get("scene_npcs", [])

        context_lines = ["【物理现实上下文】"]
        context_lines.append(f"当前位置: {current_loc}")
        context_lines.append(f"场景NPC: {scene_npcs}")
        if world_context:
            context_lines.append(f"世界知识: {world_context[:500]}")
        context_lines.append("")
        context_lines.append("【叙事文本】")
        context_lines.append(narrative)
        prompt = "\n".join(context_lines)

        messages = [
            {"role": "system", "content": EXTRACTOR_SYSTEM_PROMPT},
            {"role": "user", "content": prompt},
        ]

        try:
            result = await _call_llm("fast", messages)
            if result.is_ok and result.text and result.text.strip():
                data = json.loads(result.text)
                tier2 = data.get("tier2_new_facts", [])
        except (json.JSONDecodeError, Exception) as e:
            logger.debug(f"state_extractor: Tier 2 提取异常（非阻塞）: {e}")

    if tier1_from_actions or tier2:
        logger.info(
            f"state_extractor: {len(tier1_from_actions)} 个确定性事件, "
            f"{len(tier2)} 条 Tier 2 事实"
        )

    return {
        "pending_tier1_events": tier1_from_actions,
        "pending_tier2_facts": tier2,
    }
