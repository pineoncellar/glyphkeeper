"""
@File     :   runtime/__init__.py
@Desc     :   runtime 包 — Graph 执行引擎（系统 CPU）

职责:
  - 执行 Graph 拓扑（通过 CompiledGraph）
  - 调度多玩家输入（InputScheduler）
  - 分发 Node 执行（dispatch_with_retry / NodeDispatcher）
  - 管理执行上下文（ExecutionContext / suspend / resume）
  - 事件溯源与快照集成

使用方式:
    from src.runtime.engine import GraphEngine
    from src.runtime.scheduler import InputScheduler
    from src.runtime.context import ExecutionContext
    from src.runtime.dispatcher import dispatch_with_retry, NodeDispatcher
"""

from src.runtime.engine import GraphEngine, ENGINE_MODE_FULL, ENGINE_MODE_LANGGRAPH
from src.runtime.context import ExecutionContext
from src.runtime.dispatcher import (
    dispatch_with_retry,
    NodeDispatcher,
    ExecutionResult,
)

# InputScheduler 延迟导入（避免 adapter ↔ runtime 循环引用）
_InputScheduler = None

def get_scheduler():
    global _InputScheduler
    if _InputScheduler is None:
        from src.runtime.scheduler import InputScheduler
        _InputScheduler = InputScheduler
    return _InputScheduler

__all__ = [
    # engine
    "GraphEngine",
    "ENGINE_MODE_FULL",
    "ENGINE_MODE_LANGGRAPH",
    # scheduler
    "InputScheduler",
    # context
    "ExecutionContext",
    # dispatcher
    "dispatch_with_retry",
    "NodeDispatcher",
    "ExecutionResult",
]
