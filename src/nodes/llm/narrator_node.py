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
from src.state.game_state import GameState, format_dialogue_history, get_current_player
from src.tools import get_logger, get_settings
from src.tools.llm_client import call_llm as _call_llm, LLMResult

logger = get_logger(__name__)


# ====================================================================
# Prompt 模板
# ====================================================================

NARRATOR_SYSTEM_PROMPT = """你是克苏鲁的呼唤 TRPG 的守密人 (Keeper) — 沉浸式叙事者。

请基于玩家的意图、世界知识上下文和规则裁决结果，生成一段克苏鲁风格的叙事文本。

要求:
1. 使用中文，第二人称叙述。
2. 营造克苏鲁特有的氛围：根据场景描述，通过内敛的感官细节（如光线晦暗、气味陈旧、死寂、温度）来烘托心理压抑感，避免过于直白的灵异恐怖描写。
3. 准确反映检定结果（成功/失败/大成功/大失败）。
4. 利用【世界知识上下文】中的场景信息丰富描述，但不要直接复述。
5. 加入感官细节（视觉、听觉、嗅觉、触觉）。
6. 保持简洁，不要过度描述（2-4 句话）。

【守密人核心执行铁律 — 严禁设定僭越与幻觉加戏】
7. 【空间无缝连通与常识补白】: 如果玩家的当前动作意图指向某个直接连通的邻接场景（<adjacent_locations>）或其边界（如敲邻接小屋的门）：
   - 你必须在叙事中自然地将当前空间与该目标场景连通起来（例如发挥语言常识进行合理补白：描写敲门声打破死寂，NPC 听到动静后穿过未详细描写的走廊走过来开门）。
   - 你必须参考该目标场景中实体的特征标签（tags）。如目标 NPC 带有 "anxious"（焦虑）标签，你描写他出场、回应时的神态与语调必须符合其"焦虑、急切"的心理状态。
8. 【时空与标签关联过滤】: 邻接场景中可能包含当前不可见或与当前动作完全无关的实体，你必须作为理性的守密人执行严格过滤：
   - 检查实体标签。如公墓里包含带有 ["night_only"] 标签的食尸鬼，但当前 <session_state> 中的 <current_time> 为 AFTERNOON（下午），时空不匹配，你必须在叙事中完全忽略该生物，严禁提及它的任何异动或痕迹。
   - 如果玩家当前动作与该场景完全无关（如玩家在敲金博尔宅的门，而食尸鬼静态配置存在于相邻的公墓），该公墓实体在此轮属于无用信息，严禁将其引入当前叙事中。
9. 【严格基于事实边界】: 严禁凭空添加、编造任何物理现实 XML（<physical_reality>）中未包含的第三方 NPC、灵异异响、未知黏液或怪物踪迹。在玩家未解锁神话线索的日常探索期，必须将超自然异象的节奏控制权完整交还给规则内核。
10. 【物品可见性规则】: <items> 中的 item 是在场景中肉眼可见的物品。
    - state 属性表示该物品当前可观察到的物理状态（如 "broken_latch"、"messy"），大多可以直接用于环境描写。
    - tags 属性描述物品的属性标签，其中部分标签（如 "wooden"、"heavy"、"dusty"）代表直观外观可直接用于描写；部分标签（如 "entrance"等）代表需要玩家互动或检定才能发现的隐含信息，禁止在叙事中直接点破。
    - 两者中的数据没有绝对可见性，必须结合场景和玩家行为判断哪些信息可以直接描写，哪些需要保留。

【物理裁决铁律 — 优先级高于一切约束】
11. 本回合的物理裁决结果在 actions 的 rule_context 中以以下字段提供：
    - physical_executed: true/false  — 动作是否切实执行
    - execution_phase: NORMAL / LOCKED / ALREADY_SEARCHED / OUT_OF_REACH
    - spatial_reason: OK / OUT_OF_REACH / TARGET_NOT_FOUND
    - is_locked: true/false
    - is_searched: true/false
    - clues_discovered: [...] — 发现的线索列表（为空时严禁编造任何线索或暗示）
12. 若 physical_executed=false，你的叙事必须围绕动作因物理约束未能执行展开：
    - LOCKED: 描写锁/障碍物阻挡动作，如银制锁扣得死死的、门纹丝不动
    - OUT_OF_REACH: 描写距离/空间阻隔，如那个物品在另一个房间、你够不着
    - ALREADY_SEARCHED: 描写已经仔细搜过了、没什么新发现
13. 若 clues_discovered 为空，严禁在叙事中暗示有什么东西被遗漏了或还有什么可查的。

输出纯文本，不要包含角色名或引号外的格式标记。"""

