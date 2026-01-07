"""
数据摄入模块
负责将 JSON 数据分发到 PostgreSQL (左脑) 和 LightRAG (右脑)
"""
import os
import json
import uuid
from pathlib import Path
from typing import Optional, List, Union, Dict, Any

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from ..core import get_logger
from ..memory.RAG_engine import get_rag_engine
from ..memory.database import DatabaseManager
from ..memory.models import (
    Location, Entity, Interactable, Knowledge, ClueDiscovery
)

logger = get_logger(__name__)

class ModuleLoader:
    """模组加载器"""
    def __init__(self, db_session: AsyncSession, rag_engine):
        self.db = db_session
        self.rag = rag_engine
        self.knowledge_map: Dict[str, uuid.UUID] = {} # rag_key -> db_uuid

    async def ingest_module(self, json_data: Dict[str, Any]):
        """
        全流程摄入
        """
        module_name = json_data.get('meta', {}).get('module_name', 'Unknown Module')
        logger.info(f"开始摄入模组: {module_name}")

        # 全局知识
        if 'global_knowledge' in json_data:
            await self._ingest_knowledge(json_data['global_knowledge'])
        
        # 摄入地点与实体
        if 'locations' in json_data:
            for loc_data in json_data['locations']:
                await self._ingest_location(loc_data)

        # TODO: 其他顶层结构，如事件等
        
        logger.info(f"模组 {module_name} 摄入完成")

    async def _ingest_knowledge(self, knowledge_list: List[Dict[str, Any]]):
        for k in knowledge_list:
            rag_key = k['key']
            
            # 左脑：注册逻辑开关
            # 检查是否已存在
            stmt = select(Knowledge).where(Knowledge.rag_key == rag_key)
            result = await self.db.execute(stmt)
            existing_k = result.scalar_one_or_none()

            if existing_k:
                self.knowledge_map[rag_key] = existing_k.id
                logger.debug(f"知识条目已存在: {rag_key}")
                # 即使存在，也可以选择更新 RAG 内容，这里暂且跳过重复插入 DB
            else:
                db_record = Knowledge(
                    rag_key=rag_key, 
                    tags_granted=k.get('tags_granted', [])
                )
                self.db.add(db_record)
                await self.db.flush() # 获取 ID
                self.knowledge_map[rag_key] = db_record.id

            # 右脑：存入具体内容
            # LightRAG 需要纯文本。我们把 tags 也塞进去增加语义关联。
            doc_text = f"""
            [Knowledge: {rag_key}]
            Content: {k['rag_content']}
            Related Tags: {', '.join(k.get('tags_granted', []))}
            """
            # 这里的 insert 是向 LightRAG 插入文档
            try:
                await self.rag.insert(doc_text)
                # 刷新 session 避免连接过期
                await self.db.execute(select(1))
            except Exception as e:
                logger.error(f"RAG 插入失败 (knowledge: {rag_key}): {e}")
                raise

    async def _ingest_location(self, loc_data: Dict[str, Any]):
        # 左脑：物理结构
        loc_db = Location(
            key=loc_data.get('key'),
            name=loc_data['name'],
            base_desc=loc_data['base_desc'], # DB 里留一份用于兜底
            tags=loc_data.get('tags', []),
            exits=loc_data.get('exits', {})
        )
        self.db.add(loc_db)
        await self.db.flush() # 以此获得 loc_db.id

        # 右脑：环境氛围
        # 把地点变成一段 RAG 容易理解的描述
        interactables_summary = self._summarize_interactables_text(loc_data.get('interactables', []))
        rag_text = f"""
        [Location: {loc_data['name']}]
        Description: {loc_data['base_desc']}
        Atmosphere Tags: {', '.join(loc_data.get('tags', []))}
        Possible Interactions: {interactables_summary}
        """
        # 将 UUID 作为 metadata 存入，或者直接拼在文本里，方便反向查库
        # 将 UUID 拼接到文本中，以便检索时能关联
        rag_text += f"\nDB_UUID: {str(loc_db.id)}"
        
        try:
            await self.rag.insert(rag_text)
            # 刷新 session 避免连接过期
            await self.db.execute(select(1))
        except Exception as e:
            logger.error(f"RAG 插入失败 (location: {loc_data['name']}): {e}")
            raise
        
        # 处理物体
        for item in loc_data.get('interactables', []):
            await self._ingest_interactable(item, loc_db.id)

        # 处理实体
        for entity in loc_data.get('entities', []):
            await self._ingest_entity(entity, loc_db.id)

    async def _ingest_interactable(self, item_data: Dict[str, Any], loc_id: uuid.UUID):
        item_db = Interactable(
            key=item_data.get('key'),
            name=item_data['name'],
            location_id=loc_id,
            state=item_data.get('state', 'default'),
            tags=item_data.get('tags', [])
        )
        self.db.add(item_db)
        await self.db.flush()

        # 处理线索
        for clue in item_data.get('clues', []):
            await self._ingest_clue(clue, source_id=item_db.id, source_type="interactable")

    async def _ingest_entity(self, entity_data: Dict[str, Any], loc_id: uuid.UUID):
        # 左脑：数值
        ent_db = Entity(
            key=entity_data.get('key'),
            name=entity_data['name'],
            location_id=loc_id,
            stats=entity_data.get('stats', {}),
            tags=entity_data.get('tags', []),
            attacks=entity_data.get('attacks', []),
        )
        self.db.add(ent_db)
        await self.db.flush()

        # 右脑：人设
        # 从 tags 或其他字段构建人设描述
        role_play_text = f"""
        [NPC Profile: {entity_data['name']}]
        Tags: {', '.join(entity_data.get('tags', []))}
        Stats: {json.dumps(entity_data.get('stats', {}), ensure_ascii=False)}
        Description: {entity_data.get('name')} is located at this place.
        """
        # 如果有 dialogue_clues，也可以作为性格参考
        dialogues = entity_data.get('dialogue_clues', [])
        if dialogues:
            role_play_text += "\nDialogue Examples:\n"
            for d in dialogues:
                role_play_text += f"- {d.get('flavor_text')}\n"

        try:
            await self.rag.insert(role_play_text)
            # 刷新 session 避免连接过期
            await self.db.execute(select(1))
        except Exception as e:
            logger.error(f"RAG 插入失败 (entity: {entity_data['name']}): {e}")
            raise

        # 处理线索 (对话)
        for clue in dialogues:
            await self._ingest_clue(clue, source_id=ent_db.id, source_type="entity")

    async def _ingest_clue(self, clue_data: Dict[str, Any], source_id: uuid.UUID, source_type: str):
        target_knowledge_key = clue_data.get('target_knowledge')
        
        # 如果没有对应的知识条目则不创建 ClueDiscovery
        if not target_knowledge_key:
            return

        knowledge_id = self.knowledge_map.get(target_knowledge_key)
        if not knowledge_id:
            logger.warning(f"Clue references unknown knowledge key: {target_knowledge_key}")
            return

        clue_db = ClueDiscovery(
            knowledge_id=knowledge_id,
            required_check=clue_data.get('required_check', {}),
            discovery_flavor_text=clue_data.get('flavor_text', ''),
            interactable_id=source_id if source_type == "interactable" else None,
            entity_id=source_id if source_type == "entity" else None
        )
        self.db.add(clue_db)

    def _summarize_interactables_text(self, interactables: List[Dict[str, Any]]) -> str:
        if not interactables:
            return "None"
        return ", ".join([f"{item['name']} ({item.get('state', 'default')})" for item in interactables])


async def load_module_from_json(file_path: Union[str, Path]):
    """
    从 JSON 文件加载模组数据到数据库和 LightRAG
    """
    file_path = Path(file_path)
    if not file_path.exists():
        logger.error(f"文件不存在: {file_path}")
        return False

    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            data = json.load(f)
    except Exception as e:
        logger.error(f"读取 JSON 失败: {e}")
        return False

    logger.info("初始化 RAG 引擎...")
    rag_engine = await get_rag_engine()
    
    # 使用独立的数据库 session
    db_manager = DatabaseManager()
    _ = db_manager.engine  # 确保连接池已创建
    
    async_session_factory = db_manager.session_factory
    
    # 使用 expire_on_commit=False 避免 session 关闭后无法访问对象
    async with async_session_factory() as session:
        # 设置较长的超时时间
        await session.execute(select(1))  # 预热连接
        
        loader = ModuleLoader(session, rag_engine)
        try:
            await loader.ingest_module(data)
            await session.commit()
            logger.info("模组数据成功提交到数据库")
            return True
        except Exception as e:
            await session.rollback()
            logger.error(f"模组摄入失败，回滚事务: {e}")
            import traceback
            logger.error(traceback.format_exc())
            return False


