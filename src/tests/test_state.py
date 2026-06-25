"""
@File     :   test_state.py
@Desc     :   State 层单元测试
@Note     :   测试 game_state / reducer / event_log / snapshot / player_state / world_state

测试范围:
  - GameState 初始状态构建
  - state_view 裁剪
  - reduce_state 合并逻辑（替换/追加/深度合并/删除）
  - apply_events_to_state 回放
  - merge_patches 批量合并
  - EventLog 记录与回放
  - SnapshotManager 创建、恢复、列表
  - PlayerLoader 角色保存与查询
  - WorldManager 场景创建与查询
"""

import json
import pytest
from src.state.game_state import (
    GameState,
    create_initial_state,
    create_state_view,
    INTENT_VIEW,
    RULE_VIEW,
    NARRATE_VIEW,
)
from src.state.reducer import reduce_state, apply_events_to_state, merge_patches
from src.state.event_log import EventLog
from src.state.snapshot import SnapshotManager
from src.memory.event_store import EventStore


# ======== game_state.py ========

class TestGameState:
    """GameState 创建与视图"""

    def test_create_initial_state(self):
        """初始状态应包含所有必需字段"""
        state = create_initial_state("session-1")
        assert state["session_id"] == "session-1"
        assert state["status"] == "active"
        assert state["game_phase"] == "exploration"
        assert state["beat_counter"] == 0
        assert state["combat_active"] is False
        assert state["active_tags"] == []
        assert state["errors"] == []
        assert state["node_trace"] == []
        assert state["pending_dice"] is None
        assert state["narrative"] == ""

    def test_create_state_view(self):
        """state_view 应只包含指定字段"""
        state = create_initial_state("session-1")
        state["player_input"] = "搜索房间"
        state["game_phase"] = "investigation"

        view = create_state_view(state, INTENT_VIEW)
        assert "player_input" in view
        assert "game_phase" in view
        assert "session_id" in view
        # 不应包含的非视图字段
        assert "resolution" not in view
        assert "combat_active" not in view

    def test_intent_view_contains_required(self):
        """INTENT_VIEW 应包含 intent 节点所需的字段"""
        assert "player_input" in INTENT_VIEW
        assert "game_phase" in INTENT_VIEW
        assert "active_tags" in INTENT_VIEW

    def test_rule_view_contains_required(self):
        """RULE_VIEW 应包含 rule 节点所需的字段"""
        assert "intent" in RULE_VIEW
        assert "combat_active" in RULE_VIEW

    def test_narrate_view_contains_required(self):
        """NARRATE_VIEW 应包含 narrate 节点所需的字段"""
        assert "intent" in NARRATE_VIEW
        assert "resolution" in NARRATE_VIEW
        assert "narrative" in NARRATE_VIEW


# ======== reducer.py ========

class TestReduceState:
    """reduce_state 合并逻辑"""

    def setup_method(self):
        self.base = create_initial_state("session-reduce")

    def test_replace_field(self):
        """常规字段应直接替换"""
        result = reduce_state(self.base, {"narrative": "新叙事文本"})
        assert result["narrative"] == "新叙事文本"
        # 其他字段不变
        assert result["session_id"] == "session-reduce"

    def test_list_append(self):
        """list 字段应追加而非替换"""
        result = reduce_state(self.base, {"active_tags": ["combat"]})
        assert result["active_tags"] == ["combat"]

        result2 = reduce_state(result, {"active_tags": ["darkness"]})
        assert result2["active_tags"] == ["combat", "darkness"]

    def test_list_append_multiple(self):
        """list 字段可一次追加多个"""
        result = reduce_state(self.base, {"errors": ["err1", "err2"]})
        assert result["errors"] == ["err1", "err2"]

        result2 = reduce_state(result, {"errors": ["err3"]})
        assert result2["errors"] == ["err1", "err2", "err3"]

    def test_dict_deep_merge(self):
        """dict 字段应深度合并"""
        result = reduce_state(self.base, {"intent": {"type": "MOVE", "target": "door"}})
        assert result["intent"]["type"] == "MOVE"
        assert result["intent"]["target"] == "door"

        result2 = reduce_state(result, {"intent": {"confidence": 0.9}})
        assert result2["intent"]["type"] == "MOVE"  # 保留原值
        assert result2["intent"]["confidence"] == 0.9  # 新增字段

    def test_none_deletes_field(self):
        """None 值应删除字段"""
        result = reduce_state(self.base, {"pending_dice": {"reason": "侦查检定"}})
        assert result["pending_dice"] is not None

        result2 = reduce_state(result, {"pending_dice": None})
        assert "pending_dice" not in result2

    def test_counter_increment(self):
        """计数器字段支持 '+N' 增量语法"""
        assert self.base["beat_counter"] == 0

        result = reduce_state(self.base, {"beat_counter": "+1"})
        assert result["beat_counter"] == 1

        result2 = reduce_state(result, {"beat_counter": "+5"})
        assert result2["beat_counter"] == 6

    def test_new_field_addition(self):
        """不存在的字段应直接添加"""
        result = reduce_state(self.base, {"custom_field": "test_value"})
        assert result["custom_field"] == "test_value"

    def test_preserves_immutable_base(self):
        """原 state 不应被修改"""
        original_narrative = self.base["narrative"]
        reduce_state(self.base, {"narrative": "changed"})
        assert self.base["narrative"] == original_narrative

    def test_combatants_append(self):
        """combatants 作为 list 字段应追加"""
        result = reduce_state(self.base, {"combatants": [{"name": "怪物A", "hp": 10}]})
        assert len(result["combatants"]) == 1

        result2 = reduce_state(result, {"combatants": [{"name": "调查员B", "hp": 12}]})
        assert len(result2["combatants"]) == 2

    def test_empty_patch(self):
        """空 patch 应返回原 state"""
        result = reduce_state(self.base, {})
        assert result == self.base


