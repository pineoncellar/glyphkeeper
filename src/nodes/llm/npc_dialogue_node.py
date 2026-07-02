# -*- coding: utf-8 -*-
"""
@File     :   npc_dialogue_node.py
@Desc     :   NPC 对话节点 — 根据意图中的 NPC 目标检索人设并生成对话回应
@Note     :   使用 standard 级别 LLM；无 LLM 时使用模板兜底
              dialogue_clues 数据由模组摄入时写入 LightRAG
"""

from __future__ import annotations

import random
from typing import Optional
from src.state.game_state import GameState
from src.tools import get_logger, get_settings
from src.tools.llm_client import call_llm as _call_llm, LLMResult
from src.memory.retriever import Retriever

logger = get_logger(__name__)


# ====================================================================
# Prompt 模板
# ====================================================================

NPC_SYSTEM_PROMPT = """你是克苏鲁的呼唤 TRPG 中的一位非玩家角色 (NPC)。

请严格按照以下 NPC 人设信息来回应玩家。你的任务是扮演这个 NPC，而不是讲述故事。

扮演规则:
- 完全以 NPC 的第一人称说话，直接回应玩家
- 反映 NPC 的性格、知识范围和对玩家的态度
- 如果 NPC 有线索或信息，只有在玩家问到相关话题时才暗示性地透露
- 保持克苏鲁风格的神秘感和时代感（1920 年代）
- 输出纯对话文本，不要包含动作描述或格式标记

NPC 人设信息:
{npc_profile}

对话历史上下文:
{context}

请基于以上人设，对玩家的最后一句话做出回应。保持简洁，回复 1-3 句即可。"""


# ====================================================================
# 模板兜底 — 无 LLM 时使用
# TODO: 完善逻辑，删去
# ====================================================================

_NPC_GREETINGS: list[str] = [
    "你好，陌生人。有什么事吗？",
    "你打量着我，我也打量着你。",
    "嗯？你找我？",
    "啊，又见面了。",
]

_NPC_QUESTIONS: list[str] = [
    "哦？你想知道什么？",
    "我不太确定你在问什么。",
    "说来话长…你确定要听？",
    "这件事…我不方便多说。",
]

_NPC_FAREWELLS: list[str] = [
    "就这样吧。",
    "保重。",
    "后会有期。",
]

_NPC_HOSTILE: list[str] = [
    "滚开，别来烦我。",
    "你最好离远点。",
    "哼，我没什么好跟你说的。",
]

# 按 NPC 态度分组的模板池
_NPC_TEMPLATES: dict[str, list[str]] = {
    "neutral": _NPC_GREETINGS,
    "hostile": _NPC_HOSTILE,
}


def _get_npc_attitude(npc_name: str, npc_relations: dict) -> str:
    """从 NPC 关系数据中获取当前态度

    根据对话次数和最后互动时间判断态度倾向。
    初始态度为 neutral。
    """
    rel = npc_relations.get(npc_name, {})
    disposition = rel.get("disposition", "neutral")
    if disposition in _NPC_TEMPLATES:
        return disposition
    return "neutral"


# ====================================================================
# 全局检索器（懒加载）
# ====================================================================

_retriever: Optional[Retriever] = None


async def _get_retriever() -> Retriever:
    global _retriever
    if _retriever is None:
        _retriever = Retriever()
    return _retriever


# ====================================================================
# NPC 数据检索
# ====================================================================


async def _retrieve_npc_profile(npc_name: str) -> str:
    """从 VectorStore 检索 NPC 人设数据

    优先检索 source_type 包含"npc"或"entity"的记录，
    通过 NPC 名称关键词匹配。
    """
    try:
        retriever = await _get_retriever()
        vs = await retriever.vector_store
        result = await vs.query(
            question=f"NPC {npc_name} 人设 性格 对话",
            top_k=5,
        )
        if result and result.strip():
            return result.strip()
    except Exception as e:
        logger.warning(f"NPC 人设检索失败 ({npc_name}): {e}")

    return f"（{npc_name} — 一位普通的 NPC，态度中立）"


