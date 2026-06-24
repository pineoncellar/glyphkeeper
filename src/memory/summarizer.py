"""
对话摘要与记忆压缩

职责:
  - 将原始对话记录压缩为结构化摘要
  - 提取关键事件、实体状态变更、线索
  - 控制摘要 Token 预算（避免 Context Window 溢出）
  - 支持多级摘要（实时摘要 → 轮次摘要 → 全局摘要）

方法:
  - summarize_dialogue(records) -> Summary
  - extract_facts(narrative) -> List[Fact]
  - merge_summaries(summaries) -> Summary

策略:
  - TokenCountStrategy: 达到阈值触发摘要
  - TimeBasedStrategy: 定时触发
"""
