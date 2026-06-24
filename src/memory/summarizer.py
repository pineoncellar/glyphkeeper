"""
@File     :   summarizer.py
@Desc     :   对话摘要与记忆压缩 — 将原始对话压缩为结构化摘要

职责:
  - 将原始对话记录压缩为结构化摘要
  - 提取关键事件、实体状态变更、线索
  - 控制摘要 Token 预算（避免 Context Window 溢出）
  - 支持多级摘要（实时摘要 → 轮次摘要 → 全局摘要）

接口:
    class Summarizer:
        async def summarize(self, dialogue_records: list[dict]) -> str
        async def extract_facts(self, narrative: str) -> list[dict]
        def should_summarize(self, records: list[dict], max_tokens: int = 10000) -> bool

策略:
  - TokenCountStrategy: 达到 Token 阈值触发摘要
  - TimeBasedStrategy: 定时触发（默认 10 分钟）
  - TopicEndStrategy: 话题结束标记触发
"""

import re
from abc import ABC, abstractmethod
from typing import Optional

from src.config import get_logger

logger = get_logger(__name__)

# ── Tokenizer 初始化（降级友好） ──
try:
    from tokenizers import Tokenizer as HFTokenizer
    _tokenizer = HFTokenizer.from_pretrained("gpt2")
except Exception:
    _tokenizer = None
    logger.warning("无法加载预训练 tokenizer，使用字符计数降级")


# ====================================================================
# 1. Token 计数工具
# ====================================================================


def count_tokens(text: str) -> int:
    """计算文本的 token 数量（有 tokenizer 用精确计数，否则字符估算）"""
    if _tokenizer is None:
        return len(text)
    try:
        return len(_tokenizer.encode(text).ids)
    except Exception:
        return len(text)


# ====================================================================
# 2. 摘要策略
# ====================================================================


class ConsolidationStrategy(ABC):
    """记忆固化策略基类"""

    @abstractmethod
    def should_consolidate(self, buffer: list[dict]) -> bool:
        """判断是否应该触发固化"""
        ...


class TokenCountStrategy(ConsolidationStrategy):
    """基于 Token 数量的硬触发策略"""

    def __init__(self, max_tokens: int = 10000):
        self.max_tokens = max_tokens

    def should_consolidate(self, buffer: list[dict]) -> bool:
        if not buffer:
            return False
        combined = "\n".join(
            f"{r.get('role', '?')}: {r.get('content', '')}" for r in buffer
        )
        total = count_tokens(combined)
        logger.debug(f"Buffer token count: {total}/{self.max_tokens}")
        return total >= self.max_tokens


class TopicEndStrategy(ConsolidationStrategy):
    """基于话题结束标记的触发策略"""

    def __init__(self, end_marker: str = "<END_TOPIC>"):
        self.end_marker = end_marker

    def should_consolidate(self, buffer: list[dict]) -> bool:
        if not buffer:
            return False
        return self.end_marker in buffer[-1].get("content", "")


class TimeBasedStrategy(ConsolidationStrategy):
    """基于时间间隔的触发策略 — 距离上次固化超过指定时间则触发"""

    def __init__(self, min_interval_seconds: int = 600):
        """
        参数：
            min_interval_seconds: 最小间隔秒数（默认 10 分钟）
        """
        self.min_interval = min_interval_seconds
        self._last_consolidated_at: Optional[float] = None

    def should_consolidate(self, buffer: list[dict]) -> bool:
        import time

        if not buffer:
            return False
        if self._last_consolidated_at is None:
            return True  # 从未固化过，立即触发

        elapsed = time.time() - self._last_consolidated_at
        return elapsed >= self.min_interval

    def mark_consolidated(self):
        """标记一次固化完成（外部在固化后调用）"""
        import time
        self._last_consolidated_at = time.time()

    @property
    def time_since_last(self) -> Optional[float]:
        """距上次固化的秒数（None 表示从未固化）"""
        if self._last_consolidated_at is None:
            return None
        import time
        return time.time() - self._last_consolidated_at


