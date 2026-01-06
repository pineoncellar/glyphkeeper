"""
数据模型定义
定义用于存储游戏世界状态的数据库模型
还有玩家角色卡和跑团对话记忆的存储结构
"""
"""
TODO:
- 考虑物品与实体在不同条件下的状态变化与场景联系
"""
import uuid
import enum
from typing import List, Optional
from datetime import datetime
from sqlalchemy import String, Text, Boolean, Integer, ForeignKey, Enum, DateTime
from sqlalchemy.dialects.postgresql import UUID, JSONB, ARRAY
from sqlalchemy.orm import relationship, Mapped, mapped_column
from .database import Base

class TimeSlot(str, enum.Enum):
    MORNING = "MORNING"
    AFTERNOON = "AFTERNOON"
    EVENING = "EVENING"
    LATE_NIGHT = "LATE_NIGHT"

class SourceType(str, enum.Enum):
    ITEM = "ITEM"
    OBSERVATION = "OBSERVATION"
    DIALOGUE = "DIALOGUE"

class Location(Base):
    """
    物理层：场景表
    存储客观存在的地点及其状态。
    """
    __tablename__ = "locations"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=True)  # 可选唯一key
    name: Mapped[str] = mapped_column(String, nullable=False)
    base_desc: Mapped[str] = mapped_column(Text, nullable=False)
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)
    exits: Mapped[dict] = mapped_column(JSONB, default=dict)

    interactables: Mapped[List["Interactable"]] = relationship(back_populates="location", cascade="all, delete-orphan")
    entities: Mapped[List["Entity"]] = relationship(back_populates="location")

class Interactable(Base):
    """
    物理层：交互物表
    存储场景中的物品，如书桌、宝箱等。
    """
    __tablename__ = "interactables"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=True)  # 可选唯一key
    name: Mapped[str] = mapped_column(String, nullable=False)
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list) # 承载 "block_exit:North", "hidden" 等逻辑
    state: Mapped[str] = mapped_column(String, default="default") # RAG 检索锚点

    # 位置互斥：在场景中 或 在某人身上
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    carrier_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("entities.id"), nullable=True)
    # 关系
    location: Mapped[Optional["Location"]] = relationship(back_populates="interactables")
    carrier: Mapped[Optional["Entity"]] = relationship(back_populates="inventory")
    
    # 关联线索发现
    clue_links: Mapped[List["ClueDiscovery"]] = relationship(back_populates="interactable", cascade="all, delete-orphan")

class Entity(Base):
    """
    物理层：实体表
    存储 NPC 或怪物，包含行为逻辑 Tags。
    """
    __tablename__ = "entities"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    key: Mapped[str] = mapped_column(String, unique=True, nullable=True)  # 可选唯一key
    name: Mapped[str] = mapped_column(String, nullable=False)
    location_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("locations.id"), nullable=True)
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)
    stats: Mapped[dict] = mapped_column(JSONB, default=dict) # 数值、db等属性，如果是玩家技能和san啥的也在这
    attacks: Mapped[list] = mapped_column(JSONB, default=list) # 战斗方式

    location: Mapped[Optional["Location"]] = relationship(back_populates="entities")
    inventory: Mapped[List["Interactable"]] = relationship(back_populates="carrier") # 关联关键物品
    dialogue_links: Mapped[List["ClueDiscovery"]] = relationship(back_populates="entity", cascade="all, delete-orphan") # 关联对话/观察线索

    # 与调查员档案一对一关系，扩展调查员特有的信息
    profile: Mapped[Optional["InvestigatorProfile"]] = relationship(
        back_populates="entity", 
        uselist=False,
        cascade="all, delete-orphan"
    )

class InvestigatorProfile(Base):
    """
    规则层：调查员档案
    仅作为 Entity 的扩展，存储纯文本档案和非数值逻辑。
    """
    __tablename__ = "investigator_profiles"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # 关联回 Entity
    entity_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("entities.id"), unique=True, nullable=False)
    
    # 基础信息
    player_name: Mapped[str] = mapped_column(String, nullable=True) # 玩家名
    occupation: Mapped[str] = mapped_column(String, default="Unknown") # 职业
    age: Mapped[int] = mapped_column(Integer, default=25)
    gender: Mapped[str] = mapped_column(String, default="Unknown")
    residence: Mapped[str] = mapped_column(String, nullable=True)
    birthplace: Mapped[str] = mapped_column(String, nullable=True)

    # 背景故事：形象描述、思想与信念、重要之人、意义非凡之地、宝贵之物、特质、伤口和疤痕、恐惧症和躁狂症
    # 姑且也在数据库存一份，不过主要是靠rag查询
    backstory: Mapped[dict] = mapped_column(JSONB, default=dict)

    # 资产
    # 具体的信用等级可以放 Entity.stats，但复杂的资产描述放这里
    assets_detail: Mapped[str] = mapped_column(Text, nullable=True)

    # 反向关系
    entity: Mapped["Entity"] = relationship(back_populates="profile")

