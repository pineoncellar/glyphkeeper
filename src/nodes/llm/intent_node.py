"""
@File     :   intent_node.py
@Desc     :   意图分析节点 — 将玩家自然语言输入转换为结构化 Intent
@Note     :   使用 fast 级别 LLM；无 LLM 时使用规则兜底

Node 签名:
    async def intent_node(state: GameState) -> dict:
        读取 player_input + 上下文 → 调用 LLM → 返回结构化 intent
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional
from src.state.game_state import GameState
from src.tools import get_logger, get_settings

logger = get_logger(__name__)


# ── 开关常量 ──

INTENT_NODE_TARGET_KEY_RESOLVE = True
"""LLM target→key 映射开关。

True 时 intent_node 查询当前场景可交互物列表注入 prompt，
让 LLM 输出 target_key 供 Archivist 精确匹配。
False 时保持原有行为，靠 Archivist name 降级兜底。
"""


# ====================================================================
# Prompt 模板
# ====================================================================

INTENT_SYSTEM_PROMPT = """你是 CoC (克苏鲁的呼唤) 守密人助手 — 意图分析师。
请将玩家的自然语言输入转换为结构化意图 JSON。

你的输出必须是纯 JSON 对象，不要包含 markdown 代码块或其他格式。

可选的意图类型 (type):
- PHYSICAL_INTERACT: 物理交互（搜索、使用物品、开门等）
- SOCIAL_INTERACT: 社交交互（对话、说服、恐吓等）
- COMBAT_ACTION: 战斗行动（攻击、射击、闪避等）
- MOVE: 移动（去某处、跟随、探索等）
- META: 元操作（查看状态、保存、提问规则等）

额外字段 `needs_rag`:
- true:  需要从 LightRAG 检索世界知识（阅读/回忆/深度调查/查看 lore 时）
- false: 不需要 RAG（常规移动/物理交互/简单对话等）
- 判断依据: 玩家是否在询问背景 lore、回忆过去、或研究深层次信息

输出 JSON 格式:
```json
{
    "type": "意图类型",
    "character_name": "涉及的PC名（可选）",
    "confidence": 0.0-1.0,
    "needs_rag": true/false,
    "data": {
        "action": "标准化的动作描述",
        "target": "作用对象（可选）",
        "target_key": "匹配到的系统 key（可选，仅当下方场景列表有时使用）",
        "skill_name": "可能需要的技能（可选）",
        "query": "需要检索的信息（可选）",
        "check_type": "skill / stat / opposed / none（可选）",
        "difficulty": "REGULAR / HARD / EXTREME（可选）",
        "detail": "其他补充信息（可选）"
    }
}
```

示例:
输入: "我仔细检查书桌的抽屉"
输出: {"type": "PHYSICAL_INTERACT", "character_name": "", "confidence": 0.95, "needs_rag": false, "data": {"action": "检查抽屉", "target": "书桌", "skill_name": "侦查", "check_type": "skill", "difficulty": "REGULAR", "detail": "仔细搜查书桌的所有抽屉"}}

输入: "我要用斗殴揍那个邪教徒"
输出: {"type": "COMBAT_ACTION", "character_name": "", "confidence": 0.98, "needs_rag": false, "data": {"action": "攻击", "target": "邪教徒", "skill_name": "斗殴", "check_type": "skill", "difficulty": "REGULAR", "detail": "用拳头攻击邪教徒"}}

输入: "这墙上的符文我好像在哪见过……"
输出: {"type": "PHYSICAL_INTERACT", "character_name": "", "confidence": 0.85, "needs_rag": true, "data": {"action": "研究符文", "target": "墙上符文", "skill_name": "神秘学", "query": "墙上的符文含义和背景", "check_type": "skill", "difficulty": "HARD", "detail": "玩家试图回忆符文的来历"}}

输入: "你好，你是谁"
输出: {"type": "SOCIAL_INTERACT", "character_name": "", "confidence": 0.9, "needs_rag": false, "data": {"action": "打招呼", "target": "", "skill_name": "", "check_type": "none", "difficulty": "REGULAR", "detail": "玩家打招呼"}}

