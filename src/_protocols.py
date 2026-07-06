# -*- coding: utf-8 -*-
"""
@File     :   _protocols.py
@Desc     :   GlyphKeeper 统一执行协议 — 历史参考
@Note     :   NodeInput/NodeOutput 为早期设计，当前节点统一使用
              async def node(state: GameState) -> dict 签名。
              保留此文件供向后兼容参考。
"""

from typing import TypedDict, Any, Optional
from dataclasses import dataclass, field


# ------- Node 统一接口 -------

class NodeInput(TypedDict):
    """Node 执行时的输入

    state_view : 裁剪后的 state 投影（Node 看不到完整 GameState）
    event      : 触发本次执行的事件
    context    : 执行上下文（session_id, 重试次数, 历史回调等）
    memory     : RAG 检索结果 + 对话历史摘要（LLM Node 的语境来源）
    """
    state_view: dict          # 视图（裁剪后的 state），非完整 GameState
    event: dict               # 触发本次执行的事件
    context: dict             # 执行上下文（session_id, 历史回调等）
    memory: dict              # RAG 检索结果 + 对话历史摘要


class NodeOutput(TypedDict):
    """Node 执行后必须返回的结构

    state_patch    : 状态增量更新（禁止直接修改 state）
    emitted_events : 本次执行产生的事件列表
    next_node      : 下一个节点名，None 表示挂起等待
    control        : runtime 控制语义（非单纯路由）
    """
    state_patch: dict                  # 状态增量更新（禁止直接修改 state）
    emitted_events: list[dict]         # 本次执行产生的事件列表
    next_node: Optional[str]           # 下一个节点名，None 表示挂起等待
    control: Optional[str]             # runtime 控制语义
    # "WAIT_DICE"  — 等待玩家掷骰
    # "SUSPEND"    — 挂起执行流（等外部事件）
    # "RETRY"      — Node 执行失败，请求重试
    # "END_TURN"   — 本轮结束，等下次玩家输入


# ------- State Mutation 协议 -------

# 所有 state 修改必须通过：
#    Node 返回 state_patch → Engine 调用 Reducer → Reducer 生成新 State
# Node 永远不能直接操作数据库或修改传入的 state 对象


@dataclass
class Event:
    """不可变事件（Event Sourcing 的基本单位）

    source_node      : 产生此事件的 Node 名称（因果溯源）
    parent_event_id  : 父事件 ID（构建因果链，debug 回放用）
    """
    type: str                         # 事件类型，如 "EntityMoved", "SanityLost"
    session_id: str
    data: dict                        # 事件载荷
    source_node: str                  # 产生此事件的 Node 名
    parent_event_id: Optional[str] = None  # 父事件 ID（因果链）
    timestamp: Optional[str] = None
    version: int = 1


@dataclass
class StatePatch:
    """状态增量更新包"""
    updates: dict[str, Any] = field(default_factory=dict)      # 要更新的字段
    deletes: list[str] = field(default_factory=list)            # 要删除的字段


# ------- Reducer Contract（关键缺失补全） -------

# Reducer 是唯一允许修改 State 的地方。以下定义其输入输出契约。

class ReducerInput(TypedDict):
    """Reducer 的输入"""
    state: dict               # 当前完整 state
    patch: dict               # Node 返回的 state_patch
    events: list[dict]        # 待应用的 events


class ReducerOutput(TypedDict):
    """Reducer 的输出"""
    new_state: dict           # 合并后的新 state
    applied_events: list[dict]  # 已应用的 events（已写入 EventStore）


# ------- Node 函数签名约束 -------

# 每个 Node 必须是以下形式的异步函数：
#   async def node_name(input: NodeInput) -> NodeOutput:
#       ...
#       return {"state_patch": ..., "emitted_events": ..., ...}
#
# 注意：参数不再是 GameState，而是 NodeInput（含裁剪视图 + 记忆）
#
# 禁止的形式：
#   async def node_name(input_str: str, config: dict) -> str:
#   直接修改 state 的某个字段
#   直接操作数据库或调用 LLM 而不通过工具