NPC_NARRATOR_SUPPLEMENT = """[场景附加指令 — NPC 对话]
当前玩家正在与一位 NPC 对话。NPC 的发言原文在 <NPC_DIALOGUE> 标记中。

在 NPC 发言前加入简短的情景描写和 NPC 神态动作，使对话自然融入场景。

【★★★★★ 最高优先级规则】
NPC_DIALOGUE 中引号内的部分必须逐字保留，绝对不得改写、删减或概括。"""

NARRATOR_CHAIN_SUPPLEMENT = """[时序映射指令]
以下是本回合的已执行行动链（executed_actions）。请按顺序逐段翻译。

每个 action 包含：
- rule_context: 规则裁决的绝对结果（掷骰点、成功等级、物理裁决等），叙事不得与之矛盾
- flavor_context: 玩家的 RP 修辞文本，必须融入该段的描写中
- raw_fixed_text: 模组预设的绝对文本，如果有，必须逐字保留

分段规则：
先按顺序处理每个 action，段与段之间用自然过渡句连接，使整段叙事流畅。
不要添加 rule_context 中未提及的新规则事实。

【物理裁决特别指令】
对于包含 physical_executed / execution_phase 字段的 action：
- 如果 physical_executed=false 且 execution_phase=LOCKED，叙事必须围绕无法打开/无法操作展开
- 如果 physical_executed=false 且 execution_phase=OUT_OF_REACH，叙事必须体现距离阻隔
- 如果 execution_phase=ALREADY_SEARCHED，简要提及此地已搜过即可
- physical_executed=true 时正常描写动作结果

空间/标签/剧透等约束（见上文【守密人核心执行铁律】）仍然适用于整个叙事。"""


MOVEMENT_NARRATOR_SUPPLEMENT = """[场景附加指令 — 移动]
当前玩家正在从一个场景移动到另一个场景。

根据 <physical_reality> 中的场景信息，基于以下原则描写环境过渡：
先基于当前 location 的 base_desc 渲染宏观氛围（光线、气味、空间感）。
再将 <items> 中的所有物品自然编织进背景描写中——作为该场景中"在那里"的可见物件提及，让玩家知道它们的存在和大致外观。
如果目标位置有 <present_entities>，自然提及他们的存在。
如果来源地与目标地通过某个方向（<direction>）相连，在叙事中体现移动方向。

【重要】叙事历史中的前情应保持连贯，不要否定或忽略之前发生的事。"""