class TestApplyEventsToState:
    """事件回放重建状态"""

    def test_replay_empty(self):
        """空事件列表应返回原状态"""
        state = create_initial_state("session-replay")
        result = apply_events_to_state(state, [])
        assert result == state

    def test_replay_sequence(self):
        """按序回放事件应正确重建状态"""
        state = create_initial_state("session-replay")
        events = [
            {
                "data": {
                    "patch": {"narrative": "第一回合"},
                }
            },
            {
                "data": {
                    "patch": {"narrative": "第二回合", "beat_counter": "+1"},
                }
            },
            {
                "data": {
                    "patch": {"active_tags": ["combat"]},
                }
            },
        ]

        result = apply_events_to_state(state, events)
        assert result["narrative"] == "第二回合"  # 最后的值
        assert result["beat_counter"] == 1
        assert "combat" in result["active_tags"]


class TestMergePatches:
    """state_patch 批量合并"""

    def test_merge_simple(self):
        """简单字段合并"""
        a = {"narrative": "文本A"}
        b = {"narrative": "文本B"}
        result = merge_patches(a, b)
        assert result["narrative"] == "文本B"  # b 覆盖 a

    def test_merge_list_append(self):
        """list 字段应合并追加"""
        a = {"active_tags": ["tag1"]}
        b = {"active_tags": ["tag2"]}
        result = merge_patches(a, b)
        assert result["active_tags"] == ["tag1", "tag2"]

    def test_merge_dict(self):
        """dict 字段深度合并"""
        a = {"intent": {"type": "MOVE"}}
        b = {"intent": {"target": "door"}}
        result = merge_patches(a, b)
        assert result["intent"]["type"] == "MOVE"
        assert result["intent"]["target"] == "door"

    def test_merge_none(self):
        """None 值应从合并结果中删除"""
        a = {"narrative": "文本"}
        b = {"narrative": None}
        result = merge_patches(a, b)
        assert "narrative" not in result


# ======== event_log.py ========

