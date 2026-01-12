"""
数据桥接模块
提供通用的、基于字典的数据访问接口，屏蔽ORM细节。
"""
import uuid
import datetime
import enum
from typing import Any, Dict, List, Optional, Type, Union
from contextlib import asynccontextmanager
from contextvars import ContextVar
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

# ============ 全局数据修改队列管理 ============

class _UpdateRecord:
    """单条修改记录"""
    def __init__(self, model_name: str, data: Dict[str, Any], match_keys: List[str] = None):
        self.model_name = model_name
        self.data = data
        self.match_keys = match_keys or ["id"]


class _UpdateQueue:
    """异步上下文中的数据修改队列"""
    def __init__(self):
        self.records: List[_UpdateRecord] = []
    
    def add(self, model_name: str, data: Dict[str, Any], match_keys: List[str] = None):
        """添加一条修改记录"""
        self.records.append(_UpdateRecord(model_name, data, match_keys))
    
    def clear(self):
        """清空队列"""
        self.records.clear()
    
    def is_empty(self) -> bool:
        """检查队列是否为空"""
        return len(self.records) == 0
    
    def get_merged_records(self) -> List[_UpdateRecord]:
        """
        获取合并后的修改记录
        
        将同一实体的多个修改自动合并，避免后续修改覆盖前面的修改。
        对于字典类型的字段，进行深度合并。
        
        返回：合并后的记录列表
        """
        # 按 (model_name, match_keys, key_values) 分组
        merge_map: Dict[tuple, _UpdateRecord] = {}
        
        for record in self.records:
            # 生成分组键：(模型名, 匹配键列表, 匹配键值)
            key_values = tuple(record.data.get(k) for k in record.match_keys)
            group_key = (record.model_name, tuple(record.match_keys), key_values)
            
            if group_key not in merge_map:
                # 第一次见到这个实体，直接加入
                merge_map[group_key] = _UpdateRecord(
                    record.model_name,
                    record.data.copy(),
                    record.match_keys
                )
            else:
                # 已经见过这个实体，进行深度合并
                existing = merge_map[group_key]
                existing.data = _deep_merge_dicts(existing.data, record.data)
        
        return list(merge_map.values())


# 为每个异步任务维护独立的数据修改队列
_update_queue: ContextVar[Optional[_UpdateQueue]] = ContextVar('update_queue', default=None)


def _get_or_create_queue() -> _UpdateQueue:
    """获取或创建当前异步上下文的修改队列"""
    queue = _update_queue.get()
    if queue is None:
        queue = _UpdateQueue()
        _update_queue.set(queue)
    return queue


def _get_queue() -> Optional[_UpdateQueue]:
    """获取当前异步上下文的修改队列（可能为 None）"""
    return _update_queue.get()


