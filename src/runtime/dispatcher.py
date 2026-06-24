"""
@File     :   dispatcher.py
@Desc     :   Node 分发器 — 处理 Node 执行的容错与生命周期
@Note     :   包含内部 ExecutionResult 结构（Engine 内部使用，不对外暴露）

使用方式:
    result = await dispatch_with_retry(node_fn, state, max_retries=3, timeout=30.0)
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Coroutine, Optional

from src.config import get_logger
from src._protocols import NodeOutput, Event

logger = get_logger(__name__)


# ====================================================================
# 内部结构：ExecutionResult（Engine 内部用，不对外暴露）
# ====================================================================
# 设计决策：这是 Engine 的内部实现细节，不是 Node ↔ Engine 的契约。
#          塞进 _protocols.py 会模糊该文件的单一职责。
#          详见 开发路线.md "设计决策记录：ExecutionResult"。
# ====================================================================


@dataclass
class ExecutionResult:
    """Node 执行结果 — Engine 内部使用

    node_name    : 执行的 Node 名称
    output       : Node 返回的 NodeOutput（state_patch + emitted_events + next_node + control）
    events       : 已物化的事件列表（source_node/timestamp/version 已填充）
    duration_ms  : 执行耗时（毫秒）
    retry_count  : 重试次数
    error        : 执行错误（None 表示成功）
    """

    node_name: str
    output: Optional[dict] = None
    events: list[dict] = field(default_factory=list)
    duration_ms: float = 0.0
    retry_count: int = 0
    error: Optional[str] = None

    @property
    def success(self) -> bool:
        """执行是否成功"""
        return self.error is None

    @property
    def control(self) -> Optional[str]:
        """获取 Node 返回的控制语义"""
        if self.output:
            return self.output.get("control")
        return None

    @property
    def next_node(self) -> Optional[str]:
        """获取 Node 返回的下一个节点名"""
        if self.output:
            return self.output.get("next_node")
        return None

    @property
    def state_patch(self) -> dict:
        """获取 Node 返回的 state_patch"""
        if self.output:
            return self.output.get("state_patch", {})
        return {}

    @property
    def emitted_events(self) -> list[dict]:
        """获取 Node 返回的事件列表"""
        if self.output:
            return self.output.get("emitted_events", [])
        return []

    def to_dict(self) -> dict:
        return {
            "node_name": self.node_name,
            "success": self.success,
            "duration_ms": self.duration_ms,
            "retry_count": self.retry_count,
            "error": self.error,
            "control": self.control,
            "next_node": self.next_node,
        }

    def __repr__(self) -> str:
        status = "OK" if self.success else f"ERR:{self.error}"
        return (
            f"ExecutionResult(node={self.node_name}, "
            f"status={status}, "
            f"dur={self.duration_ms:.0f}ms, "
            f"retry={self.retry_count})"
        )


# ====================================================================
# Dispatch 函数
# ====================================================================


async def dispatch_with_retry(
    node_fn: Callable,
    state: dict,
    max_retries: int = 3,
    timeout: float = 30.0,
    node_name: str = "",
) -> ExecutionResult:
    """执行 Node 函数，带重试和超时

    Args:
        node_fn:      Node 异步函数（async def fn(state: dict) -> dict）
        state:        当前 GameState
        max_retries:  最大重试次数（默认 3）
        timeout:      单次执行超时秒数（默认 30s）
        node_name:    Node 名称（用于日志和追踪）

    Returns:
        ExecutionResult — 封装执行结果、耗时、重试信息
    """
    last_error: Optional[Exception] = None
    name = node_name or getattr(node_fn, "__name__", "unknown")
    retry_count = 0

    for attempt in range(1, max_retries + 1):
        start_time = time.monotonic()
        try:
            output = await asyncio.wait_for(
                node_fn(state),
                timeout=timeout,
            )

            duration_ms = (time.monotonic() - start_time) * 1000

            # 验证 output 是 dict
            if not isinstance(output, dict):
                logger.warning(
                    f"dispatch: {name} 返回非 dict 类型 {type(output).__name__}"
                )
                output = {"state_patch": {}, "emitted_events": [], "next_node": None}

            # 确保包含必要的 NodeOutput 字段
            output.setdefault("state_patch", {})
            output.setdefault("emitted_events", [])
            output.setdefault("next_node", None)
            output.setdefault("control", None)

            if attempt > 1:
                logger.info(f"dispatch: {name} 第 {attempt} 次重试成功")

            return ExecutionResult(
                node_name=name,
                output=output,
                duration_ms=duration_ms,
                retry_count=attempt - 1,
            )

        except asyncio.TimeoutError:
            duration_ms = (time.monotonic() - start_time) * 1000
            last_error = TimeoutError(
                f"Node '{name}' 执行超时 ({timeout}s)"
            )
            logger.warning(
                f"dispatch: {name} 超时 (attempt {attempt}/{max_retries}, "
                f"took {duration_ms:.0f}ms)"
            )
            retry_count = attempt

        except Exception as e:
            duration_ms = (time.monotonic() - start_time) * 1000
            last_error = e
            logger.warning(
                f"dispatch: {name} 异常: {e} (attempt {attempt}/{max_retries}, "
                f"took {duration_ms:.0f}ms)"
            )
            retry_count = attempt

    # 所有重试均失败
    error_msg = f"{type(last_error).__name__}: {last_error}"
    logger.error(f"dispatch: {name} 超过最大重试次数 ({max_retries})，最后错误: {error_msg}")

    return ExecutionResult(
        node_name=name,
        output={
            "state_patch": {"errors": [error_msg]},
            "emitted_events": [],
            "next_node": None,
            "control": None,
        },
        error=error_msg,
        retry_count=retry_count,
        duration_ms=0.0,
    )


# ====================================================================
# NodeDispatcher 类（高级接口）
# ====================================================================


class NodeDispatcher:
    """Node 分发器 — 管理 Node 执行生命周期

    封装 dispatch_with_retry，提供配置化的默认参数。
    """

    def __init__(
        self,
        default_max_retries: int = 3,
        default_timeout: float = 30.0,
    ):
        """
        Args:
            default_max_retries: 全局默认最大重试次数
            default_timeout:     全局默认超时秒数
        """
        self.default_max_retries = default_max_retries
        self.default_timeout = default_timeout

    async def dispatch(
        self,
        node_fn: Callable,
        state: dict,
        node_name: str = "",
        max_retries: Optional[int] = None,
        timeout: Optional[float] = None,
    ) -> ExecutionResult:
        """分发执行一个 Node

        Args:
            node_fn:     Node 异步函数
            state:       当前 GameState
            node_name:   Node 名称（用于日志）
            max_retries: 覆盖默认重试次数
            timeout:     覆盖默认超时

        Returns:
            ExecutionResult
        """
        return await dispatch_with_retry(
            node_fn=node_fn,
            state=state,
            max_retries=max_retries or self.default_max_retries,
            timeout=timeout or self.default_timeout,
            node_name=node_name,
        )

    def should_suspend(self, result: ExecutionResult) -> bool:
        """判断 Node 执行结果是否应挂起执行流

        控制语义:
            "WAIT_DICE"  — 等待玩家掷骰
            "SUSPEND"    — 挂起执行流（等外部事件）
            "RETRY"      — Node 执行失败，请求重试
            "END_TURN"   — 本轮结束，等下次玩家输入
        """
        if not result.success:
            return False  # 失败由 engine 处理
        control = result.control
        return control in ("WAIT_DICE", "SUSPEND", "END_TURN")

    def should_retry(self, result: ExecutionResult) -> bool:
        """判断是否应重试"""
        return result.control == "RETRY"
