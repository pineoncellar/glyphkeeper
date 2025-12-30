"""
Token 追踪器
用于监控 API 调用成本
"""
import json
import time
from dataclasses import dataclass, field
from typing import Dict, List, Optional
from datetime import datetime
import threading
from pathlib import Path

from ..core import get_logger
from ..core import get_settings

logger = get_logger(__name__)


@dataclass
class TokenUsage:
    """单次 API 调用的 Token 使用情况"""
    timestamp: datetime
    model: str
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    cost_cny: float = 0.0
    operation: str = "unknown"  # query, insert, embedding


@dataclass
class TokenStats:
    """Token 使用统计"""
    total_prompt_tokens: int = 0
    total_completion_tokens: int = 0
    total_tokens: int = 0
    total_cost_cny: float = 0.0
    call_count: int = 0
    start_time: datetime = field(default_factory=datetime.now)


class TokenTracker:
    """
    Token 追踪器
    线程安全的单例模式实现
    """
    _instance: Optional["TokenTracker"] = None
    _lock = threading.Lock()
    
    def __init__(self):
        self._usage_history: List[TokenUsage] = []
        self._stats = TokenStats()
        self._history_lock = threading.Lock()
        self._file_lock = threading.Lock()
        self._model_prices: Dict[str, Dict[str, float]] = {}  # 从配置加载的模型价格
        self._usage_log_enabled: bool = False
        self._usage_log_path: Optional[Path] = None
        self._load_logging_config()
        self._load_model_prices()

    def _load_logging_config(self):
        """从配置加载用量日志写入配置"""
        try:
            settings = get_settings()
            self._usage_log_enabled = bool(getattr(settings.project, "model_usage_logging", True))
            rel_path = getattr(settings.project, "model_usage_log_path", "logs/llm_usage.jsonl")

            # 统一基于项目根目录解析
            self._usage_log_path = settings.get_absolute_path(rel_path)

            # 确保目录存在
            self._usage_log_path.parent.mkdir(parents=True, exist_ok=True)
        except Exception as e:
            logger.warning(f"加载模型用量日志配置失败: {e}，将不会写入用量文件")
            self._usage_log_enabled = False
            self._usage_log_path = None
    
    def _load_model_prices(self):
        """从配置文件加载模型价格"""
        try:
            settings = get_settings()
            
            # 加载各层级模型的价格
            for tier_name, model_config in settings.model_tiers.items():
                if model_config.input_cost is not None and model_config.output_cost is not None:
                    # 使用模型名称作为键
                    self._model_prices[model_config.model_name] = {
                        "input": model_config.input_cost,
                        "output": model_config.output_cost
                    }
                    logger.debug(f"加载模型价格: {model_config.model_name} = ¥{model_config.input_cost}/{model_config.output_cost} per M tokens")
            
            # 加载向量嵌入模型价格
            vector_config = settings.vector_store
            if vector_config.input_cost is not None and vector_config.output_cost is not None:
                self._model_prices[vector_config.embedding_model_name] = {
                    "input": vector_config.input_cost,
                    "output": vector_config.output_cost
                }
                logger.debug(f"加载嵌入模型价格: {vector_config.embedding_model_name} = ¥{vector_config.input_cost}/{vector_config.output_cost} per M tokens")
            
        except Exception as e:
            logger.warning(f"加载模型价格配置失败: {e}，将不会记录额度")
            # 设置默认价格为空
            self._model_prices["default"] = {"input": 0, "output": 0}
    
    @classmethod
    def get_instance(cls) -> "TokenTracker":
        """获取单例实例"""
        with cls._lock:
            if cls._instance is None:
                cls._instance = cls()
            return cls._instance
    
    def track(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int = 0,
        operation: str = "unknown"
    ) -> TokenUsage:
        """记录一次 API 调用的 Token 使用"""
        total_tokens = prompt_tokens + completion_tokens
        cost = self._calculate_cost(model, prompt_tokens, completion_tokens)
        
        usage = TokenUsage(
            timestamp=datetime.now(),
            model=model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            cost_cny=cost,
            operation=operation
        )
        
        with self._history_lock:
            self._usage_history.append(usage)
            self._stats.total_prompt_tokens += prompt_tokens
            self._stats.total_completion_tokens += completion_tokens
            self._stats.total_tokens += total_tokens
            self._stats.total_cost_cny += cost
            self._stats.call_count += 1
        
        logger.debug(
            f"Token 使用: model={model}, "
            f"tokens={total_tokens}, cost=¥{cost:.6f}"
        )

        self._append_usage_to_file(usage)
        
        return usage

    def _append_usage_to_file(self, usage: TokenUsage) -> None:
        """将单次用量记录追加写入 JSONL 文件"""
        if not self._usage_log_enabled or self._usage_log_path is None:
            return

        record = {
            "timestamp": usage.timestamp.isoformat(timespec="seconds"),
            "model": usage.model,
            "operation": usage.operation,
            "prompt_tokens": usage.prompt_tokens,
            "completion_tokens": usage.completion_tokens,
            "total_tokens": usage.total_tokens,
            "cost_cny": usage.cost_cny,
        }

        try:
            line = json.dumps(record, ensure_ascii=False)
            with self._file_lock:
                # 采用追加写入，确保多线程安全
                with open(self._usage_log_path, "a", encoding="utf-8") as f:
                    f.write(line + "\n")
        except Exception as e:
            logger.warning(f"写入模型用量日志失败: {e}")
    
    def _calculate_cost(
        self,
        model: str,
        prompt_tokens: int,
        completion_tokens: int
    ) -> float:
        """计算成本"""
        # 查找模型价格
        prices = self._model_prices.get(model)
        if prices is None:
            # 尝试模糊匹配
            for key in self._model_prices:
                if key in model.lower() or model.lower() in key:
                    prices = self._model_prices[key]
                    break
        
        if prices is None:
            # 使用默认价格
            prices = self._model_prices.get("default", {"input": 0, "output": 0})
            logger.warning(f"未找到模型 {model} 的价格配置，不计算成本")
        
        # 价格单位为 人民币/M tokens，所以除以 1,000,000
        input_cost = (prompt_tokens / 1_000_000) * prices["input"]
        output_cost = (completion_tokens / 1_000_000) * prices["output"]
        
        return input_cost + output_cost
    
    def get_stats(self) -> TokenStats:
        """获取当前统计"""
        with self._history_lock:
            return TokenStats(
                total_prompt_tokens=self._stats.total_prompt_tokens,
                total_completion_tokens=self._stats.total_completion_tokens,
                total_tokens=self._stats.total_tokens,
                total_cost_cny=self._stats.total_cost_cny,
                call_count=self._stats.call_count,
                start_time=self._stats.start_time
            )
    
    def get_history(self, limit: int = 100) -> List[TokenUsage]:
        """获取最近的使用历史"""
        with self._history_lock:
            return list(self._usage_history[-limit:])
    
    def get_stats_by_model(self) -> Dict[str, TokenStats]:
        """按模型分组的统计"""
        result: Dict[str, TokenStats] = {}
        
        with self._history_lock:
            for usage in self._usage_history:
                if usage.model not in result:
                    result[usage.model] = TokenStats(start_time=usage.timestamp)
                
                stats = result[usage.model]
                stats.total_prompt_tokens += usage.prompt_tokens
                stats.total_completion_tokens += usage.completion_tokens
                stats.total_tokens += usage.total_tokens
                stats.total_cost_cny += usage.cost_cny
                stats.call_count += 1
        
        return result
    
    def reset(self):
        """重置统计"""
        with self._history_lock:
            self._usage_history.clear()
            self._stats = TokenStats()
        
        logger.info("Token 追踪器已重置")
    
    def format_stats(self) -> str:
        """格式化统计信息"""
        stats = self.get_stats()
        duration = datetime.now() - stats.start_time
        
        return (
            f"Token 使用统计：\n"
            f"总调用次数: {stats.call_count}\n"
            f"输入 Tokens: {stats.total_prompt_tokens:,}\n"
            f"输出 Tokens: {stats.total_completion_tokens:,}\n"
            f"总 Tokens: {stats.total_tokens:,}\n"
            f"预估成本: ¥{stats.total_cost_cny:.4f}\n"
            f"统计时长: {duration}"
        )


# ============================================
# 便捷函数
# ============================================

def track_tokens(
    model: str,
    prompt_tokens: int,
    completion_tokens: int = 0,
    operation: str = "unknown"
) -> TokenUsage:
    """记录 Token 使用的便捷函数"""
    tracker = TokenTracker.get_instance()
    return tracker.track(model, prompt_tokens, completion_tokens, operation)


def get_token_stats() -> TokenStats:
    """获取 Token 统计的便捷函数"""
    tracker = TokenTracker.get_instance()
    return tracker.get_stats()


def print_token_stats():
    """打印 Token 统计"""
    tracker = TokenTracker.get_instance()
    print(tracker.format_stats())
