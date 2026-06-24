"""
状态快照管理

职责:
  - 定期创建游戏状态的完整快照
  - 支持基于快照的快速恢复（避免回放全部事件）
  - 管理快照版本与过期策略
  - 提供时间线回溯能力

使用方式:
    snapshot = await SnapshotManager.create(session_id)
    await SnapshotManager.restore(snapshot_id)
"""
