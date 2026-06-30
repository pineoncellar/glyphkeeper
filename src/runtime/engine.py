"""
@File     :   engine.py
@Desc     :   Graph 执行引擎 — 系统核心入口
@Note     :   封装 LangGraph CompiledGraph，管理会话状态、事件溯源、快照

使用方式:
    engine = GraphEngine(keeper_graph)
    narrative = await engine.run("我搜索书桌", session_id="abc-123")
"""

from __future__ import annotations

import traceback
from datetime import datetime, timezone
from typing import Any, Optional

from langgraph.graph.state import CompiledStateGraph as CompiledGraph

from src.state.game_state import GameState, create_initial_state
from src.state.event_log import EventLog
from src.state.snapshot import SnapshotManager
from src.state.world_state import WorldManager
from src.state.state_validator import StateValidator
from src.memory.event_store import EventStore, create_event_store
from src.runtime.context import ExecutionContext
from src.runtime.dispatcher import (
    ExecutionResult,
    dispatch_with_retry,
    NodeDispatcher,
)
from src.tools import get_logger, get_settings

logger = get_logger(__name__)


# ====================================================================
# Engine 模式常量
# ====================================================================

ENGINE_MODE_FULL = "full"
"""完整模式: 遍历整个 Graph（intent → router → combat/investigate → narrate）"""

ENGINE_MODE_LANGGRAPH = "langgraph"
"""委托模式: 将 state 直接交给 CompiledGraph.ainvoke() 处理"""


# ====================================================================
# 每轮需重置的运行时字段
# ====================================================================

_RUNTIME_FIELDS: dict[str, object] = {
    "intent": None,               # intent_node 写
    "resolution": None,           # skill_node / rule_node 写
    "npc_dialogue": "",           # npc_dialogue_node 写
    "world_context": "",          # db_lookup_node + engine 写
    "physical_reality": "",       # db_lookup_node 写
    "rag_context": "",            # rag_lookup_node 写
    "archivist_result": None,     # skill_node 写（有线索时）
    "entity_name_map": {},        # db_lookup_node 写（NPC key→显示名映射，供消歧）
    "_llm_trace": None,           # narrator_node 写
    "narrative_output": "",       # narrator_node 写（给 state_extractor 消费）
    "pending_tier1_events": [],   # state_extractor 写
    "pending_tier2_facts": [],    # state_extractor 写
}
"""
_prepare_state 用此清单清除上一轮遗留在 GameState 中的运行时数据。
不在此清单中的持久字段（character / current_location / narrative 等）跨轮保留。
"""


# ====================================================================
# GraphEngine
# # ===================================================================


