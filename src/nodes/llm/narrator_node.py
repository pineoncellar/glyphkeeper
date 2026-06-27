"""
@File     :   narrator_node.py
@Desc     :   叙事生成节点 — 将裁决结果转换为沉浸式克苏鲁风格叙事文本
@Note     :   使用 standard 级别 LLM；无 LLM 时使用模板兜底

Node 签名:
    async def narrate_node(state: GameState) -> dict:
        读取 intent + resolution → 调用 LLM 或模板 → 返回 narrative
"""

from __future__ import annotations

from typing import Optional
from src.state.game_state import GameState
from src.tools import get_logger, get_settings

logger = get_logger(__name__)


# ====================================================================
# Prompt 模板
# ====================================================================

NARRATOR_SYSTEM_PROMPT = """你是克苏鲁的呼唤 TRPG 的守密人 (Keeper) — 沉浸式叙事者。

请基于玩家的意图、世界知识上下文和规则裁决结果，生成一段克苏鲁风格的叙事文本。

要求:
1. 使用中文，第一人称或第二人称叙述
2. 营造克苏鲁特有的氛围：压抑、神秘、不可名状
3. 准确反映检定结果（成功/失败/大成功/大失败）
4. 利用【世界知识上下文】中的场景信息丰富描述，但不要直接复述
5. 加入感官细节（视觉、听觉、嗅觉、触觉）
6. 保持简洁，不要过度描述（2-4 句话）
7. 严格基于裁决结果和世界知识，不要编造未给定的信息

输出纯文本，不要包含角色名或引号外的格式标记。"""

NPC_NARRATOR_SYSTEM_PROMPT = """你是克苏鲁的呼唤 TRPG 的守密人 (Keeper) — 沉浸式叙事者。

当前玩家正在与一位 NPC 对话。NPC 的发言原文已在下方以 <NPC_DIALOGUE> 标记给出。

要求:
1. 使用中文，第二人称叙述
2. 营造克苏鲁特有的氛围：压抑、神秘、不可名状
3. 严格基于世界知识和裁决结果，不要编造未给定的信息
4. 保持简洁（2-4 句话）
5. 在 NPC 发言前加入简短的情景描述和 NPC 的神态动作描写，使对话自然融入场景

【★★★★★ 最高优先级规则 ★★★★★】
NPC_DIALOGUE 中【引号内的部分】必须逐字保留，绝对不得改写、删减、替换或重述。
以下行为绝对禁止：
  - 改写 NPC 的措辞、语气或句式
  - 删减 NPC 说的任何词语
  - 用同义替换代替 NPC 的原话
  - 概括或总结 NPC 说了什么
  - 只复述 NPC 发言的"意思"而不用原话

你可以做的：
  - 将 NPC 发言中的【括号动作描述】如（擦了擦额头的汗）转化为叙事描写融入场景
  - 在引号前加入守密人口吻的动作神态描写
  - 场景信息不足时直接输出 NPC 发言原文，不做额外修饰

输出格式：你的情景描述 + NPC 完整原话（引号括起）。"""


# ====================================================================
# 模板兜底 — 无 LLM 时使用
# ====================================================================

_SUCCESS_TEMPLATES: dict[str, str] = {
    "大成功": "奇迹发生了！{action}，{detail}简直超乎想象！",
    "极难成功": "你出色地完成了{action}。{detail}",
    "困难成功": "你成功地{action}了。{detail}",
    "常规成功": "你{action}了。{detail}",
}

_FAILURE_TEMPLATES: dict[str, str] = {
    "大失败": "糟糕！你{action}时发生了可怕的事情！{detail}",
    "失败": "你试图{action}，但是没有成功。{detail}",
}

_COMBAT_HIT_TEMPLATES = [
    "你猛地击中{target}的{location}！造成了{damage}点伤害。",
    "你的攻击狠狠地打在{target}的{location}上，{target}发出一声痛苦的嘶吼。",
    "{target}未能躲开你的攻击，{location}被击中，损失了{damage}点生命值。",
]

