"""
理智规则节点

职责:
  - 执行理智检定（Sanity Check）
  - 理智损失计算
  - 临时疯狂与 indefinite insanity 处理
  - 调用 domain/sanity_rules.py 的确定性逻辑

输入: SanityCheckEvent + InvestigatorSanity
输出: SanityResolutionResult

注意: 本节点不包含 LLM 调用
"""