async def _retrieve_recent_context(session_id: str, npc_name: str) -> str:
    """检索与当前 NPC 相关的最近对话历史"""
    try:
        retriever = await _get_retriever()
        history = await retriever.retrieve_history(session_id, limit=15)
        if not history:
            return "（无先前对话记录）"

        # 只提取与当前 NPC 相关或最近的事件
        relevant = []
        for evt in reversed(history[-8:]):
            evt_type = evt.get("type", "")
            evt_data = evt.get("data", {})
            patch = evt_data.get("patch", {}) if isinstance(evt_data, dict) else {}
            narrative = patch.get("narrative", "")
            source = patch.get("source_npc", "")
            if source == npc_name or evt_type == "NPCDialogue":
                relevant.append(narrative)
            elif narrative and len(relevant) < 3:
                relevant.append(narrative)

        if not relevant:
            return "（无先前对话记录）"

        return "\n".join(f"- {r[:150]}" for r in relevant[-5:])
    except Exception as e:
        logger.debug(f"对话历史检索失败: {e}")
        return "（对话历史检索暂不可用）"


# ====================================================================
# LLM 调用
# ====================================================================


async def _generate_npc_response_llm(
    npc_name: str,
    npc_profile: str,
    player_input: str,
    context: str,
    physical_reality: str = "",
) -> LLMResult:
    """调用 LLM 生成 NPC 对话回应

    注入 physical_reality 让 NPC 感知当前场景，防止编造不存在的场所。
    """
    try:
        profile_text = npc_profile[:800]
        context_text = context[:500]
        scene_hint = f"\n当前场景: {physical_reality[:300]}" if physical_reality else ""
        messages = [
            {"role": "system", "content": NPC_SYSTEM_PROMPT.format(
                npc_profile=profile_text,
                context=context_text,
            ) + scene_hint},
            {"role": "user", "content": f"玩家对你说: {player_input}"},
        ]
        return await _call_llm("standard", messages)
    except Exception as e:
        logger.warning(f"NPC 对话 LLM 调用失败: {e}")
        return LLMResult(text=None, tier="standard", model_name="",
                         messages=[], success=False, error=str(e))


# ====================================================================
# 模板兜底
# ====================================================================


def _template_npc_response(npc_name: str, attitude: str) -> str:
    """基于 NPC 态度的模板回应"""
    templates = _NPC_TEMPLATES.get(attitude, _NPC_GREETINGS)
    greeting = random.choice(templates)
    return greeting


# ====================================================================
# 线索授予判定
# ====================================================================


def _check_clue_grant(
    npc_name: str,
    player_input: str,
    active_tags: list[str],
    npc_relations: dict,
) -> list[str]:
    """检查是否满足线索授予条件

    条件组合:
    - 与特定 NPC 对话达到一定次数
    - 玩家输入包含关键词触发了线索条件
    - 尚不拥有该线索对应的 tag

    返回需要新激活的 tag 列表。
    """
    rel = npc_relations.get(npc_name, {})
    talk_count = rel.get("talk_count", 0)

    # 当前对话次数 ≥ 2 且玩家表达了对特定话题的兴趣时才授予线索
    KEYWORD_TAG_MAP = {
        "奇怪": "clue_oddity",
        "线索": "clue_aware",
        "调查": "clue_investigate",
        "秘密": "clue_secret",
    }
    new_tags: list[str] = []
    for keyword, tag in KEYWORD_TAG_MAP.items():
        if keyword in player_input and tag not in active_tags and talk_count >= 2:
            new_tags.append(tag)
    return new_tags


# ====================================================================
# Node 主函数
# ====================================================================


