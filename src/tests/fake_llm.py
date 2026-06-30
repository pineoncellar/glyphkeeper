# -*- coding: utf-8 -*-
"""
@File     :   fake_llm.py
@Desc     :   伪造 LLM 客户端 — 测试用，返回预设响应，避免实际 API 调用
@Note     :   通过 keyword 匹配系统提示词，智能选择预设响应

使用方式:
    # 在 conftest.py 中全局启用:
    from src.tests.fake_llm import fake_call_llm, fake_call_llm_stream
    monkeypatch.setattr("src.tools.llm_client.call_llm", fake_call_llm)
    monkeypatch.setattr("src.tools.llm_client.call_llm_stream", fake_call_llm_stream)

    # 或者用 fixture:
    @pytest.fixture(autouse=True)
    def use_fake_llm(monkeypatch):
        monkeypatch.setattr("src.tools.llm_client.call_llm", fake_call_llm)
        monkeypatch.setattr("src.tools.llm_client.call_llm_stream", fake_call_llm_stream)

    # 在测试中注册/覆盖特定响应:
    from src.tests.fake_llm import register_preset, clear_presets, FakeLLMConfig
    register_preset("intent", {"type": "MOVE", "data": {"action": "去门口"}})
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import AsyncGenerator, Optional

from src.tools.llm_client import LLMResult


# ====================================================================
# 类型定义
# ====================================================================

PresetKey = str            # 匹配关键词（如 "intent", "narrative"）
PresetValue = str | dict   # 预设响应（str=直接返回，dict=序列化为 JSON）


# ====================================================================
# 预设响应注册表
# ====================================================================

_PRESETS: dict[PresetKey, PresetValue] = {}

# ── 意图分析预设（匹配 system prompt 中的关键词） ──

_INTENT_KEYWORD_MAP: dict[str, str] = {
    "意图分析": "intent",
    "intent": "intent",
    "意图分析师": "intent",
}

_NARRATIVE_KEYWORD_MAP: dict[str, str] = {
    "沉浸式叙事": "narrative",
    "叙事者": "narrative",
    "守密人": "narrative",
    "NPC_DIALOGUE": "narrative_npc",
    "非玩家角色": "narrative_npc",        # NPC 对话 system prompt 关键词
    "npc": "narrative_npc",               # NPC 对话 prompt 中含 "(NPC)"
}

# ── 裁决预设（匹配 adjudicator system prompt 中的关键词） ──

_ADJUDICATOR_KEYWORD_MAP: dict[str, str] = {
    "规则裁定者": "adjudicator",
    "adjudicator": "adjudicator",
    "即兴裁决": "adjudicator",
}


# ====================================================================
# 默认预设响应
# ====================================================================

_DEFAULT_PRESETS: dict[PresetKey, PresetValue] = {
    "intent": {
        "type": "PHYSICAL_INTERACT",
        "character_name": "",
        "confidence": 0.95,
        "needs_rag": False,
        "data": {
            "action": "检查",
            "target": "周围",
            "skill_name": "侦查",
            "query": "",
            "check_type": "skill",
            "difficulty": "REGULAR",
            "detail": "玩家进行了一个动作",
        },
    },
    "narrative": "你仔细检查了周围的环境。空气中弥漫着陈旧的灰尘味，一切都显得异常安静。",
    "narrative_npc": (
        " librarian推了推眼镜说道：\"这间图书馆已经关闭多年了，"
        "你是这些年来第一位访客。不过你要找的资料可能在东侧的阅览室。\""
    ),
    "adjudicator": json.dumps({
        "action": "攀爬",
        "skill": "攀爬",
        "difficulty": "REGULAR",
        "bonus_dice": 0,
        "penalty_dice": 0,
        "description": "玩家需要进行攀爬检定。",
        "needs_check": True,
        "check_type": "skill",
    }, ensure_ascii=False),
}

# 默认初始化
_PRESETS.update(_DEFAULT_PRESETS)


# ====================================================================
# 公开接口
# ====================================================================


def register_preset(key: PresetKey, value: PresetValue):
    """注册/覆盖预设响应

    Args:
        key:   匹配关键词（如 "intent", "narrative", 或自定义 key）
        value: 预设响应（str=直接返回文本，dict=自动 JSON 序列化）
    """
    _PRESETS[key] = value


def clear_presets():
    """清除所有预设响应并恢复默认"""
    _PRESETS.clear()
    _PRESETS.update(_DEFAULT_PRESETS)


def remove_preset(key: PresetKey):
    """移除指定预设响应"""
    _PRESETS.pop(key, None)


def get_registered_keys() -> list[str]:
    """获取所有已注册的预设响应 key"""
    return list(_PRESETS.keys())


# ====================================================================
# 关键词匹配
# ====================================================================


def _match_keyword(messages: list[dict]) -> Optional[str]:
    """根据消息内容匹配预设 key

    策略:
      1. 遍历 system prompt 中的关键词 → 匹配 _INTENT_KEYWORD_MAP / _NARRATIVE_KEYWORD_MAP
      2. 若未匹配则检查 user content 中的关键词
      3. 最后检查是否有自定义注册的 key 匹配
    """
    if not messages:
        return None

    # 收集所有消息内容
    all_content = " ".join(
        m.get("content", "") for m in messages if isinstance(m.get("content"), str)
    )
    content_lower = all_content.lower()

    # 1) 匹配意图分析 prompt
    for keyword, key in _INTENT_KEYWORD_MAP.items():
        if keyword.lower() in content_lower:
            return key

    # 2) 匹配叙事 prompt（区分 NPC 对话）
    for keyword, key in _NARRATIVE_KEYWORD_MAP.items():
        if keyword.lower() in content_lower:
            return key

    # 2.5) 匹配裁决 prompt
    for keyword, key in _ADJUDICATOR_KEYWORD_MAP.items():
        if keyword.lower() in content_lower:
            return key

    # 3) 匹配自定义注册 key
    for key in _PRESETS:
        if key not in _DEFAULT_PRESETS and key.lower() in content_lower:
            return key

    # 4) 兜底判断：空匹配
    return None


def _build_response(tier: str, messages: list[dict], matched_key: Optional[str]) -> LLMResult:
    """根据匹配的 key 构建 LLMResult"""
    if matched_key and matched_key in _PRESETS:
        value = _PRESETS[matched_key]
        if isinstance(value, dict):
            text = json.dumps(value, ensure_ascii=False)
        else:
            text = str(value)
    else:
        # 无匹配时返回默认叙事
        text = str(_DEFAULT_PRESETS.get("narrative", ""))

    return LLMResult(
        text=text,
        tier=tier,
        model_name=f"fake-llm-{tier}",
        messages=messages,
        success=True,
    )


# ====================================================================
# 伪造 call_llm（替换 src.tools.llm_client.call_llm）
# ====================================================================


async def fake_call_llm(
    tier: str,
    messages: list[dict],
    *,
    timeout: float = 60.0,
    max_retries: int = 2,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> LLMResult:
    """伪造版 call_llm — 从预设注册表返回响应，不发起真实 API 调用

    完全兼容 src.tools.llm_client.call_llm 的签名。
    """
    matched_key = _match_keyword(messages)
    return _build_response(tier, messages, matched_key)


# ====================================================================
# 伪造 call_llm_stream（替换 src.tools.llm_client.call_llm_stream）
# ====================================================================


async def fake_call_llm_stream(
    tier: str,
    messages: list[dict],
    *,
    timeout: float = 60.0,
    temperature: float | None = None,
    max_tokens: int | None = None,
) -> AsyncGenerator[str, None]:
    """伪造版 call_llm_stream — 从预设注册表返回流式响应"""
    matched_key = _match_keyword(messages)
    result = _build_response(tier, messages, matched_key)
    if result.text:
        # 一次性 yield 全部文本（简化流式模拟）
        yield result.text


# ====================================================================
# FakeLLMConfig — 带上下文的伪造 LLM（进阶用法）
# ====================================================================


@dataclass
class FakeLLMConfig:
    """伪造 LLM 配置 — 支持按测试用例定制

    使用方式:
        @pytest.fixture
        def fake_llm(monkeypatch):
            config = FakeLLMConfig()
            config.set_preset("intent", {"type": "META", ...})
            config.apply(monkeypatch)
            yield config

        def test_something(fake_llm):
            fake_llm.set_preset("narrative", "自定义叙事文本")
            ...
    """

    presets: dict[PresetKey, PresetValue] = field(default_factory=lambda: dict(_DEFAULT_PRESETS))
    _patched_modules: list[str] = field(default_factory=list)

    def set_preset(self, key: PresetKey, value: PresetValue):
        """设置预设响应"""
        self.presets[key] = value

    def remove_preset(self, key: PresetKey):
        """移除预设响应"""
        self.presets.pop(key, None)

    def get_preset(self, key: PresetKey) -> Optional[PresetValue]:
        """获取预设响应"""
        return self.presets.get(key)

    async def call_llm(
        self,
        tier: str,
        messages: list[dict],
        *,
        timeout: float = 60.0,
        max_retries: int = 2,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> LLMResult:
        """实例方法版 fake_call_llm"""
        matched_key = _match_keyword(messages)
        if matched_key and matched_key in self.presets:
            value = self.presets[matched_key]
            if isinstance(value, dict):
                text = json.dumps(value, ensure_ascii=False)
            else:
                text = str(value)
        else:
            text = str(self.presets.get("narrative", ""))
        return LLMResult(text=text, tier=tier, model_name=f"fake-llm-{tier}",
                         messages=messages, success=True)

    def apply(self, monkeypatch):
        """应用 monkeypatch 到所有已知模块"""
        monkeypatch.setattr("src.tools.llm_client.call_llm", self.call_llm)
        monkeypatch.setattr("src.tools.llm_client.call_llm_stream", fake_call_llm_stream)
        # 也 patch 掉各个 Node 中的本地引用（确保万无一失）
        monkeypatch.setattr("src.nodes.llm.intent_node._call_llm", self.call_llm)
        monkeypatch.setattr("src.nodes.llm.narrator_node._call_llm", self.call_llm)
        monkeypatch.setattr("src.nodes.llm.adjudicator_node._call_llm", self.call_llm)
        monkeypatch.setattr("src.nodes.llm.npc_dialogue_node._call_llm", self.call_llm)