_COMBAT_MISS_TEMPLATES = [
    "你的攻击落空了。{target}灵巧地闪避了你的进攻。",
    "你没能击中{target}。",
]


def _template_narrative(state: GameState) -> str:
    """基于模板的叙事生成（兜底方案）"""
    intent = state.get("intent") or {}
    resolution = state.get("resolution") or {}
    world_context = state.get("world_context", "")
    game_phase = state.get("game_phase", "exploration")
    player_input = state.get("player_input", "")

    # 战斗叙事
    if game_phase == "combat" or intent.get("type") == "COMBAT_ACTION":
        return _template_combat_narrative(resolution)

    # 导航叙事（MOVE 意图的 resolution 由 navigation_node 产生）
    if resolution.get("action") == "move":
        return _template_navigation_narrative(state, resolution, world_context, player_input)

    # 基于检定结果
    success_label = resolution.get("success_label", "")
    action = intent.get("data", {}).get("action", player_input[:20])
    detail = resolution.get("detail") or resolution.get("description", "")

    # 如果有世界上下文则附加场景描述
    if world_context:
        # 取上下文的前一句作为场景点缀
        scene_hint = world_context.strip().split("。" )[0] if world_context.strip() else ""
        if scene_hint and detail:
            detail = f"{detail} 你注意到{scene_hint}。"
        elif scene_hint:
            detail = f"你注意到{scene_hint}。"

    if resolution.get("is_success"):
        template = _SUCCESS_TEMPLATES.get(success_label, "你{action}了。{detail}")
        return template.format(action=action, detail=detail)

    elif resolution.get("is_failure") or resolution.get("success_level") in ("FAILURE", "FUMBLE"):
        template = _FAILURE_TEMPLATES.get(success_label, "你{action}失败了。{detail}")
        return template.format(action=action, detail=detail)

    # 无检定 — 使用世界上下文或默认回复
    if not resolution:
        if world_context:
            scene_hint = world_context.strip().split("。" )[0]
            return f"你{action}。{scene_hint}。"
        return f"你{action}。空气中弥漫着陈旧的灰尘味。"

    narrative = f"你{action}。{detail}"

    # 如果 archivist 有线索 flavor_text，追加到模板叙事末尾
    archivist_result = state.get("archivist_result")
    if archivist_result:
        flavor_text = archivist_result.get("flavor_text", "")
        if flavor_text:
            narrative += f"\n{flavor_text}"

    return narrative


def _template_combat_narrative(resolution: dict) -> str:
    """战斗叙事模板"""
    import random

    actor = resolution.get("actor_name", "你")
    target = resolution.get("target_name", "敌人")
    hit = resolution.get("hit", False)
    location = resolution.get("hit_location", "身体")
    damage = resolution.get("net_damage", 0)

    if hit:
        template = random.choice(_COMBAT_HIT_TEMPLATES)
        return template.format(actor=actor, target=target, location=location, damage=damage)
    else:
        template = random.choice(_COMBAT_MISS_TEMPLATES)
        return template.format(actor=actor, target=target)


def _template_navigation_narrative(state: GameState, resolution: dict, world_context: str, player_input: str) -> str:
    """导航叙事模板 — 渲染移动成功/失败的描述"""
    if resolution.get("success"):
        target_label = resolution.get("target_label", "")
        path = resolution.get("path")

        if path and len(path) > 1:
            dirs = [p["direction"] for p in path]
            dir_desc = "、".join(dirs)
            return f"你沿着路线前行——{dir_desc}。穿过几段路途后，你抵达了目的地。"
        elif path and len(path) == 1:
            return f"你向着{target_label}走去。周围的环境逐渐变化——你来到了新的地方。"
        return f"你向着{target_label}走去。周围的环境逐渐变化——你来到了新的地方。"
    else:
        return resolution.get("error", "你无法前往那里。")


