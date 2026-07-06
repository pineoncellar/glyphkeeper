"""
@File     :   test_runtime.py
@Desc     :   Runtime 层测试
@Note     :   覆盖 context / dispatcher / engine / scheduler 四个模块

测试范围:
  - ExecutionContext: 创建、追踪、存储、序列化
  - ExecutionResult: 成功/失败/控制语义
  - dispatch_with_retry: 成功/超时/异常/重试
  - NodeDispatcher: 挂起判定
  - GraphEngine: 构建初始状态、langgraph 模式运行
  - InputScheduler: 会话创建、多轮对话、并发安全
"""

from __future__ import annotations

import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.state.game_state import GameState, create_initial_state
from src.runtime.context import ExecutionContext
from src.runtime.dispatcher import (
    ExecutionResult,
    dispatch_with_retry,
    NodeDispatcher,
)
from src.adapter.protocol import InboundMessage
from src.runtime.engine import (
    GraphEngine,
    ENGINE_MODE_FULL,
    ENGINE_MODE_LANGGRAPH,
)
from src.runtime.scheduler import InputScheduler


# ====================================================================
# ExecutionContext 测试
# ====================================================================

class TestExecutionContext:
    """ExecutionContext 创建与基础功能"""

    def test_create(self):
        """创建 ExecutionContext"""
        ctx = ExecutionContext(session_id="test-123")
        assert ctx.session_id == "test-123"
        assert ctx.execution_id is not None
        assert ctx.started_at is not None
        assert ctx.trace == []
        assert ctx.storage == {}

    def test_set_trace(self):
        """记录节点执行追踪"""
        ctx = ExecutionContext(session_id="test")
        ctx.set_trace("intent", {"type": "MOVE", "data": {}})
        ctx.set_trace("narrate", {"narrative": "你打开了门"})

        assert len(ctx.trace) == 2
        assert ctx.trace[0]["node"] == "intent"
        assert ctx.trace[1]["node"] == "narrate"

    def test_get_trace(self):
        """按节点名获取追踪"""
        ctx = ExecutionContext(session_id="test")
        ctx.set_trace("intent", {"a": 1})
        ctx.set_trace("skill", {"b": 2})
        ctx.set_trace("intent", {"c": 3})

        intent_traces = ctx.get_trace("intent")
        assert len(intent_traces) == 2

    def test_last_trace(self):
        """获取最后一条追踪"""
        ctx = ExecutionContext(session_id="test")
        assert ctx.last_trace() is None
        ctx.set_trace("a", {"x": 1})
        ctx.set_trace("b", {"y": 2})
        assert ctx.last_trace()["node"] == "b"

    def test_storage(self):
        """临时数据存储"""
        ctx = ExecutionContext(session_id="test")
        ctx.set("key1", "value1")
        ctx.set("key2", 42)
        assert ctx.get("key1") == "value1"
        assert ctx.get("key2") == 42
        assert ctx.get("nonexistent", "default") == "default"

    def test_storage_pop(self):
        """读取并移除"""
        ctx = ExecutionContext(session_id="test")
        ctx.set("key", "value")
        assert ctx.pop("key") == "value"
        assert ctx.get("key") is None

    def test_to_dict(self):
        """序列化到 dict"""
        ctx = ExecutionContext(session_id="test")
        ctx.set_trace("node1", {"r": "ok"})
        ctx.set("temp", "data")

        d = ctx.to_dict()
        assert d["session_id"] == "test"
        assert len(d["trace"]) == 1
        assert d["storage"]["temp"] == "data"

    def test_from_dict(self):
        """从 dict 反序列化"""
        original = ExecutionContext(session_id="test")
        original.set_trace("n", {"v": 1})
        original.set("k", "v")

        restored = ExecutionContext.from_dict(original.to_dict())
        assert restored.session_id == original.session_id
        assert restored.trace == original.trace
        assert restored.storage == original.storage

    def test_to_json(self):
        """序列化到 JSON"""
        ctx = ExecutionContext(session_id="test")
        json_str = ctx.to_json()
        assert isinstance(json_str, str)
        assert "test" in json_str

    def test_elapsed_seconds(self):
        """执行耗时"""
        ctx = ExecutionContext(session_id="test")
        elapsed = ctx.elapsed_seconds
        assert isinstance(elapsed, float)
        assert elapsed >= 0

    def test_repr(self):
        """字符串表示"""
        ctx = ExecutionContext(session_id="abcdefghijkl")
        r = repr(ctx)
        assert "abcdefgh" in r
        assert "trace_count=0" in r