async def npc_dialogue_node(state: GameState) -> dict:
    """
    NPC 对话节点 — 主入口。

    执行流:
      从 intent 提取 NPC 名称
      检索 NPC 人设数据
      调用 LLM 或模板生成 NPC 回应
      检查线索授予条件
      返回 state_patch 包含 narrative + 事件 + 可能的 tag 更新

    返回 dict:
      - narrative: NPC 的对话文本
      - resolution: 对话过程元数据
      - active_tags: 新增的激活标签
      - emitted_events: NPCDialogue 事件
    """
    intent = state.get("intent") or {}
    intent_data = intent.get("data") or {}
    session_id = state.get("session_id", "default")
    player_input = state.get("player_input", "")
    npc_relations = state.get("npc_relations") or {}
    active_tags = state.get("active_tags", [])

    # 从 disambiguation_node 输出的 resolved_targets 获取消歧后的 NPC 名称
    resolved = state.get("resolved_targets") or {}
    npc_name = resolved.get("primary_id", "")
    if not npc_name:
        # 兜底：直接从 intent 提取
        npc_name = intent_data.get("target", "")
        if not npc_name:
            action = intent_data.get("action", "")
            detail = intent_data.get("detail", "")
            npc_name = detail or action

    if not npc_name:
        logger.debug("npc_dialogue_node: 未指定 NPC 目标")
        return {
            "narrative": "你环顾四周，没有找到可以对话的对象。",
            "resolution": {"success": False, "error": "no_npc_target"},
        }

    # 检索 NPC 人设 + 对话历史
    npc_profile, context = await _retrieve_npc_profile(npc_name), ""
    ctx_vs = await _retrieve_recent_context(session_id, npc_name)
    if ctx_vs:
        context = ctx_vs

    # 注入当前场景上下文
    physical_reality = state.get("physical_reality", "") or state.get("world_context", "")

    # 更新 NPC 关系计数
    rel = dict(npc_relations.get(npc_name, {}))
    rel["talk_count"] = rel.get("talk_count", 0) + 1
    rel["last_talk"] = player_input[:100]

    # 态度判定
    attitude = rel.get("disposition", "neutral")
    if attitude == "unknown":
        attitude = "neutral"

    # 尝试 LLM 生成
    llm_result = await _generate_npc_response_llm(
        npc_name, npc_profile, player_input, context,
        physical_reality=physical_reality,
    )
    if llm_result.is_ok:
        npc_reply = llm_result.text
    else:
        npc_reply = _template_npc_response(npc_name, attitude)

    # ── 剥离括号动作描述（保证 narrate_node 拿到纯对话） ──
    import re
    npc_reply = re.sub(r'（[^)]*）', '', npc_reply)  # （全角括号）
    npc_reply = re.sub(r'\([^)]*\)', '', npc_reply)       # (半角括号)
    npc_reply = npc_reply.strip()

    # 线索授予
    new_tags = _check_clue_grant(npc_name, player_input, active_tags, npc_relations)
    updated_tags = list(active_tags)
    for t in new_tags:
        if t not in updated_tags:
            updated_tags.append(t)

    # 如果有新线索 → 发出 ClueDiscovered 事件
    emitted_events = [
        {
            "type": "NPCDialogue",
            "data": {
                "npc_name": npc_name,
                "attitude": attitude,
                "talk_count": rel["talk_count"],
                "tags_granted": new_tags,
            },
        },
    ]
    if new_tags:
        for tag in new_tags:
            try:
                from src.memory.event_store import create_event_store
                es = await create_event_store()
                await es.append(
                    session_id=session_id,
                    event_type="ClueDiscovered",
                    data={
                        "session_id": session_id,
                        "knowledge_id": tag,  # 以 tag 作为临时 knowledge_id
                        "source": "dialogue",
                        "character_name": "",
                        "flavor_text": f"通过与 {npc_name} 的对话，你获得了一条线索。",
                    },
                    source_node="npc_dialogue",
                )
                # 投影到 session_knowledge_state（尽力而为）
                from src.state.projector import StateProjector
                projector = StateProjector()
                await projector.handle({
                    "type": "ClueDiscovered",
                    "data": {
                        "session_id": session_id,
                        "knowledge_id": tag,
                        "source": "dialogue",
                        "character_name": "",
                    },
                })
                logger.info(f"npc_dialogue: ClueDiscovered 事件已发出: {tag}")
            except Exception as e:
                logger.warning(f"npc_dialogue: ClueDiscovered 事件失败: {e}")

    # 更新 npc_relations
    updated_relations = dict(npc_relations)
    updated_relations[npc_name] = rel

    logger.info(
        f"npc_dialogue_node: {npc_name} "
        f"attitude={attitude} "
        f"reply_len={len(npc_reply)} "
        f"new_tags={new_tags}"
    )

    # npc_dialogue 供 narrate_node 读取并包装成最终叙事文本。
    return {
        "npc_dialogue": npc_reply,
        "resolution": {
            "success": True,
            "npc_name": npc_name,
            "attitude": attitude,
            "talk_count": rel["talk_count"],
        },
        "npc_relations": updated_relations,
        "_llm_trace": llm_result.to_trace() if llm_result.is_ok else None,
        "active_tags": updated_tags,
        "emitted_events": emitted_events,
    }
