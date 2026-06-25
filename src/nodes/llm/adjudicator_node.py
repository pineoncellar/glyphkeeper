"""
@File     :   adjudicator_node.py
@Desc     :   即兴裁决节点 — 将玩家的创意行动转化为规则参数
@Note     :   使用 standard/smart 级别 LLM；无 LLM 时使用规则兜底

Node 签名:
    async def adjudicate_node(state: GameState) -> dict:
        读取 intent → 判断是否可路由到规则节点 → 否，调用 LLM 即兴裁决
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from src.state.game_state import GameState
from src.tools import get_logger, get_settings

logger = get_logger(__name__)


# ====================================================================
# Prompt 模板
# ====================================================================

ADJUDICATOR_SYSTEM_PROMPT = """你是克苏鲁的呼唤 TRPG 的规则裁定者 (Adjudicator)。

玩家发起了需要规则裁决的行动。请根据以下描述，将玩家的创意行动转译为规则参数。

输出格式（必须是纯 JSON，不要包含 markdown 代码块）:
{
    "action": "标准化后的行动描述",
    "skill": "适用的技能名称（如不确定填空字符串）",
    "difficulty": "建议的难度等级 REGULAR / HARD / EXTREME",
    "bonus_dice": 0,
    "penalty_dice": 0,
    "description": "情境描述和裁定理由",
    "needs_check": true,
    "check_type": "skill / stat / opposed / none"
}