# ====================================================================
# ExecutionResult 测试
# ====================================================================

class TestExecutionResult:
    """ExecutionResult 数据类"""

    def test_success_result(self):
        """成功的执行结果"""
        er = ExecutionResult(
            node_name="test",
            output={
                "state_patch": {"narrative": "hello"},
                "emitted_events": [{"type": "Test", "data": {}}],
                "next_node": "next",
                "control": None,
            },
        )
        assert er.success
        assert er.error is None
        assert er.state_patch == {"narrative": "hello"}
        assert len(er.emitted_events) == 1
        assert er.next_node == "next"
        assert er.control is None
        assert er.retry_count == 0

    def test_error_result(self):
        """失败的执行结果"""
        er = ExecutionResult(node_name="fail", error="测试错误")
        assert not er.success
        assert er.error == "测试错误"
        assert er.state_patch == {}
        assert er.emitted_events == []

    def test_control_semantics(self):
        """控制语义"""
        er_wait = ExecutionResult(
            node_name="dice",
            output={
                "control": "WAIT_DICE",
                "state_patch": {},
                "emitted_events": [],
                "next_node": None,
            },
        )
        assert er_wait.control == "WAIT_DICE"

        er_suspend = ExecutionResult(
            node_name="s",
            output={
                "control": "SUSPEND",
                "state_patch": {},
                "emitted_events": [],
                "next_node": None,
            },
        )
        assert er_suspend.control == "SUSPEND"

    def test_output_none(self):
        """output 为 None 时安全访问"""
        er = ExecutionResult(node_name="test")
        assert er.state_patch == {}
        assert er.emitted_events == []
        assert er.next_node is None
        assert er.control is None

    def test_to_dict(self):
        """序列化"""
        er = ExecutionResult(node_name="test", output={"state_patch": {}, "emitted_events": [], "next_node": None, "control": None})
        d = er.to_dict()
        assert d["node_name"] == "test"
        assert d["success"]
        assert d["error"] is None

    def test_repr(self):
        """字符串表示"""
        er_ok = ExecutionResult(node_name="ok")
        assert "status=OK" in repr(er_ok)
        er_err = ExecutionResult(node_name="fail", error="err")
        assert "status=ERR" in repr(er_err)


# ====================================================================
# dispatch_with_retry 测试
# ====================================================================

