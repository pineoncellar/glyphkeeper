"""
搜索代理模块
封装 LightRAG 查询，定义不同的查询策略
"""
from typing import Optional, Literal, Dict, Any
from dataclasses import dataclass

from ..core import get_logger
from ..memory.RAG_engine import get_rag_engine

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """搜索结果"""
    answer: str
    mode: str
    question: str
    metadata: Optional[Dict[str, Any]] = None


class SearchAgent:
    """
    搜索代理类
    封装不同的搜索策略和提示词模板
    """
    
    PROMPT_TEMPLATES = {
        "chinese": "请用中文回答问题，确保回答准确、完整且易于理解。",
        "structured": "请用结构化的格式回答，如果涉及实体关系，请尝试用 Markdown 表格列出。",
        "concise": "请简洁回答，控制在 200 字以内。",
        "detailed": "请详细回答，包括相关的背景信息、具体例子和可能的延伸内容。",
        "game_narrator": (
            "你是一位专业的 TRPG 游戏叙事者。"
            "请用富有想象力和戏剧性的语言回答，"
            "营造沉浸式的游戏氛围。"
        ),
        "game_archivist": (
            "你是一位严谨的游戏资料管理员。"
            "请准确引用知识库中的信息，"
            "标注出处和相关规则页码（如有）。"
        ),
    }
    
    def __init__(self, environment: str = "development"):
        self.environment = environment
    
    async def query(
        self,
        question: str,
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        prompt_template: Optional[str] = None,
        custom_prompt: Optional[str] = None,
        top_k: int = 60
    ) -> SearchResult:
        """
        执行搜索查询
        
        Args:
            question: 用户问题
            mode: 查询模式
                - local: 局部搜索，侧重实体关系，适合具体问题
                - global: 全局搜索，侧重主题概念，适合概览性问题
                - hybrid: 混合模式，平衡 local 和 global (推荐)
                - mix: 组合多种结果，返回更全面的答案
                - naive: 朴素搜索，简单快速
            prompt_template: 预定义的提示词模板名称
            custom_prompt: 自定义提示词 (优先级高于模板)
            top_k: 返回的相关文档数量
            
        Returns:
            SearchResult 对象
        """
        # 确定使用的提示词
        user_prompt = None
        if custom_prompt:
            user_prompt = custom_prompt
        elif prompt_template and prompt_template in self.PROMPT_TEMPLATES:
            user_prompt = self.PROMPT_TEMPLATES[prompt_template]
        
        logger.debug(f"执行搜索: question={question[:50]}..., mode={mode}")
        
        try:
            engine = await get_rag_engine()
            answer = await engine.query(
                question=question,
                mode=mode,
                top_k=top_k,
                user_prompt=user_prompt
            )
            
            return SearchResult(
                answer=answer,
                mode=mode,
                question=question,
                metadata={"top_k": top_k, "prompt_template": prompt_template}
            )
            
        except Exception as e:
            logger.error(f"搜索失败: {e}")
            raise
    
    async def smart_query(
        self,
        question: str,
        top_k: int = 60
    ) -> SearchResult:
        """
        智能查询 - 自动选择最佳模式
        
        根据问题类型自动选择查询模式:
        - 包含 "什么是", "定义", "介绍" 等 -> global
        - 包含 "如何", "步骤", "方法" 等 -> local
        - 其他 -> hybrid
        
        Args:
            question: 用户问题
            top_k: 返回的相关文档数量
            
        Returns:
            SearchResult 对象
        """
        # 简单的关键词匹配来决定模式
        question_lower = question.lower()
        
        global_keywords = ["什么是", "定义", "介绍", "概述", "总结", "背景"]
        local_keywords = ["如何", "怎么", "步骤", "方法", "具体", "详细", "关系"]
        
        mode = "hybrid"  # 默认
        
        for kw in global_keywords:
            if kw in question_lower:
                mode = "global"
                break
        
        for kw in local_keywords:
            if kw in question_lower:
                mode = "local"
                break
        
        logger.debug(f"智能选择查询模式: {mode}")
        
        return await self.query(question, mode=mode, top_k=top_k)
    
    async def multi_mode_query(
        self,
        question: str,
        modes: list[str] = ["local", "global"],
        top_k: int = 60
    ) -> Dict[str, SearchResult]:
        """
        多模式查询 - 同时使用多种模式查询并返回所有结果
        
        Args:
            question: 用户问题
            modes: 要使用的模式列表
            top_k: 返回的相关文档数量
            
        Returns:
            {模式名: SearchResult} 字典
        """
        results = {}
        
        for mode in modes:
            try:
                result = await self.query(question, mode=mode, top_k=top_k)
                results[mode] = result
            except Exception as e:
                logger.error(f"模式 {mode} 查询失败: {e}")
        
        return results


# ============================================
# 便捷函数
# ============================================

async def search(
    question: str,
    mode: str = "hybrid",
    environment: str = "development"
) -> str:
    """
    快速搜索的便捷函数
    
    Args:
        question: 用户问题
        mode: 查询模式
        environment: 运行环境
        
    Returns:
        查询答案
    """
    agent = SearchAgent(environment=environment)
    result = await agent.query(question, mode=mode)
    return result.answer


async def game_search(
    question: str,
    role: Literal["narrator", "archivist"] = "narrator",
    mode: str = "hybrid"
) -> str:
    """
    游戏场景专用搜索
    
    Args:
        question: 用户问题
        role: 游戏角色 (narrator=叙事者, archivist=资料管理员)
        mode: 查询模式
        
    Returns:
        查询答案
    """
    agent = SearchAgent()
    prompt_template = f"game_{role}"
    result = await agent.query(question, mode=mode, prompt_template=prompt_template)
    return result.answer