def _validate_npc_dialogue(narrative: str, original_dialogue: str) -> str:
    """校验 LLM 叙事中 NPC 对话是否被篡改，若是则替换回原文

    提取 narrative 中所有双引号内容，逐一与 original_dialogue 比对。
    不匹配的引号段替换为 original_dialogue。
    无引号内容或校验异常时保持原样。
    """
    if not original_dialogue:
        return narrative

    import re
    # 提取所有双引号内的内容
    quoted = re.findall(r'"(.*?)"', narrative)
    if not quoted:
        return narrative  # 无引号内容，可能是纯场景描写，保持原样

    # 检查是否有任何引号内容与原文匹配
    for seg in quoted:
        if seg == original_dialogue:
            return narrative  # 至少有一段正确 → 通过

    # 全都不匹配 → 替换最后一段引号内容为原始对话
    # 用原始对话替换最后一个引号段，保留叙事框架
    last_quote = quoted[-1]
    narrative = narrative.replace(f'"{last_quote}"', f'"{original_dialogue}"', 1)
    logger.debug(f"_validate_npc_dialogue: 替换了偏移的 NPC 对话")
    return narrative


from src.tools.llm_client import call_llm as _call_llm, LLMResult


async def _call_llm_for_narrative(
    intent: dict,
    resolution: dict,
    game_phase: str,
    narrative_history: str,
    physical_reality: str = "",
    rag_context: str = "",
    known_knowledge: list[str] | None = None,
    npc_dialogue: str = "",
    clue_discovery: str = "",
) -> LLMResult:
    """调用 LLM 生成叙事文本

    physical_reality: 来自 DB Lookup Node 的 <physical_reality> XML
    rag_context:      来自 RAG Lookup Node 的 <semantic_knowledge> 或空
    npc_dialogue:     来自 NPC Dialogue Node 的 NPC 发言原文（若有）
    clue_discovery:   来自 Archivist 的技能检定线索原文（若有）
    """
    try:
        # 构建防剧透约束
        spoiler_constraint = ""
        if known_knowledge:
            spoiler_constraint = (
                "\n[重要约束] 玩家当前已发现的线索如下：\n"
                + "\n".join(f"- {k}" for k in known_knowledge)
                + "\n未在上述列表中的信息，玩家角色目前不知道，不应在叙事中作为已知事实提及。"
            )

        # 选择 prompt：NPC 对话场景 vs 普通叙事
        is_npc_scene = bool(npc_dialogue)
        base_prompt = NPC_NARRATOR_SYSTEM_PROMPT if is_npc_scene else NARRATOR_SYSTEM_PROMPT
        system_prompt = base_prompt + spoiler_constraint

        # 组装上下文
        context_parts = [f"游戏阶段: {game_phase}"]
        context_parts.append(f"玩家意图: {intent.get('data', {}).get('action', '未知')}")
        context_parts.append(f"裁决结果: {resolution}")

        if is_npc_scene:
            context_parts.append(f"\n<NPC_DIALOGUE>\n{npc_dialogue}\n</NPC_DIALOGUE>")
        if physical_reality:
            context_parts.append(f"\n<physical_reality>\n{physical_reality}\n</physical_reality>")
        if rag_context:
            context_parts.append(f"\n{rag_context}")
        if clue_discovery:
            context_parts.append(f"\n<clue_discovery>\n{clue_discovery}\n</clue_discovery>")

        context_parts.append(f"叙事历史: {narrative_history[-300:] if narrative_history else '无'}")
        context = "\n".join(context_parts)

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": context},
        ]
        return await _call_llm("standard", messages)

    except Exception as e:
        logger.warning(f"narrator_node: LLM 调用失败: {e}")
        return LLMResult(text=None, tier="standard", model_name="",
                         messages=[], success=False, error=str(e))