class GraphEngine:
    """Graph 执行引擎

    职责:
      1. 管理 CompiledGraph 实例
      2. 封装 graph.ainvoke() 调用
      3. 处理初始状态构建
      4. 管理事件溯源（EventLog）
      5. 管理状态快照（SnapshotManager）
      6. 提供统一的 run() 接口

    使用方式:
        engine = GraphEngine(keeper_graph)
        narrative = await engine.run("我搜索书桌", session_id="abc-123")

    模式说明:
      - full（默认）: 使用 Engine 内部流程，支持 suspend/resume/retry
      - langgraph: 委托给 CompiledGraph.ainvoke()，LangGraph 管理全部流程
    """

    def __init__(
        self,
        graph: CompiledGraph,
        mode: str = ENGINE_MODE_LANGGRAPH,
        event_store: Optional[EventStore] = None,
        event_log: Optional[EventLog] = None,
        snapshot_mgr: Optional[SnapshotManager] = None,
        world_mgr: Optional[WorldManager] = None,
        dispatcher: Optional[NodeDispatcher] = None,
    ):
        """
        Args:
            graph:        编译好的 LangGraph CompiledGraph
            mode:         引擎模式（full / langgraph）
            event_store:  事件溯源存储（None 则自动创建）
            event_log:    事件日志管理器（None 则自动创建）
            snapshot_mgr: 状态快照管理器（None 则自动创建）
            world_mgr:    世界状态管理器（None 则懒加载）
            dispatcher:   Node 分发器（None 则自动创建，仅 full 模式使用）
        """
        self.graph = graph
        self.mode = mode
        self._event_store = event_store
        self._event_log = event_log
        self._snapshot_mgr = snapshot_mgr
        self._world_manager = world_mgr
        self._dispatcher = dispatcher or NodeDispatcher(
            default_max_retries=3,
            default_timeout=30.0,
        )

    # ── 属性 ──

    @property
    def event_log(self) -> Optional[EventLog]:
        return self._event_log

    @property
    def snapshot_mgr(self) -> Optional[SnapshotManager]:
        return self._snapshot_mgr

    @property
    def world_manager(self) -> Optional[WorldManager]:
        """获取 WorldManager 实例（懒加载）"""
        if self._world_manager is None and self._event_store is not None:
            self._world_manager = WorldManager(
                event_store=self._event_store,
                event_log=self._event_log,
            )
        return self._world_manager

    # ── 核心执行 ──

    async def run(
        self,
        player_input: str,
        session_id: str,
        previous_state: Optional[GameState] = None,
        context: Optional[ExecutionContext] = None,
        auto_snapshot: bool = False,
        world_id: str = "",
    ) -> tuple[str, GameState]:
        """执行一次完整的 Graph 遍历

        参数:
            player_input:   玩家输入文本
            session_id:     会话 ID
            previous_state: 可选的上一轮状态（用于连续对话）
            context:        执行上下文（None 则自动创建）
            auto_snapshot:  是否在执行后自动创建快照
            world_id:       世界标识（覆盖 state 中的 world_id）

        返回:
            (narrative, new_state): 叙事文本与更新后的游戏状态
            执行失败时 narrative 为错误描述
        """
        ctx = context or ExecutionContext(session_id=session_id)

        # 构建初始 state
        state = self._prepare_state(
            player_input=player_input,
            session_id=session_id,
            previous_state=previous_state,
            world_id=world_id,
        )

        # ── 执行前：注入场景上下文（如果 WorldManager 可用且有当前位置） ──
        current_loc = state.get("current_location", "")
        if current_loc and self.world_manager:
            try:
                loc_view = await self.world_manager.get_location_view(
                    session_id, current_loc
                )
                # 只在不为空时覆盖，保留 db_lookup_node 的结果
                if loc_view and not state.get("world_context"):
                    state["world_context"] = loc_view
            except Exception as e:
                logger.debug(f"Engine.run: 场景上下文注入失败: {e}")

        logger.info(
            f"Engine.run: session={session_id[:8]} "
            f"mode={self.mode} "
            f"input={player_input[:50]}..."
        )

        # 执行
        try:
            if self.mode == ENGINE_MODE_LANGGRAPH:
                narrative, new_state = await self._run_langgraph(state, ctx)
            else:
                narrative, new_state = await self._run_full(state, ctx)

            # ── 执行后：由 navigation_node 处理 MOVE 位置更新，此处只做 WorldManager 同步 ──
            intent = new_state.get("intent") or {}
            resolved_loc = new_state.get("current_location", "")
            old_loc = state.get("current_location", "")
            if (
                intent.get("type") == "MOVE"
                and resolved_loc
                and resolved_loc != old_loc
                and self.world_manager
            ):
                character = new_state.get("character") or {}
                char_name = character.get("name", "")
                if char_name:
                    try:
                        await self.world_manager.move_entity(
                            session_id=session_id,
                            entity_key=char_name,
                            target_location_key=resolved_loc,
                            source_node="navigation_node",
                        )
                        logger.info(
                            f"Engine.run: 角色 '{char_name}' {old_loc} → {resolved_loc}"
                        )
                    except Exception as e:
                        logger.debug(f"Engine.run: WorldManager 同步失败: {e}")

            # 自动快照（对 new_state 做）
            if auto_snapshot and self._snapshot_mgr:
                try:
                    snap_id = await self._snapshot_mgr.create(new_state, label="auto")
                    logger.debug(f"Engine.run: 快照已创建 {snap_id[:8]}")
                except Exception as e:
                    logger.warning(f"Engine.run: 快照创建失败: {e}")

            logger.info(
                f"Engine.run: OK session={session_id[:8]} "
                f"narrative_len={len(narrative)}"
            )
            return narrative, new_state

        except Exception as e:
            error_msg = f"{type(e).__name__}: {e}"
            logger.error(f"Engine.run: FAILED {error_msg}\n{traceback.format_exc()}")
            return f"（系统异常：{error_msg}）", state

    # ── langgraph 模式 ──

    async def _run_langgraph(
        self,
        state: GameState,
        ctx: ExecutionContext,
    ) -> tuple[str, GameState]:
        """委托模式：直接调用 CompiledGraph.ainvoke()，返回 (叙事文本, 新状态)"""
        result = await self.graph.ainvoke(state)

        # 记录追踪
        ctx.set_trace("graph", {"keys": list(result.keys())})

        # 提取 narrative
        narrative = result.get("narrative", "")

        # 记录事件（如果 event_log 可用）
        if self._event_log:
            await self._record_graph_event(result, ctx)

        # 状态追赶：异步执行 Tier 1 写入 + Tier 2 索引，不阻塞返回
        tier1_events = result.get("pending_tier1_events", [])
        tier2_facts = result.get("pending_tier2_facts", [])
        if tier1_events or tier2_facts:
            asyncio.create_task(self._async_state_catchup(
                tier1_events=tier1_events,
                tier2_facts=tier2_facts,
                session_id=state.get("session_id", ""),
                world_id=state.get("world_id", ""),
                current_state=result,
            ))

        return narrative, result

    # ── full 模式 ──

    async def _run_full(
        self,
        state: GameState,
        ctx: ExecutionContext,
    ) -> str:
        """完整模式：Engine 逐节点调度执行"""
        narrative = ""
        current_node = "intent"
        safety_counter = 0
        MAX_STEPS = 50  # 最大执行步数，防止无限循环

        while current_node and safety_counter < MAX_STEPS:
            safety_counter += 1

            # 获取节点函数
            node_fn = self._get_node_fn(current_node)
            if node_fn is None:
                logger.error(f"Engine: 未知节点 '{current_node}'，终止执行")
                break

            # 执行节点
            result = await self._dispatcher.dispatch(
                node_fn=node_fn,
                state=state,
                node_name=current_node,
            )

            # 记录追踪
            ctx.set_trace(current_node, result.to_dict())

            if not result.success:
                logger.error(f"Engine: 节点 '{current_node}' 执行失败: {result.error}")
                self._add_error(state, f"[{current_node}] {result.error}")
                break

            # 应用 state_patch
            if result.state_patch:
                from src.state.reducer import reduce_state
                state = reduce_state(state, result.state_patch)

            # 记录事件
            if self._event_log and result.emitted_events:
                for evt in result.emitted_events:
                    await self._record_event(
                        state, evt, current_node, ctx
                    )

            # 检查控制语义
            if self._dispatcher.should_suspend(result):
                logger.debug(f"Engine: 节点 '{current_node}' 触发挂起 ({result.control})")
                # 如果挂起时已有 narrative，保留它
                if result.state_patch and "narrative" in result.state_patch:
                    narrative = result.state_patch.get("narrative", narrative)
                break

            # 路由到下一个节点
            next_node = result.next_node
            if next_node is None:
                # 从 state 提取 narrative
                narrative = state.get("narrative", narrative)
                break

            current_node = next_node

        if safety_counter >= MAX_STEPS:
            logger.warning(f"Engine: 达到最大执行步数 {MAX_STEPS}")
            self._add_error(state, f"达到最大执行步数 {MAX_STEPS}")

        return narrative, state

    # ── 辅助方法 ──

    def _prepare_state(
        self,
        player_input: str,
        session_id: str,
        previous_state: Optional[GameState] = None,
        world_id: str = "",
    ) -> GameState:
        """构建或复用 GameState，同时清除上一轮的运行时字段"""
        if previous_state:
            state: GameState = dict(previous_state)
            state["player_input"] = player_input
            state["beat_counter"] = state.get("beat_counter", 0) + 1
        else:
            state = create_initial_state(
                session_id=session_id,
                scenario_name="",
            )
            state["player_input"] = player_input
            state["beat_counter"] = 1

        # 如果调用方传入了 world_id，覆盖 state 中的值（用于多世界路由）
        if world_id:
            state["world_id"] = world_id

        # 重置运行时字段，防止跨轮幽灵数据残留
        for field, default in _RUNTIME_FIELDS.items():
            state[field] = default
        return state

    def _get_node_fn(self, node_name: str):
        """根据节点名获取对应的异步函数

        从 CompiledStateGraph.nodes 中提取可调用的 Node 函数。
        PregelNode.bound.func 存储了注册时的原始函数。

        注意: 排除 '__start__' 等 LangGraph 内部节点。
        """
        if node_name.startswith("__"):
            return None

        try:
            node_spec = self.graph.nodes.get(node_name)
            if node_spec is None:
                return None
            # PregelNode.bound.func 是原始注册的函数
            fn = getattr(node_spec.bound, "func", None)
            if fn is not None and callable(fn):
                return fn
            # 兜底：尝试 bound 自身的 invoke
            if hasattr(node_spec.bound, "invoke") and callable(node_spec.bound.invoke):
                return node_spec.bound.invoke
        except (AttributeError, KeyError, TypeError):
            pass
        return None

    async def _record_event(
        self,
        state: GameState,
        event_data: dict,
        source_node: str,
        ctx: ExecutionContext,
    ):
        """记录单条事件到 EventLog"""
        if not self._event_log:
            return
        try:
            await self._event_log.record_and_apply(
                current=state,
                patch=event_data.get("state_patch", {}),
                event_type=event_data.get("type", "NodeExecution"),
                source_node=source_node,
                parent_event_id=ctx.execution_id,
                extra_data=event_data.get("extra"),
            )
        except Exception as e:
            logger.warning(f"Engine: 事件记录失败: {e}")

    async def _record_graph_event(
        self,
        result: dict,
        ctx: ExecutionContext,
    ):
        """记录 Graph 执行的完整事件流

        根据 intent/resolution 内容差异化记录事件类型，确保事件溯源可回放。
        """
        if not self._event_log:
            return
        try:
            intent = result.get("intent") or {}
            resolution = result.get("resolution") or {}
            intent_type = intent.get("type", "")
            player_input = result.get("player_input", "")
            session_id = result.get("session_id", "")

            # 1. 始终记录 PlayerInput 事件
            await self._event_log.record_and_apply(
                current=result,
                patch={
                    "beat_counter": result.get("beat_counter", 0),
                },
                event_type="PlayerInput",
                source_node="engine",
                parent_event_id=ctx.execution_id,
                extra_data={
                    "text": player_input,
                    "intent_type": intent_type,
                },
            )

            # 2. 根据 resolution 内容判定事件子类型
            check_type = resolution.get("check_type", "")
            success_label = resolution.get("success_label", "")

            if intent_type == "COMBAT_ACTION" or result.get("game_phase") == "combat":
                await self._event_log.record_and_apply(
                    current=result,
                    patch={},
                    event_type="CombatRound",
                    source_node="engine",
                    parent_event_id=ctx.execution_id,
                    extra_data={
                        "action": intent.get("data", {}).get("action", ""),
                        "target": intent.get("data", {}).get("target", ""),
                        "success": success_label,
                        "resolution": resolution,
                    },
                )

            elif check_type in ("skill", "stat", "opposed") and success_label:
                await self._event_log.record_and_apply(
                    current=result,
                    patch={},
                    event_type="SkillCheck",
                    source_node="engine",
                    parent_event_id=ctx.execution_id,
                    extra_data={
                        "check_type": check_type,
                        "skill_name": resolution.get("skill_name", ""),
                        "success_label": success_label,
                        "roll_value": resolution.get("roll_value"),
                        "resolution": resolution,
                    },
                )

            # 3. narrative 更新事件（兜底 / 非战斗非检定轮）
            narrative = result.get("narrative", "")
            if narrative and not success_label and intent_type != "COMBAT_ACTION":
                await self._event_log.record_and_apply(
                    current=result,
                    patch={"narrative": narrative},
                    event_type="NarrativeOutput",
                    source_node="engine",
                    parent_event_id=ctx.execution_id,
                    extra_data={
                        "intent_type": intent_type,
                        "player_input": player_input[:60],
                    },
                )

        except Exception as e:
            logger.warning(f"Engine: Graph 事件记录失败: {e}")

    # ── 异步状态追赶 ──

    async def _async_state_catchup(
        self,
        tier1_events: list[dict],
        tier2_facts: list[str],
        session_id: str,
        world_id: str,
        current_state: GameState,
    ):
        """后台异步执行状态追赶

        先验证 Tier 1 事件 → 通过后写入 EventStore + Reducer。
        再异步写入 Tier 2 事实到 LightRAG（最终一致性，失败不重试）。

        注意：Tier 1 事件应用到的是 current_state 的快照，
        current_state 是 Graph 执行完毕时的最终状态。
        Validator 校验失败的事件静默丢弃，不阻塞后续事件处理。
        """
        validator = StateValidator()

        # Tier 1：验证后通过 EventLog 写入 EventStore + Reducer
        for event in tier1_events:
            try:
                result = await validator.validate(event, current_state)
                if result.passed and result.corrected_event and self._event_log:
                    event_type = event.get("event_type", "")
                    await self._event_log.record_and_apply(
                        current=current_state,
                        patch=result.corrected_event,
                        event_type=f"Tier1{event_type}",
                        source_node="state_extractor_node",
                        extra_data={
                            "original_event": event,
                        },
                    )
                    logger.debug(
                        f"StateCatchup: Tier1 {event_type} 已写入 "
                        f"session={session_id[:8]}"
                    )
                elif not result.passed:
                    logger.debug(
                        f"StateCatchup: Tier1 事件被拦截: {result.reason} "
                        f"event={event}"
                    )
            except Exception as e:
                logger.warning(
                    f"StateCatchup: Tier1 处理异常（跳过）: {e}"
                )

        # Tier 2：异步写入 LightRAG（最终一致性，不阻塞玩家下一轮输入）
        if tier2_facts and world_id:
            try:
                from src.memory.vector_store import VectorStore
                vs = await VectorStore.get_instance(
                    domain="world",
                    world_id=world_id,
                )
                await vs.insert(tier2_facts, source_type="state_extracted")
                logger.info(
                    f"StateCatchup: {len(tier2_facts)} 条 Tier 2 事实 "
                    f"已写入 LightRAG world={world_id}"
                )
            except Exception as e:
                logger.warning(
                    f"StateCatchup: Tier 2 LightRAG 写入失败（可忽略）: {e}"
                )

    @staticmethod
    def _add_error(state: GameState, error_msg: str):
        """向 state 中添加错误记录"""
        errors = state.get("errors", [])
        if isinstance(errors, list):
            errors.append(error_msg)
            state["errors"] = errors

    # ── 生命周期管理 ──

    async def close(self):
        """关闭引擎，释放资源"""
        if self._event_store:
            try:
                await self._event_store.close()
            except Exception as e:
                logger.warning(f"Engine.close: EventStore 关闭异常: {e}")
        if self._snapshot_mgr:
            try:
                await self._snapshot_mgr.close()
            except Exception as e:
                logger.warning(f"Engine.close: SnapshotManager 关闭异常: {e}")
        logger.info("Engine: 已关闭")

    # ── 状态重建 ──

    async def replay_to_state(
        self,
        session_id: str,
    ) -> Optional[GameState]:
        """回放事件重建指定会话的状态

        需要 event_log 已配置。
        """
        if not self._event_log:
            logger.warning("Engine.replay_to_state: event_log 未配置")
            return None
        try:
            state = await self._event_log.replay_to_state(session_id)
            logger.info(
                f"Engine.replay_to_state: session={session_id[:8]} "
                f"state_keys={list(state.keys())}"
            )
            return state
        except Exception as e:
            logger.error(f"Engine.replay_to_state: 失败 {e}")
            return None

    # ── 工厂方法 ──

    @classmethod
    async def create(
        cls,
        graph: CompiledGraph,
        mode: str = ENGINE_MODE_LANGGRAPH,
        session_id: Optional[str] = None,
        enable_event_log: bool = False,
        enable_snapshot: bool = False,
    ) -> GraphEngine:
        """异步工厂方法 — 创建带完整基础设施的 Engine

        Args:
            graph:             编译好的 CompiledGraph
            mode:              引擎模式
            session_id:        可选的测试会话 ID
            enable_event_log:  是否启用事件溯源
            enable_snapshot:   是否启用状态快照

        Returns:
            配置好的 GraphEngine 实例
        """
        event_store = None
        event_log = None
        snapshot_mgr = None

        if enable_event_log:
            event_store = await create_event_store()
            event_log = EventLog(event_store)

        if enable_snapshot:
            snapshot_mgr = SnapshotManager(
                event_store=event_store,
            )

        return cls(
            graph=graph,
            mode=mode,
            event_store=event_store,
            event_log=event_log,
            snapshot_mgr=snapshot_mgr,
        )