class TestEventLog:
    """EventLog 记录与回放"""

    @pytest.fixture
    async def event_store(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "test_events.db"))
        yield store
        await store.close()

    @pytest.fixture
    async def event_log(self, event_store):
        return EventLog(event_store)

    @pytest.mark.asyncio
    async def test_record_and_apply(self, event_log):
        """记录事件后应返回新 state 和事件记录"""
        current = create_initial_state("session-evt")
        new_state, event = await event_log.record_and_apply(
            current=current,
            patch={"narrative": "测试叙事", "beat_counter": "+1"},
            event_type="TestEvent",
            source_node="test_node",
        )

        assert new_state["narrative"] == "测试叙事"
        assert new_state["beat_counter"] == 1
        assert event["type"] == "TestEvent"
        assert event["source_node"] == "test_node"
        assert event["session_id"] == "session-evt"

    @pytest.mark.asyncio
    async def test_replay_to_state(self, event_log):
        """回放应重建状态"""
        current = create_initial_state("session-replay")

        # 记录两个事件
        await event_log.record_and_apply(
            current=current,
            patch={"narrative": "事件1", "beat_counter": "+1"},
            event_type="Event1",
        )

        await event_log.record_and_apply(
            current=current,
            patch={"narrative": "事件2", "active_tags": ["combat"]},
            event_type="Event2",
        )

        # 从空状态回放
        rebuilt = await event_log.replay_to_state("session-replay")
        assert rebuilt["narrative"] == "事件2"
        assert rebuilt["beat_counter"] == 1
        assert "combat" in rebuilt["active_tags"]

    @pytest.mark.asyncio
    async def test_get_events(self, event_log):
        """获取事件流应返回所有事件"""
        current = create_initial_state("session-get")
        await event_log.record_and_apply(
            current=current, patch={}, event_type="EventA",
        )
        await event_log.record_and_apply(
            current=current, patch={}, event_type="EventB",
        )

        events = await event_log.get_events("session-get")
        assert len(events) == 2
        assert events[0]["type"] == "EventA"
        assert events[1]["type"] == "EventB"

    @pytest.mark.asyncio
    async def test_subscribe_and_notify(self, event_log):
        """订阅者应收到事件通知"""
        received = []

        async def my_callback(event):
            received.append(event["type"])

        event_log.subscribe("TestEvent", my_callback)

        current = create_initial_state("session-sub")
        await event_log.record_and_apply(
            current=current, patch={}, event_type="TestEvent",
        )
        await event_log.record_and_apply(
            current=current, patch={}, event_type="OtherEvent",
        )

        assert len(received) == 1
        assert received[0] == "TestEvent"

    @pytest.mark.asyncio
    async def test_subscribe_all(self, event_log):
        """订阅空字符串应接收所有事件"""
        received = []

        async def my_callback(event):
            received.append(event["type"])

        event_log.subscribe("", my_callback)

        current = create_initial_state("session-all")
        await event_log.record_and_apply(
            current=current, patch={}, event_type="EventX",
        )
        await event_log.record_and_apply(
            current=current, patch={}, event_type="EventY",
        )

        assert len(received) == 2

    @pytest.mark.asyncio
    async def test_get_latest_version(self, event_log):
        """版本号应递增"""
        current = create_initial_state("session-ver")
        assert await event_log.get_latest_version("session-ver") == 0

        await event_log.record_and_apply(
            current=current, patch={}, event_type="EventV1",
        )
        assert await event_log.get_latest_version("session-ver") == 1

        await event_log.record_and_apply(
            current=current, patch={}, event_type="EventV2",
        )
        assert await event_log.get_latest_version("session-ver") == 2


# ======== snapshot.py ========

class TestSnapshotManager:
    """快照创建与恢复"""

    @pytest.fixture
    async def event_store(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "snap_events.db"))
        yield store
        await store.close()

    @pytest.fixture
    async def snapshot_mgr(self, tmp_path, event_store):
        mgr = SnapshotManager(
            event_store=event_store,
            db_path=str(tmp_path / "snapshots" / "snapshots.db"),
        )
        yield mgr
        await mgr.close()

    @pytest.mark.asyncio
    async def test_create_and_restore(self, snapshot_mgr):
        """快照创建与恢复应返回相同状态"""
        state = create_initial_state("session-snap")
        state["narrative"] = "测试快照"
        state["game_phase"] = "combat"

        snap_id = await snapshot_mgr.create(state, label="test_snap")
        assert snap_id is not None

        restored = await snapshot_mgr.restore(snap_id)
        assert restored is not None
        assert restored["session_id"] == "session-snap"
        assert restored["narrative"] == "测试快照"
        assert restored["game_phase"] == "combat"

    @pytest.mark.asyncio
    async def test_list_snapshots(self, snapshot_mgr):
        """快照列表应返回正确的元数据"""
        state = create_initial_state("session-list")
        await snapshot_mgr.create(state, label="snap1")
        await snapshot_mgr.create(state, label="snap2")

        snapshots = await snapshot_mgr.list_snapshots("session-list")
        assert len(snapshots) == 2
        # 按版本降序
        assert snapshots[0]["version"] > snapshots[1]["version"]

    @pytest.mark.asyncio
    async def test_get_latest(self, snapshot_mgr):
        """获取最新快照"""
        state = create_initial_state("session-latest")
        await snapshot_mgr.create(state, label="first")
        snap_id2 = await snapshot_mgr.create(state, label="second")

        latest = await snapshot_mgr.get_latest("session-latest")
        assert latest is not None
        assert latest["label"] == "second"
        assert latest["id"] == snap_id2

    @pytest.mark.asyncio
    async def test_delete_snapshot(self, snapshot_mgr):
        """删除快照"""
        state = create_initial_state("session-del")
        snap_id = await snapshot_mgr.create(state)
        assert await snapshot_mgr.delete(snap_id) is True
        assert await snapshot_mgr.delete(snap_id) is False

    @pytest.mark.asyncio
    async def test_restore_nonexistent(self, snapshot_mgr):
        """恢复不存在的快照应返回 None"""
        result = await snapshot_mgr.restore("non-existent-id")
        assert result is None

    @pytest.mark.asyncio
    async def test_create_multiple_snapshots(self, snapshot_mgr):
        """多次创建快照应递增版本号"""
        state = create_initial_state("session-multi")
        s1 = await snapshot_mgr.create(state)
        s2 = await snapshot_mgr.create(state)
        s3 = await snapshot_mgr.create(state)

        snaps = await snapshot_mgr.list_snapshots("session-multi")
        assert len(snaps) == 3


