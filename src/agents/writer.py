from typing import Dict, Any
from ..core.events import ResolutionResult

class Writer:
    """
    第三阶段：表达与叙事 (Expression & Narrative)
    负责将裁决结果 (ResolutionResult) 转换为叙事文本。
    """
    def __init__(self, llm_client=None):
        self.llm = llm_client

    async def write(self, result: ResolutionResult, context: str) -> str:
        # LLM 调用占位符
        if result.success:
            return f"成功！{result.outcome_desc}"
        else:
            return f"失败。{result.outcome_desc}"
