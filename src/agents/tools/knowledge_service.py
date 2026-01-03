"""
Knowledge Service - 通用知识检索服务
为 RuleKeeper、MemoryManager 和 Narrator 提供底层搜索能力
"""
from typing import Optional, Literal
from dataclasses import dataclass

from ...core import get_logger
from ...memory import RAGEngine

logger = get_logger(__name__)


@dataclass
class SearchResult:
    """搜索结果"""
    answer: str
    mode: str
    question: str
    domain: str


class KnowledgeService:
    """
    通用知识检索服务
    
    作为底层工具类，为不同的 Agent 提供统一的知识检索能力。
    支持多个独立的知识库（规则库、世界设定库等）。
    """
    
    PROMPT_TEMPLATES = {
        "chinese": "请用中文回答问题，确保回答准确、完整且易于理解。",
        "structured": "请用结构化的格式回答，如果涉及实体关系，请尝试用 Markdown 表格列出。",
        "concise": "请简洁回答，控制在 200 字以内。",
        "detailed": "请详细回答，包括相关的背景信息、具体例子和可能的延伸内容。",
        "rule_judge": (
            "你是一位公正的规则裁判。"
            "请引用规则原文，并给出简明的判定依据。"
        ),
        "lore_keeper": (
            "你是一位博学的历史学家。"
            "请结合世界观背景进行解答，"
            "使用富有想象力和戏剧性的语言，"
            "营造沉浸式的游戏氛围。"
        ),
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
    
    def __init__(self, domain: str = "world"):
        """
        初始化知识检索服务
        
        Args:
            domain: 知识库域名，区分不同的知识库
                - "world": 世界设定库（默认）
                - "rules": 规则库
        """
        self.domain = domain
        self.rag_engine: Optional[RAGEngine] = None
        
    async def initialize(self, llm_tier: str = "standard"):
        """初始化 RAG 引擎"""
        if self.rag_engine is None:
            self.rag_engine = await RAGEngine.get_instance(
                domain=self.domain,
                llm_tier=llm_tier
            )
            logger.info(f"KnowledgeService initialized with domain: {self.domain}")

    async def search(
        self,
        query: str,
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        smart_mode: bool = True,
        persona: str = "chinese",
        top_k: int = 60
    ) -> str:
        """
        执行知识检索
        
        Args:
            query: 查询问题
            mode: 查询模式
                - local: 局部搜索，侧重实体关系
                - global: 全局搜索，侧重主题概念
                - hybrid: 混合模式 (推荐)
                - mix: 组合多种结果
                - naive: 朴素搜索
            smart_mode: 是否启用智能模式选择（根据问题自动选择最佳 mode）
            persona: 人设模板名称，控制回答风格
            top_k: 返回的相关文档数量
            
        Returns:
            查询答案字符串
        """
        if not self.rag_engine:
            await self.initialize()
        
        # 1. 智能模式选择
        if smart_mode and mode == "hybrid":
            mode = self._smart_mode_selection(query)
        
        # 2. 获取 Prompt 模板
        user_prompt = self.PROMPT_TEMPLATES.get(persona)
        
        logger.debug(f"执行检索: domain={self.domain}, query={query[:50]}..., mode={mode}, persona={persona}")
        
        try:
            # 3. 执行 RAG 查询
            answer = await self.rag_engine.query(
                question=query,
                mode=mode,
                top_k=top_k,
                user_prompt=user_prompt
            )
            
            return answer
            
        except Exception as e:
            logger.error(f"知识检索失败: {e}")
            raise

    async def search_with_metadata(
        self,
        query: str,
        mode: Literal["local", "global", "hybrid", "mix", "naive"] = "hybrid",
        smart_mode: bool = True,
        persona: str = "chinese",
        top_k: int = 60
    ) -> SearchResult:
        """
        执行知识检索并返回带元数据的结果
        
        Args:
            同 search 方法
            
        Returns:
            SearchResult 对象，包含答案和元数据
        """
        answer = await self.search(query, mode, smart_mode, persona, top_k)
        
        return SearchResult(
            answer=answer,
            mode=mode,
            question=query,
            domain=self.domain
        )

    def _smart_mode_selection(self, query: str) -> str:
        """
        智能选择最佳查询模式
        
        根据问题类型自动选择:
        - 概念性问题 (什么是、定义、介绍) -> global
        - 操作性问题 (如何、步骤、方法) -> local
        - 其他 -> hybrid
        
        Args:
            query: 查询问题
            
        Returns:
            推荐的查询模式
        """
        query_lower = query.lower()
        
        # 全局查询关键词
        global_keywords = ["什么是", "定义", "介绍", "概述", "总结", "背景", "概念"]
        # 局部查询关键词
        local_keywords = ["如何", "怎么", "步骤", "方法", "具体", "详细", "关系", "流程"]
        
        for kw in global_keywords:
            if kw in query_lower:
                logger.debug(f"智能模式: 检测到全局查询关键词 '{kw}' -> global")
                return "global"
        
        for kw in local_keywords:
            if kw in query_lower:
                logger.debug(f"智能模式: 检测到局部查询关键词 '{kw}' -> local")
                return "local"
        
        logger.debug("智能模式: 未匹配特定模式 -> hybrid")
        return "hybrid"


# ============================================
# 便捷函数 - 用于快速创建和使用服务
# ============================================

async def search_world(query: str, mode: str = "hybrid", persona: str = "lore_keeper") -> str:
    """
    快速搜索世界设定
    
    Args:
        query: 查询问题
        mode: 查询模式
        persona: 人设模板
        
    Returns:
        查询答案
    """
    service = KnowledgeService(domain="world")
    return await service.search(query, mode=mode, persona=persona)


async def search_rules(query: str, mode: str = "hybrid", persona: str = "rule_judge") -> str:
    """
    快速搜索游戏规则
    
    Args:
        query: 查询问题
        mode: 查询模式
        persona: 人设模板
        
    Returns:
        查询答案
    """
    service = KnowledgeService(domain="rules")
    return await service.search(query, mode=mode, persona=persona)