# ====================================================================
# 模板兜底 — 无 LLM 时使用
# TODO: 清楚所谓的兜底逻辑，没有 LLM 玩个蛋
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
    intent_type: str = "",
    dialogue_history_str: str = "",
) -> LLMResult:
    """调用 LLM 生成叙事文本

    physical_reality: 来自 DB Lookup Node 的 <physical_reality> XML
    rag_context:      来自 RAG Lookup Node 的 <semantic_knowledge> 或空
    npc_dialogue:     来自 NPC Dialogue Node 的 NPC 发言原文（若有）
    clue_discovery:   来自 Archivist 的技能检定线索原文（若有）
    intent_type:      意图类型，用于选择专用 prompt（如 MOVE 用导航 prompt）
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

        # 构建消息列表：基础 prompt → 场景附加指令 → 用户上下文
        is_npc_scene = bool(npc_dialogue)
        is_movement = intent_type == "MOVE"
        supplementary = ""
        if is_npc_scene:
            supplementary = NPC_NARRATOR_SUPPLEMENT
        elif is_movement:
            supplementary = MOVEMENT_NARRATOR_SUPPLEMENT

        messages = [
            {"role": "system", "content": NARRATOR_SYSTEM_PROMPT + spoiler_constraint},
        ]
        if supplementary:
            messages.append({"role": "system", "content": supplementary})

        # 组装上下文
        context_parts = [f"游戏阶段: {game_phase}"]
        context_parts.append(f"玩家意图: {intent.get('data', {}).get('action', '未知')}")
        context_parts.append(f"裁决结果: {resolution}")

        if is_npc_scene:
            context_parts.append(f"\n<NPC_DIALOGUE>\n{npc_dialogue}\n</NPC_DIALOGUE>")
        if physical_reality:
            context_parts.append(f"\n{physical_reality}")
        if rag_context:
            context_parts.append(f"\n{rag_context}")
        if clue_discovery:
            context_parts.append(f"\n<clue_discovery>\n{clue_discovery}\n</clue_discovery>")

        # 用结构化对话历史替换原来的截断叙事历史
        if dialogue_history_str:
            context_parts.append(f"\n【近5轮对话历史】\n{dialogue_history_str}")
        else:
            context_parts.append(f"叙事历史: {narrative_history[-300:] if narrative_history else '无'}")
        context = "\n".join(context_parts)
        messages.append({"role": "user", "content": context})

        return await _call_llm("standard", messages)

    except Exception as e:
        logger.warning(f"narrator_node: LLM 调用失败: {e}")
        return LLMResult(text=None, tier="standard", model_name="",
                         messages=[], success=False, error=str(e))


def _build_chain_context(actions: list[dict], npc_results: list[dict]) -> str:
    """构建结果链的格式化上下文，消费 executed_actions + npc_dialogue_results"""
    import json
    parts = []
    parts.append("<executed_actions>")
    for i, action in enumerate(actions):
        parts.append(f"  <action index='{i}'>")
        parts.append(f"    <intent_type>{action.get('intent_type', '')}</intent_type>")
        if action.get("core_action"):
            parts.append(f"    <core_action>{action['core_action']}</core_action>")
        if action.get("detail"):
            parts.append(f"    <detail>{action['detail']}</detail>")
        parts.append(f"    <rule_context>{json.dumps(action.get('rule_context', {}), ensure_ascii=False)}</rule_context>")
        if action.get("flavor_context"):
            parts.append(f"    <flavor_context>{action['flavor_context']}</flavor_context>")
        if action.get("raw_fixed_text"):
            parts.append(f"    <raw_fixed_text>{action['raw_fixed_text']}</raw_fixed_text>")
        parts.append(f"  </action>")
    parts.append("</executed_actions>")
    if npc_results:
        parts.append("<npc_dialogues>")
        for nr in npc_results:
            parts.append(f"  <dialogue npc='{nr['npc_name']}'>{nr['dialogue_text']}</dialogue>")
        parts.append("</npc_dialogues>")
    return "\n".join(parts)


async def _call_llm_for_narrative_chain(
    actions: list[dict],
    npc_results: list[dict],
    physical_reality: str = "",
    rag_context: str = "",
    narrative_history: str = "",
    player_input: str = "",
    dialogue_history_str: str = "",
    known_knowledge: list[str] | None = None,
) -> LLMResult:
    """调用 LLM 基于 executed_actions 链生成叙事文本"""
    try:
        # 防剧透软约束：玩家已发现的知识白名单
        spoiler_constraint = ""
        if known_knowledge:
            spoiler_constraint = (
                "\n[重要约束] 玩家当前已发现的线索如下：\n"
                + "\n".join(f"- {k}" for k in known_knowledge)
                + "\n未在上述列表中的信息，玩家角色目前不知道，不应在叙事中作为已知事实提及。"
            )

        chain_text = _build_chain_context(actions, npc_results)
        messages = [
            {"role": "system", "content": NARRATOR_SYSTEM_PROMPT + spoiler_constraint},
            {"role": "system", "content": NARRATOR_CHAIN_SUPPLEMENT},
        ]
        context_parts = [chain_text]
        if physical_reality:
            context_parts.append(physical_reality)
        if rag_context:
            context_parts.append(rag_context)
        if player_input:
            context_parts.append(f"<player_input>\n{player_input}\n</player_input>")
        if dialogue_history_str:
            context_parts.append(f"\n【近5轮对话历史】\n{dialogue_history_str}")
        else:
            context_parts.append(f"叙事历史: {narrative_history[-300:] if narrative_history else '无'}")
        messages.append({"role": "user", "content": "\n".join(context_parts)})
        return await _call_llm("standard", messages)
    except Exception as e:
        logger.warning(f"narrator_node[chain]: LLM 调用失败: {e}")
        return LLMResult(text=None, tier="standard", model_name="",
                         messages=[], success=False, error=str(e))


async def narrate_node(state: GameState) -> dict:
    """
    叙事生成节点。

    优先消费 executed_actions 结果链（多意图串行循环产出），
    回退到旧单 intent+resolution 路径（兼容存量图拓扑）。

    返回 narrative + narrative_output + _llm_trace。
    """
    actions = state.get("executed_actions", [])
    npc_results = get_current_player(state).get("npc_dialogue_results", [])
    physical_reality = state.get("physical_reality", "")
    rag_context = state.get("rag_context", "")
    narrative_history = state.get("narrative", "")
    session_id = state.get("world_id", "")

    # 从 dialogue_history 取近5轮作为对话历史上下文
    dialogue_history = state.get("dialogue_history", [])
    dialogue_history_str = format_dialogue_history(dialogue_history, recent_n=5)

    # ── 获取玩家已发现的知识（防剧透） ──
    known_knowledge: list[str] = []
    if session_id:
        try:
            from src.state.session_state import SessionKnowledgeState
            sks = SessionKnowledgeState()
            known_knowledge = await sks.get_discovered_knowledge_ids(session_id)
        except Exception as e:
            logger.debug(f"narrator_node: 无法获取已发现知识: {e}")

    player_input = state.get("player_input", "")

    # ── 路径 A: 有 executed_actions 链 → 链消费 ──
    if actions:
        result = await _call_llm_for_narrative_chain(
            actions=actions,
            npc_results=npc_results,
            physical_reality=physical_reality,
            rag_context=rag_context,
            narrative_history=narrative_history,
            player_input=player_input,
            dialogue_history_str=dialogue_history_str,
            known_knowledge=known_knowledge,
        )
        if result.is_ok:
            narrative = result.text
            logger.info(f"narrator_node[chain]: {len(narrative)} chars, {len(actions)} actions")
            return {
                "narrative": narrative,
                "narrative_output": narrative,
                "_llm_trace": result.to_trace(),
            }
        logger.debug("narrator_node[chain]: LLM 失败，降级到模板")

    # ── 路径 B: 旧单意图路径（兼容） ──
    intent = state.get("intent") or {}
    resolution = state.get("resolution") or {}
    npc_dialogue = get_current_player(state).get("npc_dialogue", "")
    is_npc_scene = bool(npc_dialogue)

    archivist_result = state.get("archivist_result")
    clue_discovery = ""
    if archivist_result:
        clue_discovery = archivist_result.get("flavor_text", "") or ""

    intent_type = intent.get("type", "")
    result = await _call_llm_for_narrative(
        intent, resolution, state.get("game_phase", "exploration"), narrative_history,
        physical_reality=physical_reality,
        rag_context=rag_context,
        known_knowledge=known_knowledge,
        npc_dialogue=npc_dialogue,
        clue_discovery=clue_discovery,
        intent_type=intent_type,
        dialogue_history_str=dialogue_history_str,
    )

    if result.is_ok:
        narrative = result.text
        if is_npc_scene and npc_dialogue:
            narrative = _validate_npc_dialogue(narrative, npc_dialogue)
        logger.info(f"narrator_node[legacy]: {len(narrative)} chars")
    else:
        if is_npc_scene:
            npc_name = resolution.get("npc_name", "对方")
            narrative = f"{npc_name}说道：\"{npc_dialogue}\""
        else:
            narrative = _template_narrative(state)
        logger.info(f"narrator_node[template]: {len(narrative)} chars")

    return {
        "narrative": narrative,
        "narrative_output": narrative,
        "_llm_trace": result.to_trace() if result.is_ok else None,
    }


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
