"""
RuleKeeper Agent - 规则裁决者
负责查询和解释游戏规则 (CoC 7th Edition)
"""
import json
from typing import Dict, Any, Optional
from ..core import get_logger
from ..memory.RAG_engine import RAGEngine
from ..llm import LLMFactory

logger = get_logger(__name__)

class RuleKeeper:
    """规则裁决者 Agent"""
    def __init__(self):
        self.domain = "rules"
        self.rag_engine = None # Will be initialized async
        self.llm = LLMFactory.get_llm("smart") # Use smart model for reasoning
        
    async def initialize(self):
        """Initialize the RAG engine"""
        if self.rag_engine is None:
            self.rag_engine = await RAGEngine.get_instance(domain=self.domain)
            logger.info("RuleKeeper initialized with RAG domain: rules")

    async def consult_rulebook(self, query: str, context_summary: str = "") -> str:
        """
        Consult the rulebook and provide a judgment based on the context.
        """
        if not self.rag_engine:
            await self.initialize()
            
        # 1. Retrieve rules
        try:
            # Using hybrid search for rules to catch both specific keywords and general concepts
            rules_text = await self.rag_engine.query(query, mode="hybrid", top_k=3)
        except Exception as e:
            logger.error(f"Failed to query rule engine: {e}")
            rules_text = "无法检索到相关规则，请根据通用CoC规则判断。"
        
        # 2. Interpret with LLM
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
        """Return the tool schema for the Narrator to use."""
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
