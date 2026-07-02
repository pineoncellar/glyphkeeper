"""
@File     :   intent_node.py
@Desc     :   意图分析节点 — 将玩家自然语言输入裂变为多意图队列
@Note     :   使用 fast 级别 LLM；无 LLM 时使用规则兜底
              LLM 路径采用"核心谓词提取法"拆分复合意图，
              规则兜底路径降级为单意图（保持向后兼容）。

Node 签名:
    async def intent_node(state: GameState) -> dict:
        读取 player_input + 上下文 → 调用 LLM → 返回 intent_queue
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

INTENT_MAX_QUEUE_LENGTH = 5
"""单轮最多裂变意图数，防止 LLM 输出过长队列。"""


# ====================================================================
# Prompt 模板 — 多意图裂变 + 核心谓词提取
# ====================================================================

INTENT_SYSTEM_PROMPT = """你是 CoC (克苏鲁的呼唤) 守密人助手 — 意图分析师。
请将玩家的自然语言输入裂解为 1 到 N 个独立的原子意图，并按 CoC 规则的逻辑顺序排列。

【核心谓词提取法】
- 识别每个子句的"最终功利性目的"（Core Action），而非按动词切分
- 前置的 RP 修辞（如"假装系鞋带蹲下身"）不作为独立意图，而是打包为该意图的 flavor_context
- 如果一句话只表达了一个核心动作，即使有多步修辞描述，也只输出一个原子意图

【排序规则】
- 社交意图（暗示、对话）→ 物理交互（摸枪、开门、搜索）→ 战斗行动（攻击）→ 移动（去别处）
- MOVE 意图强制排在队列末尾
- 同类型意图保持原始输入中的出现顺序

可选的意图类型 (type):
- PHYSICAL_INTERACT: 物理交互（搜索、使用物品、开门、潜行拿东西等）
- SOCIAL_INTERACT: 社交交互（对话、说服、恐吓、暗示、眼神示意等）
- COMBAT_ACTION: 战斗行动（攻击、射击、闪避、准备开火等）
- MOVE: 移动（去某处、跟随、探索等）
- META: 元操作（查看状态、保存、提问规则等）

额外字段 `needs_rag`:
- true:  需要从 LightRAG 检索世界知识（阅读/回忆/深度调查/查看 lore 时）
- false: 不需要 RAG（常规移动/物理交互/简单对话等）

输出格式（纯 JSON 数组，不要包含 markdown 代码块）:
```json
[
    {
        "type": "意图类型",
        "confidence": 0.0-1.0,
        "needs_rag": true/false,
        "core_action": "核心动作描述",
        "flavor_context": "玩家的 RP 修辞文本（没有则填空字符串）",
        "data": {
            "target": "作用对象（可选）",
            "skill_name": "可能需要的技能名（可选）",
            "check_type": "skill / stat / opposed / none（可选，默认 none）",
            "difficulty": "REGULAR / HARD / EXTREME（可选，默认 REGULAR）",
            "detail": "其他补充信息（可选）"
        }
    }
]
```

示例:
输入: "我用眼神暗示旁边的队友，同时右手悄悄摸向腰间的转轮手枪，如果邪教徒动一下我就开枪"
输出: [
    {"type": "SOCIAL_INTERACT", "confidence": 0.9, "needs_rag": false, "core_action": "暗示队友", "flavor_context": "用眼神示意", "data": {"target": "队友", "skill_name": "心理学", "check_type": "skill", "difficulty": "REGULAR", "detail": "暗示队友准备行动"}},
    {"type": "PHYSICAL_INTERACT", "confidence": 0.95, "needs_rag": false, "core_action": "拔出手枪", "flavor_context": "右手悄悄摸向腰间", "data": {"target": "转轮手枪", "skill_name": "潜行", "check_type": "skill", "difficulty": "HARD", "detail": "偷偷拔出转轮手枪不被发现"}},
    {"type": "COMBAT_ACTION", "confidence": 0.9, "needs_rag": false, "core_action": "准备射击", "flavor_context": "如果邪教徒动一下就开枪", "data": {"target": "邪教徒", "skill_name": "手枪", "check_type": "skill", "difficulty": "REGULAR", "detail": "瞄准邪教徒准备随时开火"}}
]

输入: "我假装系鞋带蹲下身，趁邪教徒转头时把纸条塞进队友手心"
输出: [
    {"type": "SOCIAL_INTERACT", "confidence": 0.95, "needs_rag": false, "core_action": "传递纸条给队友", "flavor_context": "假装系鞋带蹲下身，趁邪教徒转头时", "data": {"target": "队友", "skill_name": "潜行", "check_type": "skill", "difficulty": "HARD", "detail": "偷偷将纸条塞给队友"}}
]

输入: "我仔细检查书桌的抽屉"
输出: [
    {"type": "PHYSICAL_INTERACT", "confidence": 0.95, "needs_rag": false, "core_action": "检查抽屉", "flavor_context": "", "data": {"target": "书桌", "skill_name": "侦查", "check_type": "skill", "difficulty": "REGULAR", "detail": "仔细搜查书桌的所有抽屉"}}
]

