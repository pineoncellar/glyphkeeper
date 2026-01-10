"""
数据桥接模块
提供通用的、基于字典的数据访问接口，屏蔽ORM细节。
"""
import uuid
import datetime
import enum
from typing import Any, Dict, List, Optional, Type, Union
from contextlib import asynccontextmanager
from sqlalchemy import select, and_
from sqlalchemy.ext.asyncio import AsyncSession
from .database import transactional, get_session
from . import models

# 建立模型名称到类的映射
MODEL_MAP = {
    "Location": models.Location,
    "Interactable": models.Interactable,
    "Entity": models.Entity,
    "InvestigatorProfile": models.InvestigatorProfile,
    "Knowledge": models.Knowledge,
    "ClueDiscovery": models.ClueDiscovery,
    "GameSession": models.GameSession,
    "Event": models.Event,
    "DialogueRecord": models.DialogueRecord,
    "MemoryTrace": models.MemoryTrace,
}

def _to_dict(instance: Any) -> Optional[Dict[str, Any]]:
    """
    将 SQLAlchemy 模型实例转换为字典。
    处理 UUID, DateTime, Enum 等特殊类型。
    """
    if not instance:
        return None
    
    data = {}
    # 遍历模型定义的所有列
    for column in instance.__table__.columns:
        value = getattr(instance, column.name)
        
        # 类型转换
        if isinstance(value, uuid.UUID):
            value = str(value)
        elif isinstance(value, datetime.datetime):
            value = value.isoformat()
        elif isinstance(value, enum.Enum):
            value = value.value
            
        data[column.name] = value
    return data

@transactional
async def fetch_model_data(
    target: Union[str, Type[models.Base]], 
    filters: Dict[str, Any] = None, 
    one: bool = True,
    session: AsyncSession = None
) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
    """
    获取模型数据
    target: 模型类或模型名称字符串
    filters: 过滤条件字典 {field_name: value}
    one: True=返回单个字典, False=返回字典列表
    session: 数据库会话（自动注入）
    """
    # 解析目标模型
    if isinstance(target, str):
        model_cls = MODEL_MAP.get(target)
        if not model_cls:
            raise ValueError(f"Unknown model name: {target}")
    else:
        model_cls = target

    stmt = select(model_cls)
    
    # 应用过滤条件
    if filters:
        conditions = []
        for k, v in filters.items():
            if hasattr(model_cls, k):
                column = getattr(model_cls, k)
                conditions.append(column == v)
        
        if conditions:
            stmt = stmt.where(and_(*conditions))
    
    result = await session.execute(stmt)
    
    if one:
        instance = result.scalars().first()
        return _to_dict(instance)
    else:
        instances = result.scalars().all()
        return [_to_dict(i) for i in instances]

@transactional
async def save_model_data(
    target: Union[str, Type[models.Base]], 
    data: Dict[str, Any], 
    match_keys: List[str] = None,
    session: AsyncSession = None
) -> Dict[str, Any]:
    """
    写入模型数据（创建或更新）
    target: 模型类或模型名称字符串
    data: 要保存的数据字典
    match_keys: 用于匹配现有记录的字段列表。如果为 None，默认尝试使用 'id'。
    session: 数据库会话（自动注入）
    """
    # 解析目标模型
    if isinstance(target, str):
        model_cls = MODEL_MAP.get(target)
        if not model_cls:
            raise ValueError(f"Unknown model name: {target}")
    else:
        model_cls = target

    if match_keys is None:
        match_keys = ["id"]

    # 尝试查找现有记录
    instance = None
    
    # 只有当 data 中包含了所有 match_keys 时才尝试查找
    if all(k in data for k in match_keys):
        stmt = select(model_cls)
        conditions = []
        for key in match_keys:
            conditions.append(getattr(model_cls, key) == data[key])
        
        stmt = stmt.where(and_(*conditions))
        result = await session.execute(stmt)
        instance = result.scalars().first()

    if instance:
        # 更新现有记录
        for k, v in data.items():
            # 跳过主键更新（通常不应该更新主键）
            if k in match_keys:
                continue
                
            if hasattr(instance, k):
                # 简单的类型处理：如果是 UUID 字符串且列是 UUID 类型，SQLAlchemy 通常能处理
                # 但对于 Enum，可能需要转换
                col_type = getattr(model_cls, k).type
                
                # 如果传入的是字符串，但目标是 Enum，尝试转换
                if hasattr(col_type, "python_type") and issubclass(col_type.python_type, enum.Enum):
                    if isinstance(v, str):
                         try:
                             v = col_type.python_type(v)
                         except ValueError:
                             pass # 转换失败则保持原样，让 SQLAlchemy 报错或处理
                             
                setattr(instance, k, v)
    else:
        # 创建新记录
        # 过滤掉不在 model 中的字段
        valid_data = {}
        for k, v in data.items():
            if hasattr(model_cls, k):
                # 同样处理 Enum
                col_type = getattr(model_cls, k).type
                if hasattr(col_type, "python_type") and issubclass(col_type.python_type, enum.Enum):
                    if isinstance(v, str):
                         try:
                             v = col_type.python_type(v)
                         except ValueError:
                             pass
                valid_data[k] = v
                
        instance = model_cls(**valid_data)
        session.add(instance)
    
    # 刷新以获取默认值和ID
    await session.flush()
    await session.refresh(instance)
    
    return _to_dict(instance)


