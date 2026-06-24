"""
@File     :   context.py
@Desc     :   执行上下文管理 — 封装单次 Graph 执行的上下文
@Note     :   支持序列化/反序列化（用于暂停/恢复）

使用方式:
    ctx = ExecutionContext(session_id="abc-123")
    ctx.set_trace("intent", {"type": "MOVE", ...})
    ctx.get_trace("intent")
    data = ctx.to_dict()  # 序列化
"""

from __future__ import annotations

import json
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Optional


@dataclass
class ExecutionContext:
    """
    单次 Graph 执行的上下文。

    封装执行追踪信息、执行元数据、临时存储空间。
    不直接包含 GameState — 状态由 engine 管理。

    字段:
        session_id:    当前会话 ID
        execution_id:  本次执行的唯一标识
        started_at:    执行开始时间
        trace:         节点执行追踪日志
        storage:       临时数据存储（Node 间传递非 state 数据）
    """

    session_id: str
    execution_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    started_at: str = field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    trace: list[dict] = field(default_factory=list)
    storage: dict[str, Any] = field(default_factory=dict)

    # ── 追踪管理 ──

    def set_trace(self, node_name: str, result: dict):
        """记录节点执行追踪

        Args:
            node_name: 节点名称
            result:    节点返回的 dict
        """
        self.trace.append({
            "node": node_name,
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "result_summary": {
                k: (str(v)[:80] if not isinstance(v, (int, float, bool)) else v)
                for k, v in result.items()
            },
        })

    def get_trace(self, node_name: str) -> list[dict]:
        """获取指定节点的所有追踪记录"""
        return [t for t in self.trace if t["node"] == node_name]

    def last_trace(self) -> Optional[dict]:
        """获取最后一条追踪记录"""
        return self.trace[-1] if self.trace else None

    # ── 临时存储 ──

    def set(self, key: str, value: Any):
        """存储临时数据（不会持久化到 state）"""
        self.storage[key] = value

    def get(self, key: str, default: Any = None) -> Any:
        """读取临时数据"""
        return self.storage.get(key, default)

    def pop(self, key: str, default: Any = None) -> Any:
        """读取并移除临时数据"""
        return self.storage.pop(key, default)

    # ── 序列化 ──

    def to_dict(self) -> dict:
        """序列化为 dict（用于持久化）"""
        return {
            "session_id": self.session_id,
            "execution_id": self.execution_id,
            "started_at": self.started_at,
            "trace": self.trace,
            "storage": self.storage,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ExecutionContext:
        """从 dict 反序列化"""
        return cls(
            session_id=data.get("session_id", ""),
            execution_id=data.get("execution_id", str(uuid.uuid4())),
            started_at=data.get("started_at", datetime.now(timezone.utc).isoformat()),
            trace=data.get("trace", []),
            storage=data.get("storage", {}),
        )

    def to_json(self, ensure_ascii: bool = False) -> str:
        """序列化为 JSON 字符串"""
        return json.dumps(self.to_dict(), ensure_ascii=ensure_ascii, default=str)

    # ── 辅助 ──

    @property
    def elapsed_seconds(self) -> float:
        """从执行开始到现在的秒数"""
        try:
            start = datetime.fromisoformat(self.started_at)
            return (datetime.now(timezone.utc) - start).total_seconds()
        except (ValueError, TypeError):
            return 0.0

    def __repr__(self) -> str:
        return (
            f"ExecutionContext(session={self.session_id[:8]}, "
            f"exec={self.execution_id[:8]}, "
            f"trace_count={len(self.trace)})"
        )
