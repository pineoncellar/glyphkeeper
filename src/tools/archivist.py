# -*- coding: utf-8 -*-
"""
@File     :   archivist.py
@Desc     :   线索管理员 — 由 archivist_node 调用的数据访问层
@Note     :   负责查询 PG 读模型、校验线索条件、写入 EventStore + 读模型投影
"""

from __future__ import annotations

from typing import Optional

from src.tools import get_logger

logger = get_logger(__name__)


# ── 技能名中英映射 ──
# TODO: 统一模组标准，把这不优雅的硬映射删了
_SKILL_NAME_ALIASES: dict[str, list[str]] = {
    "侦查": ["侦查", "spot hidden", "侦查(spot hidden)", "spot_hidden"],
    "聆听": ["聆听", "listen", "聆听(listen)"],
    "潜行": ["潜行", "stealth", "潜行(stealth)"],
    "图书馆利用": ["图书馆利用", "library use", "library", "library_use"],
    "神秘学": ["神秘学", "occult", "occult(神秘学)"],
    "心理学": ["心理学", "psychology", "心理学(psychology)"],
    "说服": ["说服", "persuade", "说服(persuade)"],
    "恐吓": ["恐吓", "intimidate", "恐吓(intimidate)"],
    "斗殴": ["斗殴", "fighting", "fighting(brawl)", "brawl"],
    "闪避": ["闪避", "dodge", "闪避(dodge)"],
    "急救": ["急救", "first aid", "first_aid"],
    "医学": ["医学", "medicine", "医学(medicine)"],
    "锁匠": ["锁匠", "locksmith", "lock picking", "lock_picking"],
    "机械维修": ["机械维修", "mechanical repair", "mechanical_repair"],
    "计算机使用": ["计算机使用", "computer use", "computer_use"],
    "手枪": ["手枪", "handgun", "手枪(handgun)"],
    "射击": ["射击", "firearm", "射击(firearm)", "rifle", "shotgun"],
}


def _skill_name_matches(required: str, actual: str) -> bool:
    """判断 required 技能名是否与 actual 匹配，支持中英别名"""
    if not required or not actual:
        return False
    if required.lower() == actual.lower():
        return True
    # 查别名表
    for aliases in _SKILL_NAME_ALIASES.values():
        if required.lower() in [a.lower() for a in aliases]:
            return actual.lower() in [a.lower() for a in aliases]
    # 兜底：子串匹配（以防有未收录的别名）
    return required.lower() in actual.lower() or actual.lower() in required.lower()


