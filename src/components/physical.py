from .base import BaseComponent
from ..core.events import ResolutionResult

class PhysicalComponent(BaseComponent):
    def initialize(self):
        pass

    def handle_interaction(self, target: str, action: str, params: dict) -> ResolutionResult:
        # 占位符逻辑
        return ResolutionResult(
            success=True,
            outcome_desc=f"你对 {target} 执行了 {action}。(物理组件占位符)"
        )