# ====================================================================
# 3. 摘要器
# ====================================================================


class Summarizer:
    """
    对话摘要与记忆压缩器。

    使用方式：
        summarizer = Summarizer(llm_tier="standard")
        summary = await summarizer.summarize(dialogue_records)
    """

    def __init__(self, llm_tier: str = "standard", llm_func=None):
        """
        参数：
            llm_tier: 模型等级（fast/standard/smart）
            llm_func: 可选的异步 LLM 调用函数 async (prompt: str) -> str
        """
        self.llm_tier = llm_tier
        self._llm_func = llm_func
        self._strategies: list[ConsolidationStrategy] = [
            TokenCountStrategy(max_tokens=10000),
        ]

    def add_strategy(self, strategy: ConsolidationStrategy):
        """添加自定义固化策略"""
        self._strategies.append(strategy)

    # ── 核心方法 ──

    async def summarize(self, dialogue_records: list[dict]) -> str:
        """
        将对话记录列表压缩为一段简洁的第三人称叙事摘要。

        参数：
            dialogue_records: [
                {"role": "user", "content": "我打开门"},
                {"role": "assistant", "content": "你推开了吱呀作响的木门..."},
            ]

        返回：
            纯文本摘要（≤ 200 字）
        """
        if not dialogue_records:
            return ""

        text_to_summarize = "\n".join(
            f"{r.get('role', '?')}: {r.get('content', '')}"
            for r in dialogue_records
        )

        prompt = (
            "请用一段简洁的第三人称叙述总结以下跑团对话（不超过100字）。\n\n"
            "要求：\n"
            "1. 只描述发生了什么事实和玩家的行动\n"
            "2. 不要使用 XML 标签、markdown 格式或列表\n"
            "3. 不要分析或评论，只陈述事实\n"
            "4. 使用第三人称（\"调查员...\"、\"艾德薇诗...\"）\n\n"
            f"对话内容：\n{text_to_summarize}\n\n"
            "总结："
        )

        raw_summary = await self._call_llm(prompt)
        return self._clean_summary(raw_summary)

    async def extract_facts(self, narrative: str) -> list[dict]:
        """
        从叙事文本中提取结构化事实。

        返回：
            [
                {"type": "entity_state", "subject": "调查员", "attribute": "位置", "value": "书房"},
                {"type": "event", "description": "发现了密室"},
                {"type": "clue", "description": "地板上有血迹"},
            ]
        """
        if not narrative.strip():
            return []

        prompt = (
            "从以下跑团叙事文本中提取关键事实，以 JSON 列表格式输出。\n\n"
            "事实类型：\n"
            '  - {"type": "entity_state", "subject": "...", "attribute": "...", "value": "..."}\n'
            '  - {"type": "event", "description": "..."}\n'
            '  - {"type": "clue", "description": "..."}\n\n'
            f"文本：\n{narrative}\n\n"
            "JSON 输出："
        )

        raw = await self._call_llm(prompt)
        return self._parse_facts(raw)

    def should_summarize(self, records: list[dict], max_tokens: int = 10000) -> bool:
        """检查是否应该触发摘要固化"""
        strategy = TokenCountStrategy(max_tokens=max_tokens)
        return strategy.should_consolidate(records)

    # ── LLM 调用 ──

    async def _call_llm(self, prompt: str) -> str:
        """调用 LLM 生成文本（通过 llm_func 回调或 lazy import）"""
        if self._llm_func is not None:
            return await self._llm_func(prompt)

        # 兜底：lazy import（依赖外部 llm 模块）
        try:
            from lightrag.llm.openai import openai_complete_if_cache
            from src.config import get_settings

            settings = get_settings()
            model_config, provider_config = settings.get_full_model_config(self.llm_tier)

            result = await openai_complete_if_cache(
                model=model_config.model_name,
                prompt=prompt,
                api_key=provider_config.api_key,
                base_url=provider_config.base_url,
            )
            return result
        except ImportError:
            logger.warning("LLM 模块未就绪，摘要将返回原文")
            return prompt[:500]
        except Exception as e:
            logger.error(f"LLM 调用失败: {e}")
            return prompt[:500]

    # ── 文本清洗 ──

    @staticmethod
    def _clean_summary(text: str) -> str:
        """清理总结文本，移除格式化标记和冗余内容"""
        clean = text.strip()

        # 移除 thinking 标签
        if "<thinking>" in clean:
            end = clean.find("</thinking>")
            if end != -1:
                clean = clean[end + len("</thinking>"):].strip()

        # 提取 narrative 标签内容
        if "<narrative>" in clean and "</narrative>" in clean:
            start = clean.find("<narrative>") + len("<narrative>")
            end = clean.find("</narrative>")
            clean = clean[start:end].strip()

        # 移除其他 XML 标签
        clean = re.sub(r'<[^>]+>', '', clean)

        # 移除 Markdown 格式化
        clean = re.sub(r'\*\*([^*]+)\*\*', r'\1', clean)
        clean = re.sub(r'^#+\s+', '', clean, flags=re.MULTILINE)

        # 过滤掉列表项和元数据行
        lines = clean.split('\n')
        kept = []
        for line in lines:
            line = line.strip()
            if not line:
                continue
            if line.startswith(('- ', '* ', '•')):
                continue
            if any(marker in line for marker in ['关键线索', '玩家决策', '当前状态', '环境细节']):
                continue
            kept.append(line)

        clean = ' '.join(kept[:5])

        if len(clean) > 500:
            clean = clean[:497] + "..."

        return clean.strip()

    @staticmethod
    def _parse_facts(raw: str) -> list[dict]:
        """
        从 LLM 输出中解析 JSON 事实列表。

        策略（按优先级）：
        1. 括号计数器提取完整的顶级 [...] 数组（支持嵌套）
        2. 括号计数器提取单个顶级 {...} 对象
        3. 非贪婪正则匹配（兜底）
        4. 逐行解析独立的 JSON 行

        返回：始终是 list[dict]，解析失败返回空列表。
        """
        import json

        text = raw.strip()
        if not text:
            return []

        def _ensure_list(result: object) -> list[dict]:
            """类型守卫：确保返回 list[dict]"""
            if isinstance(result, list):
                return [item for item in result if isinstance(item, dict)]
            if isinstance(result, dict):
                return [result]
            return []

        # ── 策略 1：括号计数器提取顶级数组 ──
        if text.startswith('['):
            try:
                depth, end = 0, 0
                for i, ch in enumerate(text):
                    if ch == '[':
                        depth += 1
                    elif ch == ']':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > 0:
                    return _ensure_list(json.loads(text[:end]))
            except (json.JSONDecodeError, ValueError):
                pass

        # ── 策略 2：括号计数器提取单个顶级对象 ──
        if text.startswith('{'):
            try:
                depth, end = 0, 0
                for i, ch in enumerate(text):
                    if ch == '{':
                        depth += 1
                    elif ch == '}':
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end > 0:
                    return _ensure_list(json.loads(text[:end]))
            except (json.JSONDecodeError, ValueError):
                pass

        # ── 策略 3：非贪婪正则匹配 ──
        json_match = re.search(r'\[.*?\]', text, re.DOTALL)
        if json_match:
            try:
                return _ensure_list(json.loads(json_match.group()))
            except json.JSONDecodeError:
                pass

        # ── 策略 4：逐行解析独立的 JSON 行 ──
        facts: list[dict] = []
        for line in text.split('\n'):
            line = line.strip()
            if line.startswith('{') and line.endswith('}'):
                try:
                    parsed = json.loads(line)
                    if isinstance(parsed, dict):
                        facts.append(parsed)
                except json.JSONDecodeError:
                    pass
        return facts
