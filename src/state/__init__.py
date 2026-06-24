"""
@File     :   __init__.py
@Desc     :   state 包 — 世界唯一真相（Event Sourcing）
@Note     :   所有游戏状态的唯一权威来源

职责:
  - 所有游戏状态的唯一权威来源
  - LLM 不直接修改 state，仅通过 event → reducer 变更
  - 支持快照与时间线回溯
"""

from src.state.game_state import (
    GameState,
    create_initial_state,
    create_state_view,
    INTENT_VIEW,
    RULE_VIEW,
    NARRATE_VIEW,
)
from src.state.reducer import (
    reduce_state,
    apply_events_to_state,
    merge_patches,
)
from src.state.event_log import EventLog
from src.state.snapshot import SnapshotManager
from src.state.player_state import PlayerLoader
from src.state.world_state import WorldManager

__all__ = [
    # game_state
    "GameState",
    "create_initial_state",
    "create_state_view",
    "INTENT_VIEW",
    "RULE_VIEW",
    "NARRATE_VIEW",
    # reducer
    "reduce_state",
    "apply_events_to_state",
    "merge_patches",
    # event_log
    "EventLog",
    # snapshot
    "SnapshotManager",
    # player_state
    "PlayerLoader",
    # world_state
    "WorldManager",
]