class Archivist:
    """线索管理员 — 在技能检定成功后检查是否有线索可发现

    连接到静态读模型（StaticReadStore）和事件存储（EventStore），
    为检定成功的目标查找并触发线索发现。

    注：不再负责 target key 解析（已上提至 archivist_node），
    收到什么 key 就查什么 key。
    """

    def __init__(self, static_store=None, event_store=None, session_state=None):
        self._static_store = static_store
        self._event_store = event_store
        self._session_state = session_state

    # ── 懒加载 ──

    @property
    async def static_store(self):
        if self._static_store is None:
            from src.state.read_models import StaticReadStore
            self._static_store = StaticReadStore()
        return self._static_store

    @property
    async def event_store(self):
        if self._event_store is None:
            from src.memory.event_store import create_event_store
            self._event_store = await create_event_store()
        return self._event_store

    @property
    async def session_state(self):
        if self._session_state is None:
            from src.state.session_state import SessionKnowledgeState
            self._session_state = SessionKnowledgeState()
        return self._session_state

    # ── 主入口 ──

    async def inspect_target(
        self,
        session_id: str,
        target_key: str,
        skill_name: str = "",
        skill_value: int = 0,
        roll_value: int = 0,
        character_name: str = "",
    ) -> Optional[dict]:
        """检查目标是否有可发现的线索

        先查物品线索（clue_discoveries WHERE interactable_id = target），
        无匹配再查 NPC 线索（clue_discoveries WHERE entity_key = target）。
        均无匹配时返回 None。

        注意：target_key 已由 archivist_node 解析完成，此处不再做降级匹配。
        """
        store = await self.static_store

        # 先查物品线索（按 key）
        item_clues = await store.get_clues_for_interactable(target_key)

        if item_clues:
            return await self._process_clues(
                clues=item_clues,
                session_id=session_id,
                skill_name=skill_name,
                skill_value=skill_value,
                roll_value=roll_value,
                character_name=character_name,
                source="examine",
            )

        # 再查 NPC 线索
        entity_clues = await store.get_clues_for_entity(target_key)
        if entity_clues:
            return await self._process_clues(
                clues=entity_clues,
                session_id=session_id,
                skill_name=skill_name,
                skill_value=skill_value,
                roll_value=roll_value,
                character_name=character_name,
                source="dialogue",
            )

        logger.debug(f"archivist: 目标 '{target_key}' 无关联线索")
        return None

    # ── 内部处理 ──

    async def _process_clues(
        self,
        clues: list[dict],
        session_id: str,
        skill_name: str,
        skill_value: int,
        roll_value: int,
        character_name: str,
        source: str,
    ) -> Optional[dict]:
        """逐条检查线索的发现条件是否满足

        无 required_check 的线索视为自动发现（如纯 flavor_text 的剧情提示）。
        有检定条件的线索需比对技能名和满足难度的成功率。
        """
        for clue in clues:
            raw_check = clue.get("required_check", {}) or {}
            # 防御：required_check 可能被存为 JSONB null 或意外字符串
            required_check = raw_check if isinstance(raw_check, dict) else {}

            if not required_check:
                return await self._grant_clue(
                    session_id=session_id,
                    knowledge_id=clue.get("knowledge_id", ""),
                    flavor_text=clue.get("flavor_text", ""),
                    source=source,
                    character_name=character_name,
                    loot_items=clue.get("loot_items", []),
                )

            # 有检定条件：先比对技能名（含中英映射）
            required_skill = required_check.get("skill", "")
            if required_skill and not _skill_name_matches(required_skill, skill_name):
                continue

            # 再比对掷骰结果是否满足线索要求的难度等级
            if roll_value > 0 and skill_value > 0:
                from src.domain.coc_rules import Difficulty

                difficulty_str = required_check.get("difficulty", "Regular")
                try:
                    difficulty = Difficulty[difficulty_str.upper()]
                except (KeyError, AttributeError):
                    difficulty = Difficulty.REGULAR

                threshold = self._get_threshold_for_difficulty(difficulty, skill_value)
                if roll_value <= threshold:
                    return await self._grant_clue(
                        session_id=session_id,
                        knowledge_id=clue.get("knowledge_id", ""),
                        flavor_text=clue.get("flavor_text", ""),
                        source=source,
                        character_name=character_name,
                        loot_items=clue.get("loot_items", []),
                    )

        return None

    async def _grant_clue(
        self,
        session_id: str,
        knowledge_id: str,
        flavor_text: str,
        source: str,
        character_name: str,
        loot_items: list = None,
    ) -> dict:
        """颁发线索：发出 ClueDiscovered 事件，随后投影到 session_knowledge_state

        返回含 knowledge_id、flavor_text、source、loot_items 的字典。
        投影失败仅记警告，不做回滚 — 事件本身已确保线索不会丢失。
        """
        loot = loot_items or []
        if not knowledge_id:
            logger.debug("archivist: 线索无 knowledge_id，仅返回 flavor_text（纯文本线索）")
            return {"knowledge_id": "", "flavor_text": flavor_text, "source": source, "loot_items": loot}

        # 先发出 ClueDiscovered 事件（主流程，保证不丢）
        es = await self.event_store
        await es.append(
            session_id=session_id,
            event_type="ClueDiscovered",
            data={
                "session_id": session_id,
                "knowledge_id": knowledge_id,
                "source": source,
                "character_name": character_name,
                "flavor_text": flavor_text,
            },
            source_node="archivist",
        )

        # 再投影到 session_knowledge_state（尽力而为，失败不阻塞主流程）
        try:
            from src.state.projector import StateProjector
            projector = StateProjector()
            await projector.handle({
                "type": "ClueDiscovered",
                "data": {
                    "session_id": session_id,
                    "knowledge_id": knowledge_id,
                    "source": source,
                    "character_name": character_name,
                },
            })
        except Exception as e:
            logger.warning(f"archivist: 线索投影失败（不影响主流程）: {e}")

        logger.info(
            f"archivist: 线索已授予 knowledge={knowledge_id} "
            f"source={source} session={session_id[:8]}"
            + (f" loot={loot}" if loot else "")
        )
        return {
            "knowledge_id": knowledge_id,
            "flavor_text": flavor_text,
            "source": source,
            "loot_items": loot,
        }

    @staticmethod
    def _get_threshold_for_difficulty(difficulty, skill_value: int) -> int:
        """根据 CoC 7e 难度等级和角色真实技能值返回掷骰阈值

        Regular 对应 skill_value，Hard 对应一半，Extreme 对应五分之一。
        skill_value 为 0 时回退到 50 避免除零。
        """
        sv = skill_value if skill_value > 0 else 50
        mapping = {
            "REGULAR": sv,
            "HARD": sv // 2,
            "EXTREME": sv // 5,
        }
        return mapping.get(difficulty.name, sv)