输入: "你好，你是谁"
输出: [
    {"type": "SOCIAL_INTERACT", "confidence": 0.9, "needs_rag": false, "core_action": "打招呼", "flavor_context": "", "data": {"target": "", "skill_name": "", "check_type": "none", "difficulty": "REGULAR", "detail": "玩家打招呼"}}
]

如果无法识别意图，输出: [
    {"type": "META", "confidence": 0.1, "needs_rag": false, "core_action": "unknown", "flavor_context": "", "data": {"target": "", "skill_name": "", "check_type": "none", "difficulty": "REGULAR", "detail": "无法识别的输入"}}
]"""


# ====================================================================
# 规则兜底 — 关键词匹配（无 LLM 时使用，降级为单意图）
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


def _rule_based_intent(player_input: str, game_phase: str) -> list[dict]:
    """基于关键词的规则兜底意图识别（降级为单意图列表）"""
    text = player_input.strip()

    if not text:
        return [{
            "type": "META",
            "confidence": 0.0,
            "needs_rag": False,
            "core_action": "empty",
            "flavor_context": "",
            "data": {
                "action": "empty",
                "target": "",
                "skill_name": "",
                "query": "",
                "check_type": "none",
                "difficulty": "REGULAR",
                "detail": "",
            },
        }]

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

    needs_rag = any(kw in text for kw in [
        "回忆", "回想", "记得", "记不", "想起",
        "研究", "调查", "查", "查阅", "翻找",
        "lore", "背景", "传说", "历史", "意义",
        "什么意思", "是什么", "符文", "符号",
        "这墙", "这地", "这房间", "这个地方",
    ])

    return [{
        "type": intent_type,
        "confidence": 0.6,
        "needs_rag": needs_rag,
        "core_action": action,
        "flavor_context": "",
        "data": {
            "target": target,
            "skill_name": skill_name,
            "check_type": check_type,
            "difficulty": difficulty,
            "detail": text,
        },
    }]


def _parse_llm_response(response_text: str) -> list[dict] | None:
    """解析 LLM 返回的 JSON 字符串（支持 list 和向后兼容单 dict）"""
    if not response_text:
        return None

    text = response_text.strip()

    # 移除可能的 markdown 代码块标记
    text = re.sub(r'^```(?:json)?\s*', '', text)
    text = re.sub(r'\s*```$', '', text)

    try:
        result = json.loads(text)
        # 新格式：list[dict]
        if isinstance(result, list):
            # 过滤无效条目
            valid = [item for item in result if isinstance(item, dict) and item.get("type")]
            return valid if valid else None
        # 向后兼容：单 dict → 包装为列表
        if isinstance(result, dict) and "type" in result:
            return [result]
    except json.JSONDecodeError:
        pass

    # 尝试从文本中提取 JSON
    try:
        start = text.index('[')
        end = text.rindex(']') + 1
        result = json.loads(text[start:end])
        if isinstance(result, list):
            valid = [item for item in result if isinstance(item, dict) and item.get("type")]
            return valid if valid else None
    except (ValueError, json.JSONDecodeError):
        pass

    # 兜底：尝试单 dict 提取
    try:
        start = text.index('{')
        end = text.rindex('}') + 1
        result = json.loads(text[start:end])
        if isinstance(result, dict) and "type" in result:
            return [result]
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

    接收玩家输入 → 尝试 LLM 分析（多意图裂变）→ 失败时规则兜底（单意图）→
    返回 intent_queue + 重置 current_intent_idx。
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
            "intent_queue": [{
                "type": "META",
                "confidence": 0.0,
                "needs_rag": False,
                "core_action": "empty",
                "flavor_context": "",
                "data": {"target": "", "skill_name": "", "check_type": "none", "difficulty": "REGULAR", "detail": "空输入"},
            }],
            "current_intent_idx": 0,
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
            queue = parsed[:INTENT_MAX_QUEUE_LENGTH]
            logger.info(
                f"intent_node[LLM]: {len(queue)} intent(s), "
                f"first={queue[0].get('type', '')} "
                f"core={queue[0].get('core_action', '')}"
            )
            return {
                "intent_queue": queue,
                "current_intent_idx": 0,
                "npc_dialogue": "",
                "_llm_trace": result.to_trace(),
            }

    # ── 规则兜底（降级为单意图列表） ──
    queue = _rule_based_intent(player_input, game_phase)
    logger.info(
        f"intent_node[RULE]: type={queue[0].get('type', '')} "
        f"core={queue[0].get('core_action', '')}"
    )

    return {
        "intent_queue": queue,
        "current_intent_idx": 0,
        "npc_dialogue": "",
        "_llm_trace": result.to_trace() if not result.is_ok else None,
    }


async def rule_only_intent_node(state: GameState) -> dict:
    """
    纯规则意图分析节点 — 不尝试调用 LLM。

    用于测试环境或禁用 LLM 的场景。
    """
    player_input = state.get("player_input", "").strip()
    game_phase = state.get("game_phase", "exploration")

    queue = _rule_based_intent(player_input, game_phase)
    return {
        "intent_queue": queue,
        "current_intent_idx": 0,
    }