# ============ 事务上下文管理 ============

@asynccontextmanager
async def transaction_context():
    """
    事务上下文管理器，用于需要多次操作在同一事务中执行的场景。
    
    使用示例：
        async with transaction_context() as tx:
            entity = await tx.fetch("Entity", {"name": "Player"})
            entity["stats"]["hp"] -= 10
            await tx.save("Entity", entity)
    """
    async with get_session() as session:
        tx = TransactionContext(session)
        try:
            yield tx
            await session.commit()
        except Exception:
            await session.rollback()
            raise


class TransactionContext:
    """在同一事务中执行多个数据库操作"""
    
    def __init__(self, session: AsyncSession):
        self._session = session
    
    async def fetch(
        self,
        target: Union[str, Type[models.Base]], 
        filters: Dict[str, Any] = None, 
        one: bool = True
    ) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
        """在当前事务中获取数据"""
        # 复用 fetch_model_data 的逻辑，但传入当前 session
        if isinstance(target, str):
            model_cls = MODEL_MAP.get(target)
            if not model_cls:
                raise ValueError(f"Unknown model name: {target}")
        else:
            model_cls = target

        stmt = select(model_cls)
        
        if filters:
            conditions = []
            for k, v in filters.items():
                if hasattr(model_cls, k):
                    column = getattr(model_cls, k)
                    conditions.append(column == v)
            
            if conditions:
                stmt = stmt.where(and_(*conditions))
        
        result = await self._session.execute(stmt)
        
        if one:
            instance = result.scalars().first()
            return _to_dict(instance)
        else:
            instances = result.scalars().all()
            return [_to_dict(i) for i in instances]
    
    async def save(
        self,
        target: Union[str, Type[models.Base]], 
        data: Dict[str, Any], 
        match_keys: List[str] = None
    ) -> Dict[str, Any]:
        """在当前事务中保存数据"""
        if isinstance(target, str):
            model_cls = MODEL_MAP.get(target)
            if not model_cls:
                raise ValueError(f"Unknown model name: {target}")
        else:
            model_cls = target

        if match_keys is None:
            match_keys = ["id"]

        instance = None
        
        if all(k in data for k in match_keys):
            stmt = select(model_cls)
            conditions = []
            for key in match_keys:
                conditions.append(getattr(model_cls, key) == data[key])
            
            stmt = stmt.where(and_(*conditions))
            result = await self._session.execute(stmt)
            instance = result.scalars().first()

        if instance:
            for k, v in data.items():
                if k in match_keys:
                    continue
                    
                if hasattr(instance, k):
                    col_type = getattr(model_cls, k).type
                    
                    if hasattr(col_type, "python_type") and issubclass(col_type.python_type, enum.Enum):
                        if isinstance(v, str):
                             try:
                                 v = col_type.python_type(v)
                             except ValueError:
                                 pass
                                 
                    setattr(instance, k, v)
        else:
            valid_data = {}
            for k, v in data.items():
                if hasattr(model_cls, k):
                    col_type = getattr(model_cls, k).type
                    if hasattr(col_type, "python_type") and issubclass(col_type.python_type, enum.Enum):
                        if isinstance(v, str):
                             try:
                                 v = col_type.python_type(v)
                             except ValueError:
                                 pass
                    valid_data[k] = v
                    
            instance = model_cls(**valid_data)
            self._session.add(instance)
        
        await self._session.flush()
        await self._session.refresh(instance)
        
        return _to_dict(instance)
