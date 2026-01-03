"""
RuleKeeper Agent - 规则裁决者
负责查询和解释游戏规则 (CoC 7th Edition)
"""
import json
from typing import Dict, Any, Optional
from ..core import get_logger
from .tools.knowledge_service import KnowledgeService
from ..llm import LLMFactory

logger = get_logger(__name__)

class RuleKeeper:
    """规则裁决者 Agent"""
    def __init__(self):
        self.domain = "rules"
        self.knowledge_service = KnowledgeService(domain=self.domain)
        self.llm = LLMFactory.get_llm("standard")
        
    async def initialize(self):
        """初始化 RuleKeeper 的知识服务"""
        await self.knowledge_service.initialize()
        logger.info("RuleKeeper initialized with KnowledgeService")

    async def consult_rulebook(self, query: str, context_summary: str = "") -> str:
        """查询规则书并根据上下文提供裁决建议"""
        if not self.knowledge_service.rag_engine:
            await self.initialize()
            
        # 使用 KnowledgeService 检索规则
        try:
            # 使用 rule_judge 角色的智能模式以更好地解释规则
            rules_text = await self.knowledge_service.search(
                query=query,
                mode="hybrid",
                smart_mode=True,
                persona="rule_judge",
                top_k=3
            )
        except Exception as e:
            logger.error(f"查询规则引擎失败: {e}")
            rules_text = "无法检索到相关规则，请根据通用CoC规则判断。"
        
        # 使用 LLM 进行解释
        prompt = f"""
        你是一个精通《克苏鲁的呼唤 7版》规则的裁判。
        
        【规则参考】
        {rules_text}
        
        【当前情况】
        {context_summary}
        
        【问题】
        {query}
        
        请简明扼要地给出规则判定建议。
        如果是“孤注一掷”，请明确指出失败的可怕后果。
        如果是战斗，请给出行动顺序或修正建议。
        不要废话，直接给出裁决。
        """
        
        response = await self.llm.chat(prompt)
        return response

    def get_tool_schema(self) -> Dict[str, Any]:
        """返回 Narrator 使用的工具模式"""
        return {
            "type": "function",
            "function": {
                "name": "consult_rulebook",
                "description": "当玩家提及特殊机制（如孤注一掷、战斗、追逐、魔法）或你不确定规则判定时调用。Consult the rulebook for mechanics and judgments.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {
                            "type": "string", 
                            "description": "具体的规则问题，如'孤注一掷的条件'或'手枪射击修正'"
                        },
                        "context_summary": {
                            "type": "string", 
                            "description": "当前场景的简要描述，包括玩家意图和相关状态"
                        }
                    },
                    "required": ["query"]
                }
            }
        }
