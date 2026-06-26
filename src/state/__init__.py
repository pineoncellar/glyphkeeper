"""
@File     :   __init__.py
@Desc     :   state 包 — 世界唯一真相（Event Sourcing）
@Note     :   所有游戏状态的唯一权威来源

职责:
  - 所有游戏状态的唯一权威来源
  - LLM 不直接修改 state，仅通过 event → reducer 变更
  - 支持快照与时间线回溯
  - 模组载入（从 EventStore 读取已摄入数据）
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
from src.state.player_state import CharacterStore, _character_to_dict, _dict_to_character
from src.state.world_state import WorldManager
from src.state.module_loader import ModuleLoader
from src.state.read_models import StaticReadStore
from src.state.session_state import SessionKnowledgeState
from src.state.projector import StateProjector

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
    "_character_to_dict",
    "_dict_to_character",
    # world_state
    "WorldManager",
    # module_loader
    "ModuleLoader",
]