def _deep_merge_dicts(base: Dict[str, Any], updates: Dict[str, Any]) -> Dict[str, Any]:
    """
    深度合并两个字典，updates 中的值会覆盖 base 中的对应值。
    对于嵌套的字典，递归进行合并而不是直接覆盖。
    
    示例：
      base = {"stats": {"hp": 100, "mp": 50}, "name": "Player"}
      updates = {"stats": {"hp": 80}, "level": 5}
      result = {"stats": {"hp": 80, "mp": 50}, "name": "Player", "level": 5}
    """
    result = base.copy()
    
    for key, value in updates.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # 递归合并嵌套字典
            result[key] = _deep_merge_dicts(result[key], value)
        else:
            # 直接覆盖或新增
            result[key] = value
    
    return result


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
async def read(
    target: Union[str, Type[models.Base]], 
    filters: Dict[str, Any] = None, 
    one: bool = True,
    session: AsyncSession = None
) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
    """
    简化的读取接口，直接从数据库获取数据
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


def update_queue(
    target: Union[str, Type[models.Base]], 
    data: Dict[str, Any], 
    match_keys: List[str] = None
) -> None:
    """
    将待修改的数据追加到全局队列中，等待后续提交
    target: 模型类或模型名称字符串
    data: 要保存的数据字典
    match_keys: 用于匹配现有记录的字段列表。如果为 None，默认使用 'id'
    """
    queue = _get_or_create_queue()
    
    # 解析模型名称（支持字符串或类）
    if isinstance(target, str):
        model_name = target
    else:
        model_name = target.__name__
    
    queue.add(model_name, data, match_keys)


@transactional
async def commit(session: AsyncSession = None) -> int:
    """一次性提交全局修改队列中的所有数据到数据库"""
    queue = _get_queue()
    
    if queue is None or queue.is_empty():
        return 0
    
    # 获取合并后的记录
    merged_records = queue.get_merged_records()
    record_count = 0
    
    try:
        # 处理合并后的每条记录
        for record in merged_records:
            model_cls = MODEL_MAP.get(record.model_name)
            if not model_cls:
                raise ValueError(f"Unknown model name: {record.model_name}")
            
            await _save_to_db(model_cls, record.data, record.match_keys, session)
            record_count += 1
        
        # 全部成功后清空队列
        queue.clear()
        return record_count
    
    except Exception as e:
        # 失败时保留队列，以便调试或重试
        raise RuntimeError(f"Failed to commit {record_count} records: {e}")


async def _save_to_db(
    model_cls: Type[models.Base],
    data: Dict[str, Any],
    match_keys: List[str],
    session: AsyncSession
) -> None:
    """
    内部函数：将单条数据保存到数据库（创建或更新）
    """
    if match_keys is None:
        match_keys = ["id"]

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
        # 创建新记录
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
        session.add(instance)
    
    # 刷新以获取默认值和ID
    await session.flush()
    await session.refresh(instance)

# ============ 向后兼容性：旧接口别名 ============

async def fetch_model_data(
    target: Union[str, Type[models.Base]], 
    filters: Dict[str, Any] = None, 
    one: bool = True,
    session: AsyncSession = None
) -> Union[Dict[str, Any], List[Dict[str, Any]], None]:
    """
    [已弃用] 向后兼容的旧接口，请使用 read() 替代
    """
    return await read(target, filters, one, session)


async def save_model_data(
    target: Union[str, Type[models.Base]], 
    data: Dict[str, Any], 
    match_keys: List[str] = None,
    session: AsyncSession = None
) -> Dict[str, Any]:
    """
    [已弃用] 向后兼容的旧接口
    
    请改为使用：
      data_dict = await read(target, ...)  # 读取
      # ... 本地修改 data_dict ...
      update_queue(target, data_dict)      # 暂存
      await commit()                        # 提交
    """
    # 为了向后兼容，这个函数直接执行单条修改
    # 但新代码应该使用 update_queue() + commit() 模式
    
    @transactional
    async def _save_single(session: AsyncSession = None):
        model_cls = MODEL_MAP.get(target) if isinstance(target, str) else target
        if isinstance(target, str) and not model_cls:
            raise ValueError(f"Unknown model name: {target}")
        
        await _save_to_db(model_cls, data, match_keys, session)
        return data
    
    return await _save_single()


# ============ 事务上下文管理（仅供需要的场景使用） ============

@asynccontextmanager
async def transaction_context():
    """
    [可选] 事务上下文管理器
    
    大多数情况下应该使用 read() + update_queue() + commit() 模式
    
    这个 context manager 仅供以下场景使用：
    - 需要多个异步子任务在同一事务中共享修改队列
    - 或需要手动控制事务边界
    
    使用示例：
        async with transaction_context() as tx:
            entity = await read("Entity", {"name": "Player"})
            entity["stats"]["hp"] -= 10
            update_queue("Entity", entity)
            await commit()  # 在 context 内提交
    """
    # 保存旧队列
    old_queue = _update_queue.get()
    # 创建新队列
    new_queue = _UpdateQueue()
    _update_queue.set(new_queue)
    
    async with get_session() as session:
        try:
            yield
            # 自动提交（使用合并后的记录）
            merged_records = new_queue.get_merged_records()
            async with session.begin():
                for record in merged_records:
                    model_cls = MODEL_MAP.get(record.model_name)
                    if not model_cls:
                        raise ValueError(f"Unknown model name: {record.model_name}")
                    await _save_to_db(model_cls, record.data, record.match_keys, session)
        except Exception:
            await session.rollback()
            raise
        finally:
            # 恢复旧队列
            _update_queue.set(old_queue)