class Knowledge(Base):
    """
    知识层：知识注册表
    线索的逻辑开关，指向 LightRAG 中的具体内容。
    """
    __tablename__ = "knowledge_registry"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    rag_key: Mapped[str] = mapped_column(String, nullable=False)
    tags_granted: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)
    
    discoveries: Mapped[List["ClueDiscovery"]] = relationship(back_populates="knowledge") # 反向关联

class ClueDiscovery(Base):
    """
    中间层：线索映射表
    连接物理实体/物品与逻辑知识，定义发现条件和情境描述。
    实现多对多 (N:N) 的逻辑映射。
    """
    __tablename__ = "clue_discoveries"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    
    # 互斥来源：物理物品 或 生物实体
    interactable_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("interactables.id"), nullable=True)
    entity_id: Mapped[Optional[uuid.UUID]] = mapped_column(ForeignKey("entities.id"), nullable=True)
    
    # 指向：核心知识
    knowledge_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("knowledge_registry.id"), nullable=False)
    
    # 逻辑：触发条件
    required_check: Mapped[dict] = mapped_column(JSONB, default=dict)
    
    # 叙事：发现时的情境描述
    discovery_flavor_text: Mapped[str] = mapped_column(Text, nullable=False)

    # 关系
    interactable: Mapped[Optional["Interactable"]] = relationship(back_populates="clue_links")
    entity: Mapped[Optional["Entity"]] = relationship(back_populates="dialogue_links")
    knowledge: Mapped["Knowledge"] = relationship(back_populates="discoveries")

class GameSession(Base):
    """
    控制层：会话状态表
    管理模糊时间流和全局状态 Tags。
    """
    __tablename__ = "game_session"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    time_slot: Mapped[TimeSlot] = mapped_column(Enum(TimeSlot), default=TimeSlot.MORNING)
    beat_counter: Mapped[int] = mapped_column(Integer, default=0)
    active_global_tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)
    # 调查员列表：存储参与本次会话的调查员 Entity ID
    investigator_ids: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)

class Event(Base):
    """
    控制层：事件表
    定义状态转换规则和触发条件。
    """
    __tablename__ = "events"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    trigger_condition: Mapped[dict] = mapped_column(JSONB, nullable=False)
    effect_script: Mapped[dict] = mapped_column(JSONB, nullable=False)


class DialogueRecord(Base):
    """
    最底层的原始对话记录。
    用于：全量存档、前端展示、以及构建 Sliding Window。
    """
    __tablename__ = "dialogue_records"

    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    # investigator_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("investigators.id"), index=True) # 暂时注释，等待 investigator 表定义
    investigator_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), index=True, nullable=True) # 临时改为 nullable=True，避免外键报错

    # 序号，确保顺序绝对正确
    turn_number: Mapped[int] = mapped_column(Integer, index=True) 
    
    # 角色: "user" 或 "assistant"
    role: Mapped[str] = mapped_column(String)
    
    # 内容: 原始文本
    content: Mapped[str] = mapped_column(Text)
    
    # 状态标记：这句话是否已经被"总结/固化"进 LightRAG 了？
    # True = 已归档，可以从 Prompt 中移除了（如果超出窗口）
    # False = 还在 Buffer 中，必须优先保留
    is_consolidated: Mapped[bool] = mapped_column(Boolean, default=False)
    
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)

class MemoryTrace(Base):
    """
    中层记忆索引。
    存储对话的摘要，作为 LightRAG 和 原始对话 之间的桥梁。
    """
    __tablename__ = "memory_traces"
    
    id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)
    summary: Mapped[str] = mapped_column(Text) # The compressed summary
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.now)
    
    # Range of dialogue turns this trace covers
    start_turn: Mapped[int] = mapped_column(Integer)
    end_turn: Mapped[int] = mapped_column(Integer)
    
    # Metadata for retrieval/filtering
    tags: Mapped[List[str]] = mapped_column(ARRAY(Text), default=list)
    importance_score: Mapped[int] = mapped_column(Integer, default=0)

    