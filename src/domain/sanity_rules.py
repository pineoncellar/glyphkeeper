"""
理智规则内核

职责:
  - 理智损失计算（基于 SAN 值和损失表）
  - 临时疯狂判定与效果生成
  -  indefinite insanity 判定
  - 疯狂症状表（可扩展）

函数:
  - calculate_sanity_loss(max_san_loss, current_san, is_mythos) -> int
  - check_temporary_insanity(current_san, max_san) -> bool
  - check_indefinite_insanity(sanity_loss, current_san) -> bool
  - roll_insanity_symptom() -> str
  - get_sanity_loss_bounds(source_type: str) -> tuple[int, int]

原则: 100% 确定性，无 LLM 调用
"""
