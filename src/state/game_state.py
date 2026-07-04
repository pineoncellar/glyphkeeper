"""
@File     :   game_state.py
@Desc     :   游戏全局状态定义 — LangGraph StateGraph 的核心 TypedDict
@Note     :   players dict 为多玩家预留，单玩家时以 DEFAULT_PLAYER_ID 为键
"""

from __future__ import annotations

from typing import TypedDict, Optional, Any
from src.tools.time import TimeSlot

DEFAULT_PLAYER_ID = "default"
"""单玩家模式下的默认玩家 ID，多玩家时替换为真实 user_id"""


def _default_player_entry() -> dict:
    """新建一个玩家槽的默认结构"""
    return {
        "character": None,
        "current_location": "",
        "pending_dice": None,
        "npc_relations": {},
        "current_npc": "",
        "npc_dialogue": "",
        "npc_dialogue_results": [],
    }


def get_player(state: dict, user_id: str = "") -> dict:
    """
    从 state 中获取指定玩家的数据。
    user_id 为空时取 DEFAULT_PLAYER_ID。
    不存在时自动创建默认槽，保证调用方不用做 None 检查。
    """
    uid = user_id or DEFAULT_PLAYER_ID
    players = state.get("players", {})
    if uid not in players:
        players[uid] = _default_player_entry()
        state["players"] = players
    return players[uid]


def get_current_player(state: dict) -> dict:
    """
    获取当前活跃玩家（单玩家模式下取唯一玩家）。
    兼容多玩家扩展：后续可根据 user_id 路由。
    """
    return get_player(state, DEFAULT_PLAYER_ID)


class ActionExecutionResult(TypedDict):
    """单步行动的独立局部裁决账单

    由每个规则节点在串行循环中产出，按执行顺序追加。
    """
    intent_id: str
    intent_type: str
    rule_context: dict
    deterministic_changes: dict
    raw_fixed_text: str
    flavor_context: str


class GameState(TypedDict):
    """游戏全局状态 — 所有 Node 的唯一数据源

    players: {user_id: {character, current_location, pending_dice, ...}}
    玩家独有数据在 players[uid] 内；世界共享数据在顶层。
    """

    # ── 会话标识 ──
    session_id: str
    platform: str
    channel_id: str
    user_id: str
    world_id: str

    # ── 会话元数据 ──
    scenario_name: str
    status: str
    created_at: str

    # ── 游戏内时间 ──
    time_slot: str
    beat_counter: int

    # ── 当前输入 ──
    player_input: str

    # ── 多意图串行循环控制流 ──
    intent_queue: list[dict]
    current_intent_idx: int
    executed_actions: list[dict]

    # ── 当前处理结果 ──
    intent: Optional[dict]
    resolution: Optional[dict]
    physical_reality: str
    world_context: str
    rag_context: str
    archivist_result: Optional[dict]
    entity_name_map: dict
    narrative: str

    # ── 游戏控制 ──
    game_phase: str
    active_tags: list[str]

    # ── 多玩家数据 ──
    players: dict[str, dict]
    """{user_id: {character, current_location, pending_dice, npc_relations, ...}}"""

    # ── 战斗状态（世界共享） ──
    combat_active: bool
    combat_round: int
    combatants: list[dict]

    # ── 实体对齐结果 ──
    resolved_targets: Optional[dict]
    scene_npcs: list[str]
    attention_focus: Optional[dict]

    # ── 状态审计缓冲区 ──
    narrative_output: str
    pending_tier1_events: list[dict]
    pending_tier2_facts: list[str]

    # ── 对话历史 ──
    dialogue_history: list[dict]

    # ── 物理交互子图内部传递（每轮清零，子图内节点间传递中间结果） ──
    _skill_check_result: Optional[dict]       # skill_check_node 输出缓存
    _spatial_result: Optional[dict]           # spatial_physics_node 输出缓存

    # ── db_lookup_node 结构化缓存（供 spatial_physics_node 消费，免重复 SQL） ──
    _scene_interactables: list[dict]          # 当前场景的可交互物品列表
    _scene_locations: dict                    # 当前场景 + 邻接场景元数据

    # ── 运行时掉落物品池（key=场景key, value=该场景地面上的物品名列表） ──
    _dropped_items: dict[str, list[str]]

    # ── 运行时元数据 ──
    errors: list[str]
    node_trace: list[dict]


# ── 辅助函数 ──

def _fresh_player_entry(uid: str) -> dict:
    """构造初始玩家槽（与 _default_player_entry 同步）"""
    return {
        "character": None,
        "current_location": "",
        "pending_dice": None,
        "npc_relations": {},
        "current_npc": "",
        "npc_dialogue": "",
        "npc_dialogue_results": [],
    }


