"""
守密人主 Graph（核心流程）

职责:
  - 定义 Keeper 系统的主执行流程拓扑
  - 节点序列: intent → route → rule_check → state_update → narrate → wait
  - 管理节点间的边与路由条件
  - 定义 Graph 的入口节点和终止条件

流程示意:
    [PlayerInput] → [IntentNode] → [RouterGraph]
        ↓ success               ↓
    [RuleNodes] → [StateUpdate] → [NarratorNode] → [Wait]
        ↓ fail
    [NarratorNode: 描述失败原因] → [Wait]
"""