如果无法识别意图，输出: {"type": "META", "character_name": "", "confidence": 0.1, "needs_rag": false, "data": {"action": "unknown", "target": "", "skill_name": "", "check_type": "none", "difficulty": "REGULAR", "detail": "无法识别的输入"}}"""


# ====================================================================
# 规则兜底 — 关键词匹配（无 LLM 时使用）
# ====================================================================

# 动作 → 意图类型的映射
_ACTION_KEYWORDS: dict[str, str] = {
    # 战斗
    "攻击": "COMBAT_ACTION", "打": "COMBAT_ACTION", "揍": "COMBAT_ACTION",
    "射击": "COMBAT_ACTION", "开枪": "COMBAT_ACTION", "砍": "COMBAT_ACTION",
    "刺": "COMBAT_ACTION", "踢": "COMBAT_ACTION", "战斗": "COMBAT_ACTION",
    "杀": "COMBAT_ACTION", "消灭": "COMBAT_ACTION",
    # 物理交互
    "搜索": "PHYSICAL_INTERACT", "检查": "PHYSICAL_INTERACT", "查看": "PHYSICAL_INTERACT",
    "打开": "PHYSICAL_INTERACT", "关门": "PHYSICAL_INTERACT", "推": "PHYSICAL_INTERACT",
    "拉": "PHYSICAL_INTERACT", "拿": "PHYSICAL_INTERACT", "拾取": "PHYSICAL_INTERACT",
    "使用": "PHYSICAL_INTERACT", "阅读": "PHYSICAL_INTERACT", "翻": "PHYSICAL_INTERACT",
    # 移动
    "去": "MOVE", "走": "MOVE", "跑": "MOVE", "前往": "MOVE", "进入": "MOVE",
    "跟随": "MOVE", "离开": "MOVE", "上楼": "MOVE", "下楼": "MOVE",
    # 社交
    "对话": "SOCIAL_INTERACT", "说": "SOCIAL_INTERACT", "问": "SOCIAL_INTERACT",
    "告诉": "SOCIAL_INTERACT", "说服": "SOCIAL_INTERACT", "恐吓": "SOCIAL_INTERACT",
    "魅惑": "SOCIAL_INTERACT", "询问": "SOCIAL_INTERACT",
    # 元
    "状态": "META", "属性": "META", "背包": "META", "技能": "META",
    "帮助": "META", "规则": "META", "保存": "META",
}

# 行动 → 技能映射
_ACTION_SKILL_MAP: dict[str, str] = {
    "搜索": "侦查", "检查": "侦查", "查看": "侦查",
    "翻阅": "图书馆利用", "阅读": "图书馆利用",
    "开门": "机械维修", "锁": "锁匠",
    "说服": "说服", "恐吓": "恐吓", "魅惑": "魅惑",
    "攀爬": "攀爬", "跳跃": "跳跃", "潜行": "潜行",
    "追踪": "追踪", "聆听": "聆听",
    "急救": "急救", "治疗": "医学",
}


def _rule_based_intent(player_input: str, game_phase: str) -> dict:
    """基于关键词的规则兜底意图识别"""
    text = player_input.strip()

    if not text:
        return {
            "type": "META",
            "character_name": "",
            "confidence": 0.0,
            "data": {
                "action": "empty",
                "target": "",
                "skill_name": "",
                "query": "",
                "check_type": "none",
                "difficulty": "REGULAR",
                "detail": "",
            },
        }

    # 检测意图类型 — META 关键词优先匹配
    intent_type = "META"
    skill_name = ""
    target = ""
    check_type = "none"
    difficulty = "REGULAR"
    action = "unknown"

    # 先检查 META 关键词
    for keyword in ["状态", "属性", "背包", "技能", "帮助", "规则", "保存"]:
        if keyword in text:
            intent_type = "META"
            action = keyword
            break
    else:
        # 再检查其他关键词
        for keyword, itype in _ACTION_KEYWORDS.items():
            if itype == "META":
                continue
            if keyword in text:
                intent_type = itype
                action = keyword
                break

    # 提取目标（动词后的内容）
    for verb in ["检查", "查看", "搜索", "打开", "攻击", "打", "去", "进入"]:
        if verb in text:
            parts = text.split(verb, 1)
            if len(parts) > 1 and parts[1].strip():
                target = parts[1].strip().rstrip("，。！？,!?")
            break

    # 战斗阶段特殊处理
    if game_phase == "combat" and intent_type != "COMBAT_ACTION":
        # 战斗阶段大多数动作都是战斗行动
        if any(kw in text for kw in ["闪避", "躲", "防御"]):
            intent_type = "COMBAT_ACTION"
            action = "dodge"
            skill_name = "闪避"
            check_type = "skill"
        else:
            intent_type = "COMBAT_ACTION"
            action = "attack"

    # 技能映射
    for act, sk in _ACTION_SKILL_MAP.items():
        if act in text:
            skill_name = sk
            check_type = "skill"
            break

    # needs_rag 判定: 需要翻找知识/回忆/研究时标记为 true
    # TODO: 优化为 NLP 语义分析，而非简单关键词匹配
    needs_rag = any(kw in text for kw in [
        "回忆", "回想", "记得", "记不", "想起",
        "研究", "调查", "查", "查阅", "翻找",
        "lore", "背景", "传说", "历史", "意义",
        "什么意思", "是什么", "符文", "符号",
        "这墙", "这地", "这房间", "这个地方",
    ])

    return {
        "type": intent_type,
        "character_name": "",
        "confidence": 0.6,
        "needs_rag": needs_rag,
        "data": {
            "action": action,
            "target": target,
            "skill_name": skill_name,
            "query": text if intent_type in ("META", "RECALL") else target,
            "check_type": check_type,
            "difficulty": difficulty,
            "detail": text,
        },
    }


def _parse_llm_response(response_text: str) -> dict | None:
    """解析 LLM 返回的 JSON 字符串"""
    if not response_text:
        return None

    text = response_text.strip()

    # 移除可能的 markdown 代码块标记
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    try:
        result = json.loads(text)
        if isinstance(result, dict) and "type" in result:
            return result
    except json.JSONDecodeError:
        pass

    # 尝试从文本中提取 JSON 对象
    try:
        start = text.index('{')
        end = text.rindex('}') + 1
        result = json.loads(text[start:end])
        if isinstance(result, dict) and "type" in result:
            return result
    except (ValueError, json.JSONDecodeError):
        pass

    return None


from src.tools.llm_client import call_llm as _call_llm, LLMResult


async def _call_llm_for_intent(
    player_input: str,
    context: str,
    scene_targets: str = "",
) -> LLMResult:
    """调用 LLM 获取意图分析结果

    player_input:  玩家原始输入
    context:       游戏阶段 + 叙事历史
    scene_targets: 当前场景物品/NPC 列表（key→name），供 LLM 做 target→key 映射
    """
    try:
        user_content = f"当前游戏阶段: {context}\n玩家输入: {player_input}"
        if scene_targets:
            user_content += f"\n\n当前场景中的可交互物品:\n{scene_targets}"
        messages = [
            {"role": "system", "content": INTENT_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ]
        return await _call_llm("fast", messages)

    except Exception as e:
        logger.warning(f"intent_node: LLM 调用失败: {e}")
        return LLMResult(text=None, tier="fast", model_name="",
                         messages=[], success=False, error=str(e))


async def intent_node(state: GameState) -> dict:
    """
    意图分析节点。

    接收玩家输入 → 尝试 LLM 分析 → 失败时规则兜底 → 返回结构化 Intent。
    同时返回 _llm_trace 供 LangSmith 追踪。

    从 state 中读取:
        player_input: str       — 玩家的原始输入文本
        game_phase: str         — 当前游戏阶段
        narrative: str          — 最近的叙事文本（提供语境）
    """
    player_input = state.get("player_input", "").strip()
    game_phase = state.get("game_phase", "exploration")
    narrative = state.get("narrative", "")

    if not player_input:
        logger.warning("intent_node: 无玩家输入")
        return {
            "intent": {
                "type": "META",
                "character_name": "",
                "confidence": 0.0,
                "data": {"action": "empty", "target": "", "detail": "空输入"},
            },
            "npc_dialogue": "",
            "_llm_trace": None,
        }

    # ── 构建场景目标列表（供 LLM 做 target→key 映射） ──
    scene_targets = ""
    if INTENT_NODE_TARGET_KEY_RESOLVE:
        current_location = state.get("current_location", "")
        if current_location:
            try:
                from src.state.read_models import StaticReadStore
                store = StaticReadStore()
                items = await store.get_interactables_by_location(current_location)
                if items:
                    lines = [f"  - {i['key']} → {i['name']}" for i in items]
                    scene_targets = "\n".join(lines)
            except Exception as e:
                logger.debug(f"intent_node: 场景查询失败（非阻塞）: {e}")

    # ── 尝试 LLM ──
    context_text = f"阶段={game_phase}, 最近叙事: {narrative[-200:]}" if narrative else f"阶段={game_phase}"
    result = await _call_llm_for_intent(player_input, context_text, scene_targets=scene_targets)

    if result.is_ok:
        parsed = _parse_llm_response(result.text)
        if parsed:
            intent = parsed
            logger.info(
                f"intent_node[LLM]: type={intent['type']} "
                f"action={intent.get('data', {}).get('action', '')} "
                f"conf={intent.get('confidence', 0)}"
            )
            return {"intent": intent, "npc_dialogue": "", "_llm_trace": result.to_trace()}

    # ── 规则兜底 ──
    intent = _rule_based_intent(player_input, game_phase)
    logger.info(
        f"intent_node[RULE]: type={intent['type']} "
        f"action={intent['data']['action']}"
    )

    return {"intent": intent, "npc_dialogue": "", "_llm_trace": result.to_trace() if not result.is_ok else None}


async def rule_only_intent_node(state: GameState) -> dict:
    """
    纯规则意图分析节点 — 不尝试调用 LLM。

    用于测试环境或禁用 LLM 的场景。
    """
    player_input = state.get("player_input", "").strip()
    game_phase = state.get("game_phase", "exploration")

    intent = _rule_based_intent(player_input, game_phase)
    return {"intent": intent}