def create_initial_state(
    session_id: str,
    scenario_name: str = "",
    time_slot: str = "MORNING",
    platform: str = "cli",
    channel_id: str = "",
    user_id: str = "",
    world_id: str = "",
) -> GameState:
    """构建初始游戏状态，自动为当前玩家创建默认槽"""
    from datetime import datetime, timezone

    uid = user_id or DEFAULT_PLAYER_ID

    return {
        "session_id": session_id,
        "platform": platform,
        "channel_id": channel_id,
        "user_id": uid,
        "world_id": world_id,
        "scenario_name": scenario_name,
        "status": "active",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "time_slot": time_slot,
        "beat_counter": 0,
        "player_input": "",
        # 多意图串行循环控制流
        "intent_queue": [],
        "current_intent_idx": 0,
        "executed_actions": [],
        # 当前处理结果
        "intent": None,
        "resolution": None,
        "physical_reality": "",
        "world_context": "",
        "rag_context": "",
        "archivist_result": None,
        "entity_name_map": {},
        "narrative": "",
        "narrative_output": "",
        "game_phase": "exploration",
        # 多玩家
        "players": {uid: _fresh_player_entry(uid)},
        # 战斗状态
        "combat_active": False,
        "combat_round": 0,
        "combatants": [],
        # 实体对齐
        "resolved_targets": None,
        "scene_npcs": [],
        "attention_focus": None,
        # 物理交互子图临时字段
        "_skill_check_result": None,
        "_spatial_result": None,
        "_scene_interactables": [],
        "_scene_locations": {},
        "_dropped_items": {},
        # 审计缓冲区
        "pending_tier1_events": [],
        "pending_tier2_facts": [],
        # 对话历史
        "dialogue_history": [],
        # 运行时元数据
        "active_tags": [],
        "errors": [],
        "node_trace": [],
    }


DIALOGUE_HISTORY_MAX = 20
"""dialogue_history 最大保留轮次"""


def format_dialogue_history(
    history: list[dict],
    recent_n: int = 5,
) -> str:
    """将 dialogue_history 格式化为 LLM 友好的多轮对话文本

    从历史中取最近 recent_n 轮，按时间正序排列。
    每轮格式： 第N轮 玩家: xxx 守密人: xxx
    """
    if not history:
        return ""
    recent = history[-recent_n:]
    lines: list[str] = []
    for entry in recent:
        turn = entry.get("turn", "?")
        player = entry.get("player", "")
        keeper = entry.get("keeper", "")
        lines.append(f"第{turn}轮")
        lines.append(f"  玩家: {player}")
        lines.append(f"  守密人: {keeper}")
    return "\n".join(lines)


def create_state_view(state: GameState, view_keys: list[str]) -> dict:
    """裁剪 state，只保留指定字段（用于 NodeInput.state_view）"""
    return {k: v for k, v in state.items() if k in view_keys}


# ── 运行时字段归属 ──

_PLAYER_LOCAL_FIELDS = {
    "character", "current_location", "pending_dice",
    "npc_relations", "current_npc", "npc_dialogue", "npc_dialogue_results",
}
"""运行时 Graph 执行中由 Nodes 写入、但实际应归属于 players[uid] 的顶层字段。
   engine 执行后调用 rehome_player_fields() 将它们搬回正确的嵌套位置。"""


def rehome_player_fields(state: dict, user_id: str = "") -> dict:
    """
    将 Graph 执行后散落在 state 顶层的玩家字段搬回 players[uid]。
    先读后删，避免覆盖已在 players[uid] 中的值（以 players 内的值为准）。

    在 engine.run() 末尾、返回结果前调用。
    """
    uid = user_id or DEFAULT_PLAYER_ID
    players = state.setdefault("players", {})
    player = players.setdefault(uid, _default_player_entry())

    for field in _PLAYER_LOCAL_FIELDS:
        if field in state:
            # 只在 players[uid] 中该字段为 None/空 时才搬运
            if field == "character":
                if player.get("character") is None:
                    player["character"] = state.pop(field, None)
                else:
                    # character 特殊处理：合并而非替换
                    existing = player.get("character") or {}
                    incoming = state.pop(field, {}) or {}
                    if incoming:
                        existing.update(incoming)
                        player["character"] = existing
            elif field == "npc_dialogue_results":
                existing = player.get("npc_dialogue_results", [])
                incoming = state.pop(field, []) or []
                if incoming:
                    player["npc_dialogue_results"] = existing + incoming
            else:
                # 一般字段：优先保留 players 中的值，不覆盖
                if not player.get(field):
                    player[field] = state.pop(field)
                else:
                    state.pop(field, None)

    state["players"] = players
    return state


def is_loop_complete(state: GameState) -> bool:
    """判断多意图串行循环是否已完成

    当循环指针 >= 队列长度时，所有意图已处理完毕。
    """
    return state.get("current_intent_idx", 0) >= len(state.get("intent_queue", []))


# 常用视图模板
INTENT_VIEW = ["session_id", "player_input", "game_phase", "active_tags", "narrative", "players"]
RULE_VIEW = ["session_id", "intent", "game_phase", "active_tags", "combat_active", "combatants", "players", "resolved_targets", "scene_npcs"]
NARRATE_VIEW = ["session_id", "intent", "resolution", "world_context", "narrative", "game_phase", "active_tags", "players", "resolved_targets", "scene_npcs"]