class TestDispatchWithRetry:
    """dispatch_with_retry 函数"""

    @pytest.mark.asyncio
    async def test_success(self):
        """正常执行"""
        async def good_node(state):
            return {"state_patch": {"n": "ok"}, "emitted_events": [], "next_node": None, "control": None}

        result = await dispatch_with_retry(good_node, {})
        assert result.success
        assert result.state_patch == {"narrative": "ok"} if False else result.state_patch == {"n": "ok"}
        assert result.retry_count == 0

    @pytest.mark.asyncio
    async def test_success_with_name(self):
        """指定 node_name"""
        async def fn(state):
            return {"state_patch": {}, "emitted_events": [], "next_node": None, "control": None}

        result = await dispatch_with_retry(fn, {}, node_name="my_custom_name")
        assert result.success
        assert result.node_name == "my_custom_name"

    @pytest.mark.asyncio
    async def test_timeout(self):
        """超时后返回错误"""
        async def slow_node(state):
            await asyncio.sleep(10)

        result = await dispatch_with_retry(slow_node, {}, max_retries=1, timeout=0.05)
        assert not result.success
        assert "超时" in result.error

    @pytest.mark.asyncio
    async def test_exception_retry(self):
        """异常后重试"""
        call_count = 0

        async def flaky_node(state):
            nonlocal call_count
            call_count += 1
            if call_count < 2:
                raise ValueError("暂时错误")
            return {"state_patch": {}, "emitted_events": [], "next_node": None, "control": None}

        result = await dispatch_with_retry(flaky_node, {}, max_retries=3)
        assert result.success
        assert result.retry_count == 1  # 第1次失败，第2次成功
        assert call_count == 2

    @pytest.mark.asyncio
    async def test_all_retries_exhausted(self):
        """所有重试均失败"""
        async def always_bad(state):
            raise RuntimeError("始终失败")

        result = await dispatch_with_retry(always_bad, {}, max_retries=2)
        assert not result.success
        assert "RuntimeError" in result.error
        assert result.retry_count == 2

    @pytest.mark.asyncio
    async def test_non_dict_output(self):
        """Node 返回非 dict 类型"""
        async def bad_return(state):
            return "not_a_dict"

        result = await dispatch_with_retry(bad_return, {})
        assert result.success  # 被兜底处理
        assert result.state_patch == {}
        assert result.emitted_events == []

    @pytest.mark.asyncio
    async def test_missing_fields_in_output(self):
        """Node 返回的 dict 缺少字段"""
        async def minimal_return(state):
            return {"state_patch": {"narrative": "minimal"}}

        result = await dispatch_with_retry(minimal_return, {})
        assert result.success
        assert result.state_patch == {"narrative": "minimal"}
        assert result.emitted_events == []
        assert result.next_node is None
        assert result.control is None


# ====================================================================
# NodeDispatcher 测试
# ====================================================================

class TestNodeDispatcher:
    """NodeDispatcher 类"""

    def test_default_params(self):
        """默认参数"""
        d = NodeDispatcher()
        assert d.default_max_retries == 3
        assert d.default_timeout == 30.0

    def test_custom_params(self):
        """自定义参数"""
        d = NodeDispatcher(default_max_retries=5, default_timeout=60.0)
        assert d.default_max_retries == 5
        assert d.default_timeout == 60.0

    @pytest.mark.asyncio
    async def test_dispatch(self):
        """执行分发"""
        d = NodeDispatcher()

        async def good(state):
            return {"state_patch": {}, "emitted_events": [], "next_node": None, "control": None}

        result = await d.dispatch(good, {})
        assert result.success

    def test_should_suspend(self):
        """挂起判定"""
        d = NodeDispatcher()

        ok = ExecutionResult(node_name="ok", output={"control": None, "state_patch": {}, "emitted_events": [], "next_node": None})
        assert not d.should_suspend(ok)

        wait = ExecutionResult(node_name="dice", output={"control": "WAIT_DICE", "state_patch": {}, "emitted_events": [], "next_node": None})
        assert d.should_suspend(wait)

        suspend = ExecutionResult(node_name="s", output={"control": "SUSPEND", "state_patch": {}, "emitted_events": [], "next_node": None})
        assert d.should_suspend(suspend)

        end = ExecutionResult(node_name="e", output={"control": "END_TURN", "state_patch": {}, "emitted_events": [], "next_node": None})
        assert d.should_suspend(end)

    def test_should_retry(self):
        """重试判定"""
        d = NodeDispatcher()
        retry = ExecutionResult(node_name="r", output={"control": "RETRY", "state_patch": {}, "emitted_events": [], "next_node": None})
        assert d.should_retry(retry)

        ok = ExecutionResult(node_name="ok", output={"control": None, "state_patch": {}, "emitted_events": [], "next_node": None})
        assert not d.should_retry(ok)

    def test_error_result_not_suspend(self):
        """错误结果不应挂起"""
        d = NodeDispatcher()
        err = ExecutionResult(node_name="fail", error="错误")
        assert not d.should_suspend(err)