async def narrate_node(state: GameState) -> dict:
    """
    叙事生成节点。

    读取 intent + resolution + world_context → 调用 LLM → 失败用模板兜底 → 返回 narrative。
    同时返回 _llm_trace 供 LangSmith 追踪。

    physical_reality 由 db_lookup_node 从 PG 读模型表注入（物理事实），
    rag_context 由 rag_lookup_node 从 LightRAG 按需注入（语义背景）。

    防剧透机制：
      在调用 LLM 前查询 session_knowledge_state 获取玩家已发现的知识列表，
      注入到 system prompt 中约束 LLM 不提及未发现的信息。
    """
    intent = state.get("intent") or {}
    resolution = state.get("resolution") or {}
    physical_reality = state.get("physical_reality", "")
    rag_context = state.get("rag_context", "")
    game_phase = state.get("game_phase", "exploration")
    narrative_history = state.get("narrative", "")
    session_id = state.get("session_id", "")

    # ── 检测 NPC 对话场景 ──
    npc_dialogue = state.get("npc_dialogue", "")
    is_npc_scene = bool(npc_dialogue)

    # ── 读取技能检定发现的线索（由 skill_node 经 archivist 写入） ──
    archivist_result = state.get("archivist_result")
    clue_discovery = ""
    if archivist_result:
        flavor_text = archivist_result.get("flavor_text", "")
        if flavor_text:
            clue_discovery = flavor_text

    # ── 获取玩家已发现的知识（防剧透） ──
    known_knowledge: list[str] = []
    if session_id:
        try:
            from src.state.session_state import SessionKnowledgeState
            sks = SessionKnowledgeState()
            known_knowledge = await sks.get_discovered_knowledge_ids(session_id)
        except Exception as e:
            logger.debug(f"narrator_node: 无法获取已发现知识: {e}")

    # ── 尝试 LLM ──
    result = await _call_llm_for_narrative(
        intent, resolution, game_phase, narrative_history,
        physical_reality=physical_reality,
        rag_context=rag_context,
        known_knowledge=known_knowledge,
        npc_dialogue=npc_dialogue,
        clue_discovery=clue_discovery,
    )

    if result.is_ok:
        narrative = result.text
        # ── NPC 对话校验：提取引号内容比对原始 NPC_DIALOGUE ──
        if is_npc_scene and npc_dialogue:
            narrative = _validate_npc_dialogue(narrative, npc_dialogue)
        logger.info(f"narrator_node[LLM]: {len(narrative)} chars")
    else:
        # ── 模板兜底 ──
        if is_npc_scene:
            # NPC 对话兜底：直接使用 NPC 发言，前面加简单场景描述
            npc_name = resolution.get("npc_name", "对方")
            narrative = f"{npc_name}说道：\"{npc_dialogue}\""
        else:
            narrative = _template_narrative(state)
        logger.info(f"narrator_node[TEMPLATE]: {len(narrative)} chars")

    return {"narrative": narrative, "_llm_trace": result.to_trace() if result.is_ok else None}


# ====================================================================
# 快捷叙事辅助函数
# ====================================================================

def build_success_narrative(
    action: str,
    skill_name: str,
    success_label: str,
    roll_value: int,
    target: str = "",
) -> str:
    """构建技能检定成功叙事"""
    texts = {
        "大成功": f"骰子叮当落下——{roll_value}！命运的眷顾！你{action}的过程简直不可思议，",
        "极难成功": f"你深吸一口气，{action}。骰出{roll_value}，完美达成！",
        "困难成功": f"你专注地{action}。骰出{roll_value}，做得不错。",
        "常规成功": f"你{action}。骰出{roll_value}，勉强成功了。",
    }
    base = texts.get(success_label, f"你{action}成功。")
    if target:
        base += f"目标: {target}。"
    return base


def build_failure_narrative(
    action: str,
    skill_name: str,
    success_label: str,
    roll_value: int,
    target: str = "",
) -> str:
    """构建技能检定失败叙事"""
    texts = {
        "大失败": f"不！骰出{roll_value}——灾难降临！你{action}时发生了可怕的事故！",
        "失败": f"骰出{roll_value}。你试图{action}，但是没能成功。",
    }
    base = texts.get(success_label, f"你{action}失败了。")
    if target:
        base += f"关于{target}，你一无所获。"
    return base
