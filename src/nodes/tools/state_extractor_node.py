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
    """从叙事文本中提取 Tier 1 事件和 Tier 2 事实

    Node 签名:
        async def state_extractor_node(state: GameState) -> dict

    从 state.narrative_output 读取完整叙事文本，
    结合 state.world_context 中的物理现实做对照，
    返回 pending_tier1_events / pending_tier2_facts 供 Engine 后台追赶。

    返回:
        dict with keys:
          - pending_tier1_events: list[dict] 提取的 Tier 1 事件
          - pending_tier2_facts:   list[str]   提取的 Tier 2 事实
    """
    narrative = state.get("narrative_output", "")
    if not narrative:
        return {"pending_tier1_events": [], "pending_tier2_facts": []}

    # 构建物理现实上下文（给 LLM 做对照，裁剪长度防超 token）
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
        if not result.is_ok:
            logger.warning(f"state_extractor: LLM 调用失败: {result.error}")
            return {"pending_tier1_events": [], "pending_tier2_facts": []}

        data = json.loads(result.text)
    except json.JSONDecodeError as e:
        logger.warning(f"state_extractor: JSON 解析失败: {e}")
        return {"pending_tier1_events": [], "pending_tier2_facts": []}
    except Exception as e:
        logger.warning(f"state_extractor: 提取异常: {e}")
        return {"pending_tier1_events": [], "pending_tier2_facts": []}

    tier1 = data.get("tier1_implied_events", [])
    tier2 = data.get("tier2_new_facts", [])

    if tier1 or tier2:
        logger.info(
            f"state_extractor: 提取到 {len(tier1)} 个 Tier 1 事件, "
            f"{len(tier2)} 条 Tier 2 事实"
        )

    return {
        "pending_tier1_events": tier1,
        "pending_tier2_facts": tier2,
    }