# ====================================================================
# GraphEngine 测试
# ====================================================================

class TestGraphEngine:
    """GraphEngine 创建与基础功能"""

    def test_engine_mode_constants(self):
        """模式常量"""
        assert ENGINE_MODE_FULL == "full"
        assert ENGINE_MODE_LANGGRAPH == "langgraph"

    def test_create(self):
        """创建 Engine 实例"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        assert engine.graph is not None
        assert engine.mode == ENGINE_MODE_LANGGRAPH  # 默认模式
        assert engine.event_log is None
        assert engine.snapshot_mgr is None

    def test_create_full_mode(self):
        """创建 full 模式 Engine"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph, mode=ENGINE_MODE_FULL)
        assert engine.mode == ENGINE_MODE_FULL

    @pytest.mark.asyncio
    @pytest.mark.pg
    async def test_create_with_components(self):
        """创建带组件的 Engine"""
        from src.graph.keeper_graph import keeper_graph
        from src.memory.event_store import EventStore
        from src.state.event_log import EventLog
        from src.state.snapshot import SnapshotManager
        from src.tools.pg_manager import PgManager

        await PgManager.reset_instance()
        mgr = await PgManager.get_instance()
        if not mgr.available:
            pytest.skip("pgembed 不可用")
        await mgr.start()

        store = EventStore()
        elog = EventLog(store)
        snap = SnapshotManager(event_store=store)

        engine = GraphEngine(
            graph=keeper_graph,
            event_store=store,
            event_log=elog,
            snapshot_mgr=snap,
        )
        assert engine.event_log is elog
        assert engine.snapshot_mgr is snap
        await mgr.stop()

    @pytest.mark.asyncio
    async def test_initial_state_building(self):
        """构建初始 GameState"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)

        state = engine._prepare_state(
            player_input="你好",
            session_id="test-session",
            previous_state=None,
        )
        assert state["session_id"] == "test-session"
        assert state["player_input"] == "你好"
        assert state["beat_counter"] == 1
        assert state["status"] == "active"

    @pytest.mark.asyncio
    async def test_initial_state_with_previous(self):
        """复用上一轮 state"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)

        prev = create_initial_state("test-session", "测试")
        prev["beat_counter"] = 5
        prev["narrative"] = "上一轮的输出"

        state = engine._prepare_state(
            player_input="继续",
            session_id="test-session",
            previous_state=prev,
        )
        assert state["player_input"] == "继续"
        assert state["beat_counter"] == 6  # +1
        assert state["narrative"] == "上一轮的输出"  # 保留

    @pytest.mark.asyncio
    async def test_run_langgraph_mode(self):
        """langgraph 模式运行"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph, mode=ENGINE_MODE_LANGGRAPH)

        narrative, new_state = await engine.run("你好", session_id="test-session")
        assert isinstance(narrative, str)
        assert len(narrative) > 0
        # 应包含叙事文本
        assert narrative is not None
        # 应返回新状态
        assert isinstance(new_state, dict)
        assert new_state.get("session_id") == "test-session"

    @pytest.mark.asyncio
    async def test_run_with_previous_state(self):
        """连续多轮对话"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)

        prev = create_initial_state("test-session", "测试")
        prev["beat_counter"] = 3

        narrative, new_state = await engine.run(
            "我检查房间",
            session_id="test-session",
            previous_state=prev,
        )
        assert isinstance(narrative, str)
        assert len(narrative) > 0
        # 上一轮 state 的字段应保留
        assert new_state.get("session_id") == "test-session"

    @pytest.mark.asyncio
    async def test_run_error_handling(self):
        """异常时返回错误描述"""
        from src.graph.keeper_graph import keeper_graph
        # 用一个空的 state 模拟错误
        engine = GraphEngine(keeper_graph)

        # 注入空的 session_id 不应导致崩溃
        result, _ = await engine.run("test", session_id="")
        assert isinstance(result, str)
        # 要么是正常叙事，要么是错误消息
        assert len(result) > 0

    @pytest.mark.asyncio
    async def test_factory_create(self):
        """工厂方法创建"""
        from src.graph.keeper_graph import keeper_graph
        engine = await GraphEngine.create(
            keeper_graph,
            enable_event_log=False,
            enable_snapshot=False,
        )
        assert engine.graph is not None
        assert engine.event_log is None
        assert engine.snapshot_mgr is None

    @pytest.mark.asyncio
    async def test_factory_create_with_event_log(self):
        """工厂方法创建（带事件日志）"""
        from src.graph.keeper_graph import keeper_graph
        engine = await GraphEngine.create(
            keeper_graph,
            enable_event_log=True,
            enable_snapshot=False,
        )
        assert engine.event_log is not None
        await engine.close()

    def test_get_node_fn(self):
        """从 graph 中提取节点函数"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)

        fn = engine._get_node_fn("intent")
        assert fn is not None
        assert callable(fn)

        fn = engine._get_node_fn("narrate")
        assert fn is not None
        assert callable(fn)

        fn = engine._get_node_fn("__start__")
        assert fn is None  # 内部节点应被排除

        fn = engine._get_node_fn("nonexistent")
        assert fn is None


# ====================================================================
# InputScheduler 测试
# ====================================================================

class TestInputScheduler:
    """InputScheduler 多会话调度"""

    @pytest.mark.asyncio
    async def test_create(self):
        """创建调度器"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        assert scheduler.active_session_count == 0
        assert scheduler.session_ids == []

    # ── 辅助：从 session_id + text 构建 InboundMessage ──

    def _msg(self, session_id: str, text: str) -> InboundMessage:
        return InboundMessage(
            type="player_input",
            text=text,
            session_id=session_id,
            platform="test",
        )

    @pytest.mark.asyncio
    async def test_submit_new_session(self):
        """初次提交创建新会话"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        narrative = await scheduler.submit(self._msg("test-session", "你好"))
        assert isinstance(narrative, str)
        assert len(narrative) > 0
        assert scheduler.active_session_count == 1
        assert "test-session" in scheduler.session_ids

    @pytest.mark.asyncio
    async def test_submit_multi_turn(self):
        """多轮对话"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        n1 = await scheduler.submit(self._msg("session-1", "你好"))
        assert len(n1) > 0

        n2 = await scheduler.submit(self._msg("session-1", "我搜索房间"))
        assert len(n2) > 0

        slot = scheduler.get_session("session-1")
        assert slot is not None
        assert slot.turn_count == 2

    @pytest.mark.asyncio
    async def test_state_persists_across_turns(self):
        """多轮对话中 state 正确累积"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        # 第一轮：状态从无到有
        await scheduler.submit(self._msg("persist-test", "第一轮"))
        state1 = scheduler.get_session_state("persist-test")
        assert state1 is not None
        assert state1["beat_counter"] >= 1
        assert state1["session_id"] == "persist-test"
        first_narrative = state1.get("narrative", "")

        # 第二轮：beat_counter 应递增，narrative 可能保留
        await scheduler.submit(self._msg("persist-test", "第二轮"))
        state2 = scheduler.get_session_state("persist-test")
        assert state2 is not None
        assert state2["beat_counter"] >= state1["beat_counter"] + 1
        # session_id 应始终一致
        assert state2["session_id"] == "persist-test"

        # 第三轮：进一步累积
        await scheduler.submit(self._msg("persist-test", "第三轮"))
        state3 = scheduler.get_session_state("persist-test")
        assert state3 is not None
        assert state3["beat_counter"] >= state2["beat_counter"] + 1

    @pytest.mark.asyncio
    async def test_multiple_sessions(self):
        """多会话隔离"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        n1 = await scheduler.submit(self._msg("session-a", "你好"))
        n2 = await scheduler.submit(self._msg("session-b", "你好"))

        assert scheduler.active_session_count == 2
        assert scheduler.get_session("session-a") is not None
        assert scheduler.get_session("session-b") is not None

    @pytest.mark.asyncio
    async def test_get_session_state(self):
        """获取会话状态"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        await scheduler.submit(self._msg("test", "你好"))
        state = scheduler.get_session_state("test")
        assert state is not None
        assert state["session_id"] == "test"
        assert state["player_input"] == "你好"

    @pytest.mark.asyncio
    async def test_remove_session(self):
        """删除会话"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        await scheduler.submit(self._msg("test", "你好"))
        assert scheduler.active_session_count == 1

        removed = await scheduler.remove_session("test")
        assert removed
        assert scheduler.active_session_count == 0

        # 删除不存在的会话
        removed = await scheduler.remove_session("nonexistent")
        assert not removed

    @pytest.mark.asyncio
    async def test_clear_all_sessions(self):
        """清空所有会话"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        await scheduler.submit(self._msg("a", "你好"))
        await scheduler.submit(self._msg("b", "你好"))
        await scheduler.submit(self._msg("c", "你好"))

        assert scheduler.active_session_count == 3
        await scheduler.clear_all_sessions()
        assert scheduler.active_session_count == 0

    @pytest.mark.asyncio
    async def test_get_stats(self):
        """统计信息"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        await scheduler.submit(self._msg("a", "你好"))
        await scheduler.submit(self._msg("b", "你好"))

        stats = scheduler.get_stats()
        assert stats["total_sessions"] == 2
        assert stats["total_turns"] == 2
        assert stats["engine_mode"] == ENGINE_MODE_LANGGRAPH

    @pytest.mark.asyncio
    async def test_submit_with_queue(self):
        """队列提交"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        result = await scheduler.submit_with_queue(self._msg("test", "你好"))
        assert isinstance(result, str)
        assert scheduler.active_session_count == 1

    @pytest.mark.asyncio
    async def test_concurrent_submit_same_session(self):
        """同一会话的并发输入应串行处理"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        async def submit_and_check(sid, text):
            n = await scheduler.submit(self._msg(sid, text))
            assert isinstance(n, str)
            assert len(n) > 0
            return n

        results = await asyncio.gather(
            submit_and_check("shared-session", "输入A"),
            submit_and_check("shared-session", "输入B"),
        )
        assert len(results) == 2
        assert scheduler.get_session("shared-session").turn_count == 2

    @pytest.mark.asyncio
    async def test_auto_create_on_submit(self):
        """submit 会自动创建新会话"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        # 未手动创建，直接提交
        narrative = await scheduler.submit(self._msg("auto-create", "测试"))
        assert scheduler.get_session("auto-create") is not None

    @pytest.mark.asyncio
    async def test_cleanup_expired(self):
        """过期会话清理"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)

        # 使用极短的 timeout 以便测试清理
        scheduler = InputScheduler(engine, session_timeout=0.0)

        await scheduler.submit(self._msg("expired-session", "你好"))
        assert scheduler.active_session_count == 1

        # 手动触发清理
        await scheduler._cleanup_expired_sessions()
        assert scheduler.active_session_count == 0

    @pytest.mark.asyncio
    async def test_cleanup_task_lifecycle(self):
        """清理任务生命周期"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        await scheduler.start_cleanup_task()
        assert scheduler._cleanup_task is not None

        await scheduler.stop_cleanup_task()
        assert scheduler._cleanup_task is None

    @pytest.mark.asyncio
    async def test_close(self):
        """关闭调度器"""
        from src.graph.keeper_graph import keeper_graph
        engine = GraphEngine(keeper_graph)
        scheduler = InputScheduler(engine)

        await scheduler.submit(self._msg("test", "你好"))
        await scheduler.close()
        assert scheduler.active_session_count == 0
