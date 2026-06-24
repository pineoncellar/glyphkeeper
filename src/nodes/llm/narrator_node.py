"""
叙事生成节点 (替代旧 Writer Agent)

职责:
  - 将裁决结果转换为沉浸式克苏鲁风格叙事文本
  - 根据 ResolutionResult 的状态决定叙事方向
  - 注入场景氛围与角色情感描写
  - 使用 standard 级别 LLM

输入: ResolutionResult + Context
输出: NarrativeText (str)

注意: 本节点只负责"表达"，不修改任何游戏状态
"""