裁定原则:
1. 简单的日常动作 → needs_check=false, check_type="none"
2. 有挑战但合理的动作 → needs_check=true, difficulty="REGULAR"
3. 非常困难的动作 → difficulty="HARD"
4. 几乎不可能的动作 → difficulty="EXTREME"
5. 有利情境 → bonus_dice=1~2
6. 不利情境 → penalty_dice=1~2
7. 对抗 → check_type="opposed"
8. 属性相关 → check_type="stat"（并指明 stat 名称: STR/CON/DEX/INT/POW/APP/EDU/SIZ）"""


# ====================================================================
# 规则兜底
# ====================================================================

# 不需要检定的简单动作
_NO_CHECK_ACTIONS = {"打招呼", "说", "问", "回答", "坐下", "站起", "等待", "观察"}

# 行动 → 检定映射
_ADJUDICATION_RULES: dict[str, dict] = {
    "攀爬": {"skill": "攀爬", "check_type": "skill", "difficulty": "REGULAR"},
    "爬": {"skill": "攀爬", "check_type": "skill", "difficulty": "REGULAR"},
    "跳跃": {"skill": "跳跃", "check_type": "skill", "difficulty": "REGULAR"},
    "跳": {"skill": "跳跃", "check_type": "skill", "difficulty": "REGULAR"},
    "潜行": {"skill": "潜行", "check_type": "skill", "difficulty": "REGULAR"},
    "躲藏": {"skill": "潜行", "check_type": "skill", "difficulty": "REGULAR"},
    "跟踪": {"skill": "追踪", "check_type": "skill", "difficulty": "REGULAR"},
    "聆听": {"skill": "聆听", "check_type": "skill", "difficulty": "REGULAR"},
    "偷听": {"skill": "聆听", "check_type": "skill", "difficulty": "HARD"},
    "急救": {"skill": "急救", "check_type": "skill", "difficulty": "REGULAR"},
    "说服": {"skill": "说服", "check_type": "skill", "difficulty": "REGULAR"},
    "恐吓": {"skill": "恐吓", "check_type": "skill", "difficulty": "REGULAR"},
    "魅惑": {"skill": "魅惑", "check_type": "skill", "difficulty": "REGULAR"},
    "开锁": {"skill": "锁匠", "check_type": "skill", "difficulty": "HARD"},
    "撬锁": {"skill": "锁匠", "check_type": "skill", "difficulty": "HARD"},
    "搜索": {"skill": "侦查", "check_type": "skill", "difficulty": "REGULAR"},
    "力大掀翻": {"check_type": "stat", "stat": "STR", "difficulty": "HARD"},
    "举": {"check_type": "stat", "stat": "STR", "difficulty": "REGULAR"},
    "推": {"check_type": "stat", "stat": "STR", "difficulty": "REGULAR"},
    "拉": {"check_type": "stat", "stat": "STR", "difficulty": "REGULAR"},
    "回忆": {"check_type": "stat", "stat": "INT", "difficulty": "REGULAR"},
    "记住": {"check_type": "stat", "stat": "INT", "difficulty": "REGULAR"},
}


def _rule_based_adjudication(intent_data: dict) -> dict:
    """基于规则的即兴裁决"""
    action = intent_data.get("action", "").lower() or intent_data.get("detail", "").lower()
    target = intent_data.get("target", "")
    detail = (intent_data.get("detail") or "").lower()

    # 不需要检定的动作
    if action in _NO_CHECK_ACTIONS:
        return {
            "success": True,
            "action": action,
            "skill": "",
            "difficulty": "REGULAR",
            "bonus_dice": 0,
            "penalty_dice": 0,
            "description": f"简单的{action}动作，无需检定。",
            "needs_check": False,
            "check_type": "none",
        }

    # 查找预定义规则
    for keyword, rule in _ADJUDICATION_RULES.items():
        if keyword in action or keyword in detail:
            return {
                "success": True,
                "action": action,
                "skill": rule.get("skill", ""),
                "stat": rule.get("stat", ""),
                "difficulty": rule.get("difficulty", "REGULAR"),
                "bonus_dice": 1 if "有利" in target else 0,
                "penalty_dice": 1 if "不利" in target else 0,
                "description": f"玩家{action}{target}，"
                f"建议{'属性检定' if rule.get('check_type') == 'stat' else '技能检定'}。",
                "needs_check": True,
                "check_type": rule.get("check_type", "skill"),
            }

    # 默认：需要常规技能检定
    return {
        "success": True,
        "action": action,
        "skill": "",
        "difficulty": "REGULAR",
        "bonus_dice": 0,
        "penalty_dice": 0,
        "description": f"玩家{action}{target}，建议进行技能检定。",
        "needs_check": True,
        "check_type": "skill",
    }


def _parse_llm_response(response_text: str) -> dict | None:
    """解析 LLM 返回的 JSON"""
    if not response_text:
        return None
    text = response_text.strip()
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass
    try:
        start = text.index('{')
        end = text.rindex('}') + 1
        result = json.loads(text[start:end])
        if isinstance(result, dict):
            return result
    except (ValueError, json.JSONDecodeError):
        pass
    return None


async def _call_llm_for_adjudication(action_desc: str, context: str) -> str | None:
    """调用 LLM 获取裁决参数"""
    try:
        import sys, os
        old_src_path = None
        for p in [
            os.path.join(os.path.dirname(__file__), "../../../backup_old_structure/old_src"),
            os.path.join(os.path.dirname(__file__), "../../backup_old_structure/old_src"),
        ]:
            ap = os.path.abspath(p)
            if os.path.isdir(ap):
                old_src_path = ap
                break

        if old_src_path and old_src_path not in sys.path:
            sys.path.insert(0, old_src_path)

        from llm import LLMFactory
        llm = LLMFactory.get_llm("standard")

        messages = [
            {"role": "system", "content": ADJUDICATOR_SYSTEM_PROMPT},
            {"role": "user", "content": f"玩家行动: {action_desc}\n情境: {context}"},
        ]

        full_response = ""
        async for chunk in llm.chat(messages):
            if isinstance(chunk, str):
                full_response += chunk

        return full_response

    except ImportError:
        logger.debug("adjudicator_node: LLMFactory 不可用，使用规则兜底")
        return None
    except Exception as e:
        logger.warning(f"adjudicator_node: LLM 调用失败: {e}")
        return None


async def adjudicate_node(state: GameState) -> dict:
    """
    即兴裁决节点。

    处理无硬编码规则对应的玩家即兴行为。
    将玩家的创意行动转化为规则参数（难度等级、技能、效果）。
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    action_desc = intent_data.get("detail") or intent_data.get("action", "")
    context = (
        f"游戏阶段: {state.get('game_phase', 'exploration')}, "
        f"玩家输入: {state.get('player_input', '')[:100]}"
    )

    if not action_desc:
        logger.warning("adjudicator_node: 无行动描述")
        return {
            "resolution": {
                "success": False,
                "error": "无行动描述",
                "action": "",
                "needs_check": False,
                "check_type": "none",
            },
        }

    # ── 尝试 LLM ──
    llm_response = await _call_llm_for_adjudication(action_desc, context)

    if llm_response:
        parsed = _parse_llm_response(llm_response)
        if parsed:
            result = parsed
            logger.info(
                f"adjudicator_node[LLM]: action={result.get('action', '')} "
                f"skill={result.get('skill', '')} "
                f"difficulty={result.get('difficulty', '')}"
            )
            return {"resolution": result}

    # ── 规则兜底 ──
    result = _rule_based_adjudication(intent_data)
    logger.info(
        f"adjudicator_node[RULE]: action={result['action']} "
        f"check_type={result['check_type']}"
    )

    return {"resolution": result}
