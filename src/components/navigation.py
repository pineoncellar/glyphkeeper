from .base import BaseComponent
from ..core.events import ResolutionResult

class NavigationComponent(BaseComponent):
    def initialize(self):
        pass

    def move(self, destination: str) -> ResolutionResult:
        return ResolutionResult(
            success=True,
            outcome_desc=f"移动到了 {destination}。(导航组件占位符)"
        )