# ======== 集成测试 ========

class TestEventLogSnapshotIntegration:
    """EventLog + Snapshot 集成"""

    @pytest.fixture
    async def event_store(self, tmp_path):
        store = EventStore(db_path=str(tmp_path / "integ_events.db"))
        yield store
        await store.close()

    @pytest.fixture
    async def event_log(self, event_store):
        return EventLog(event_store)

    @pytest.fixture
    async def snapshot_mgr(self, tmp_path, event_store):
        mgr = SnapshotManager(
            event_store=event_store,
            db_path=str(tmp_path / "snapshots" / "integ.db"),
        )
        yield mgr
        await mgr.close()

    @pytest.mark.asyncio
    async def test_record_snapshot_restore(self, event_log, snapshot_mgr):
        """事件记录 → 快照 → 恢复的完整流程"""
        state = create_initial_state("session-integ")

        # 记录事件
        state, _ = await event_log.record_and_apply(
            current=state,
            patch={"narrative": "你走进黑暗的走廊"},
            event_type="Narrative",
        )
        state, _ = await event_log.record_and_apply(
            current=state,
            patch={"active_tags": ["dark"], "beat_counter": "+1"},
            event_type="TagAdded",
        )

        # 创建快照
        snap_id = await snapshot_mgr.create(state, label="checkpoint")

        # 再记录一个事件
        state, _ = await event_log.record_and_apply(
            current=state,
            patch={"narrative": "你发现了一扇锁着的门"},
            event_type="Narrative",
        )

        # 从快照恢复
        restored = await snapshot_mgr.restore(snap_id)
        assert restored is not None
        # 快照 + 增量事件回放
        assert "锁着的门" in restored["narrative"]
        assert "dark" in restored["active_tags"]
        assert restored["beat_counter"] == 1


class TestReducerEdgeCases:
    """Reducer 边界情况"""

    def test_empty_state(self):
        """空 state 应能处理"""
        result = reduce_state({}, {"narrative": "test"})
        assert result["narrative"] == "test"

    def test_nested_dict_overwrite(self):
        """深度合并时同名字段应覆盖"""
        state = create_initial_state("session-edge")
        state["intent"] = {"type": "MOVE", "target": "door", "flags": {"urgent": True}}

        result = reduce_state(state, {"intent": {"target": "window", "flags": {"silent": True}}})
        assert result["intent"]["type"] == "MOVE"  # 保留
        assert result["intent"]["target"] == "window"  # 覆盖
        assert result["intent"]["flags"]["silent"] is True  # 新增
        assert result["intent"]["flags"].get("urgent") is True  # 深度合并保留

    def test_multiple_list_appends(self):
        """多次追加 list"""
        state = create_initial_state("session-list2")
        state = reduce_state(state, {"node_trace": [{"node": "A"}]})
        state = reduce_state(state, {"node_trace": [{"node": "B"}]})
        state = reduce_state(state, {"node_trace": [{"node": "C"}]})
        assert len(state["node_trace"]) == 3

    def test_counter_non_string(self):
        """计数器字段直接设值（非增量语法）"""
        state = create_initial_state("session-counter")
        result = reduce_state(state, {"beat_counter": 42})
        assert result["beat_counter"] == 42

    def test_combat_state_transition(self):
        """战斗状态切换"""
        state = create_initial_state("session-combat")
        state = reduce_state(state, {"game_phase": "combat"})
        state = reduce_state(state, {"combat_active": True})
        state = reduce_state(state, {"combatants": [{"name": "怪物", "hp": 15}]})
        state = reduce_state(state, {"combat_round": "+1"})

        assert state["game_phase"] == "combat"
        assert state["combat_active"] is True
        assert state["combat_round"] == 1
        assert len(state["combatants"]) == 1
