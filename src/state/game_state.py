"""
@File     :   game_state.py
@Desc     :   游戏全局状态定义 — LangGraph StateGraph 的核心 TypedDict
@Note     :   所有 Node 函数的签名都是 async def node(state: GameState) -> dict
             返回的 dict 被 LangGraph reducer 自动合并到 state 中
"""

from __future__ import annotations

from typing import TypedDict, Optional, Any
from src.tools.time import TimeSlot


class GameState(TypedDict):
    """游戏全局状态 — 所有 Node 的唯一数据源

    修改规则:
      - Node 只能通过返回 state_patch 请求修改
      - Engine 调用 Reducer 合并 state_patch 到 state
      - 每次修改都生成一条不可变 Event
    """

    # ── 会话标识 ──
    session_id: str                     # 当前游戏会话 UUID

    # ── 会话元数据 ──
    scenario_name: str                  # 当前模组/剧本名称
    status: str                         # 会话状态: active / paused / completed
    created_at: str                     # 会话创建时间 ISO

    # ── 游戏内时间 ──
    time_slot: str                      # 当前时间段（TimeSlot 枚举值）
    beat_counter: int                   # 节拍计数器（每次玩家输入 +1）

    # ── 当前输入 ──
    player_input: str                   # 玩家的原始输入文本

    # ── 当前处理结果 ──
    intent: Optional[dict]              # IntentNode 的输出
    resolution: Optional[dict]          # RuleNode 的输出
    world_context: str                  # LookupNode 的世界知识上下文
    narrative: str                      # NarratorNode 的叙事文本

    # ── 游戏控制 ──
    game_phase: str                     # 游戏阶段: exploration / combat / dialogue
    active_tags: list[str]              # 当前激活的全局标签（条件性内容解锁）

    # ── 角色数据 ──
    character: Optional[dict]           # 调查员角色数据（由角色创建流程注入）
    current_location: str               # 玩家当前所在场景 key

    # ── NPC 交互状态 ──
    npc_relations: dict                 # NPC 关系追踪: {npc_name: {talk_count, disposition, last_talk}}
    current_npc: str                    # 当前对话中的 NPC 名称

    # ── 交互挂起 ──
    pending_dice: Optional[dict]        # 等待玩家掷骰: {reason, skill_name, difficulty, bonus_dice, penalty_dice}

    # ── 战斗状态 ──
    combat_active: bool                 # 是否处于战斗轮
    combat_round: int                   # 当前战斗轮次
    combatants: list[dict]              # 参战方快照列表

    # ── 运行时元数据 ──
    errors: list[str]                   # 执行过程中的错误记录
    node_trace: list[dict]              # 节点执行追踪日志


# ── 辅助函数 ──

def create_initial_state(
    session_id: str,
    scenario_name: str = "",
    time_slot: str = "MORNING",
) -> GameState:
    """构建初始游戏状态"""
    from datetime import datetime, timezone

    return {
        "session_id": session_id,
        "scenario_name": scenario_name,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "time_slot": time_slot,
        "beat_counter": 0,
        "player_input": "",
        "intent": None,
        "resolution": None,
        "world_context": "",
        "narrative": "",
        "game_phase": "exploration",
        "character": None,
        "current_location": "",
        "npc_relations": {},
        "current_npc": "",
        "active_tags": [],
        "pending_dice": None,
        "combat_active": False,
        "combat_round": 0,
        "combatants": [],
        "errors": [],
        "node_trace": [],
    }


def create_state_view(state: GameState, view_keys: list[str]) -> dict:
    """裁剪 state，只保留指定字段（用于 NodeInput.state_view）"""
    return {k: v for k, v in state.items() if k in view_keys}


# 常用视图模板
INTENT_VIEW = ["session_id", "player_input", "game_phase", "active_tags", "narrative", "character", "current_location"]
RULE_VIEW = ["session_id", "intent", "game_phase", "active_tags", "combat_active", "combatants", "character"]
NARRATE_VIEW = ["session_id", "intent", "resolution", "world_context", "narrative", "game_phase", "active_tags", "character", "current_location"]
