"""
SocialComponent
负责处理社交交互。
通过构建包含NPC人设、场景信息和已知线索的Prompt，让LLM扮演NPC进行对话。
"""
import json
import uuid
from typing import List, Dict, Any, Optional
from dataclasses import asdict

from ..core import get_logger
from ..core.events import ResolutionResult, IntentSocialInteractData
from .base import BaseComponent
from ..llm import LLMFactory
from ..memory.bridge import read_model
from ..memory import RAGEngine

logger = get_logger(__name__)

class SocialComponent(BaseComponent):
    def initialize(self):
        # 使用适合角色扮演的模型配置，如果没有专门的 'scene' 配置，回退到 'smart' 或 'standard'
        try:
            self.llm = LLMFactory.get_llm("scene")
        except Exception:
            self.llm = LLMFactory.get_llm("smart")

    async def handle_interaction(self, character_name: str, data: IntentSocialInteractData) -> ResolutionResult:
        """
        处理社交交互意图
        """
        target_name = data.target
        logger.info(f"SocialComponent handling interaction: {character_name} -> {target_name}")

        if not target_name:
             return ResolutionResult(
                state=True,
                success=True,
                outcome_desc=f"{character_name} 自言自语了一番。"
            )

        # 构建 NPC 人设
        try:
            npc_info = await self._get_npc_persona(target_name)
        except ValueError as e:
            return ResolutionResult(
                state=False,
                success=False,
                outcome_desc=f"找不到目标: {target_name}"
            )

        # 建立场景
        # 获取 NPC 所在位置（通常也是玩家所在位置，但需确认）
        scene_info = await self._get_scene_context(npc_info["location_id"], character_name, target_name)

        # 获取相关知识/线索
        # 决定调查员能从这位 NPC 口中得到什么
        # 结合 dialogue (玩家的话) 和 intention (玩家意图) 进行搜索
        query_parts = []
        if data.raw_dialogue:
            query_parts.append(data.raw_dialogue)
        if data.intention:
            query_parts.append(data.intention)
        
        query_content = " ".join(query_parts) if query_parts else "与调查员的对视"
        
        knowledge_context = await self._get_knowledge_context(
            npc_entity_id=npc_info["id"], 
            query=query_content
        )

        # 构建 Prompt 并生成回复
        response = await self._generate_npc_response(
            character_name=character_name,
            npc_info=npc_info,
            scene_info=scene_info,
            knowledge_context=knowledge_context,
            interaction_data=data
        )

        return ResolutionResult(
            state=True,
            success=True,
            outcome_desc=response
        )

    async def _get_npc_persona(self, npc_name: str) -> Dict[str, Any]:
        """从记忆模块搜寻NPC相关的信息，构造人设"""
        entity = await read_model("Entity", {"name": npc_name})
        if not entity:
            raise ValueError(f"NPC {npc_name} not found")

        # 尝试查询 RAG 获取更详细的 NPC 背景设定
        rag = await RAGEngine.get_instance()
        rag_results = await rag.query(f"关于NPC {npc_name} 的外貌、性格和背景", top_k=1)
        
        persona_desc = "无特别描述"
        if rag_results:
            persona_desc = rag_results # 假设是字符串

        return {
            "id": entity["id"],
            "name": entity["name"],
            "tags": entity.get("tags", []),
            "stats": entity.get("stats", {}),
            "location_id": entity.get("location_id"),
            "description": persona_desc
        }

    async def _get_scene_context(self, location_id: str, player_name: str, npc_name: str) -> str:
        """建立场景"""
        if not location_id:
            return "位置未知"

        location = await read_model("Location", {"id": location_id})
        if not location:
            return "位置数据丢失"

        # 获取在场的其他人
        entities = await read_model("Entity", {"location_id": location_id}, one=False) or []
        present_entities = [e["name"] for e in entities if e["name"] not in [player_name, npc_name]]
        
        scene_desc = f"""
当前地点: {location['name']}
环境描述: {location['base_desc']}
在场人员: {', '.join(present_entities) if present_entities else '只有你们两人'}
"""
        return scene_desc

    async def _get_knowledge_context(self, npc_entity_id: str, query: str) -> str:
        """从数据库中寻找相关的知识或者说线索"""
        
        # 查找该 NPC 身上直接关联的 ClueDiscovery
        clue_discoveries = await read_model("ClueDiscovery", {"entity_id": npc_entity_id}, one=False) or []
        
        known_clues = []
        for discovery in clue_discoveries:
            # 获取对应的 Knowledge 内容以了解其大致主题（比如tags），但不一定要全部透露
            knowledge = await read_model("Knowledge", {"id": discovery["knowledge_id"]})
            if knowledge:
                # 这里我们假设 discovery_flavor_text 是 NPC 知道的信息描述
                known_clues.append(f"- [线索] {discovery['discovery_flavor_text']} (相关标签: {knowledge.get('tags_granted')})")

        # 使用 RAG 搜索相关通用知识或背景 (Conversation history, World lore)
        rag = await RAGEngine.get_instance()
        rag_info = await rag.query(query, top_k=2)

        context = "【已知线索/秘密】\n"
        if known_clues:
            context += "\n".join(known_clues)
        else:
            context += "（该角色暂时没有关联特定的关键剧情线索）"
            
        context += f"\n\n【相关背景知识/记忆】\n{rag_info}"
        
        return context

    async def _generate_npc_response(
        self, 
        character_name: str, 
        npc_info: Dict[str, Any], 
        scene_info: str, 
        knowledge_context: str,
        interaction_data: IntentSocialInteractData
    ) -> str:
        """构建 Prompt 并让模型扮演 NPC"""
        
        # 安全处理可能为空的字段
        player_dialogue = interaction_data.raw_dialogue if interaction_data.raw_dialogue else "（沉默）"
        player_intention = interaction_data.intention if interaction_data.intention else "无明确意图"
        player_tone = interaction_data.tone if interaction_data.tone else "正常"
        
        system_prompt = f"""
你现在是游戏中的非玩家角色 (NPC): **{npc_info['name']}**。
正在与玩家角色 **{character_name}** 进行对话。

【角色设定】
{npc_info['description']}
状态标签: {', '.join(npc_info['tags'])}
属性参考: {json.dumps(npc_info['stats'], ensure_ascii=False)}

【当前场景】
{scene_info}

【你可以提供的信息】
{knowledge_context}

【玩家输入】
玩家原话: "{player_dialogue}"
交流意图: {player_intention}
语气/态度: {player_tone}

【指令】
请以 {npc_info['name']} 的身份做出回应。
1. **风格一致性**: 严格遵守角色设定、说话风格和当前状态（如恐惧、愤怒、虚弱等）。
2. **场景感知**: 你的回答应体现你身处当前环境中。
3. **信息披露**: 
   - 如果玩家询问了你不知道的事情，自然地表示不知道。
   - 如果玩家询问了你掌握的【线索】，请根据你对玩家的态度（友好/敌对/怀疑）以及玩家的沟通方式决定是否透露。
   - 不要一次性把所有秘密倒出来，要像真人一样交流。
4. **格式**: 
   - 直接输出你的台词。
   - 如果有肢体动作或表情，请用括号包裹，例如：(皱眉) 我不知道你在说什么。
   - 不要输出 "NPC:" 等前缀。
"""
        messages = [{"role": "user", "content": system_prompt}]
        
        response_text = ""
        async for chunk in self.llm.chat(messages):
             if isinstance(chunk, str):
                response_text += chunk
        
        return response_text
