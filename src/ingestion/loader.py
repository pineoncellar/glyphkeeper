"""
数据摄入模块
处理文件上传、文本提取和 LightRAG 插入
"""
import os
from pathlib import Path
from typing import Optional, List, Union

from ..core import get_logger
from ..memory.RAG_engine import get_rag_engine
from .pdf_parser import extract_text_from_pdf

logger = get_logger(__name__)


async def ingest_file(
    file_path: Union[str, Path],
    environment: str = "development"
) -> bool:
    """摄入单个文件到 LightRAG 知识库"""
    file_path = Path(file_path)
    
    if not file_path.exists():
        logger.error(f"文件不存在: {file_path}")
        return False
    
    # 根据文件类型提取文本
    suffix = file_path.suffix.lower()
    
    try:
        if suffix in [".txt", ".md"]:
            text_content = await _read_text_file(file_path)
        elif suffix == ".pdf":
            text_content = await extract_text_from_pdf(file_path)
        else:
            logger.warning(f"不支持的文件格式: {suffix}")
            return False
        
        if not text_content or not text_content.strip():
            logger.warning(f"文件内容为空: {file_path}")
            return False
        
        # 获取 RAG 引擎并插入
        engine = await get_rag_engine()
        success = await engine.insert(text_content)
        
        if success:
            logger.info(f"成功摄入文件: {file_path.name}")
        
        return success
        
    except Exception as e:
        logger.error(f"摄入文件失败 [{file_path}]: {e}")
        return False


async def ingest_text(
    text: str,
    environment: str = "development"
) -> bool:
    """直接摄入文本内容"""
    if not text or not text.strip():
        logger.warning("文本内容为空")
        return False
    
    try:
        # 适配新版 API: 移除 environment 参数
        engine = await get_rag_engine()
        return await engine.insert(text)
    except Exception as e:
        logger.error(f"摄入文本失败: {e}")
        return False


async def ingest_batch(
    contents: List[str],
    environment: str = "development"
) -> int:
    """批量摄入文本内容"""
    if not contents:
        return 0
    
    try:
        engine = await get_rag_engine(environment=environment)
        return await engine.insert_batch(contents)
    except Exception as e:
        logger.error(f"批量摄入失败: {e}")
        return 0


async def ingest_directory(
    directory_path: Union[str, Path],
    pattern: str = "**/*.txt",
    environment: str = "development"
) -> dict:
    """摄入目录下的所有匹配文件"""
    directory_path = Path(directory_path)
    
    if not directory_path.is_dir():
        logger.error(f"目录不存在: {directory_path}")
        return {"success": 0, "failed": 0, "skipped": 0}
    
    files = list(directory_path.glob(pattern))
    logger.info(f"发现 {len(files)} 个文件待摄入")
    
    stats = {"success": 0, "failed": 0, "skipped": 0}
    
    for file_path in files:
        if file_path.is_file():
            success = await ingest_file(file_path, environment)
            if success:
                stats["success"] += 1
            else:
                stats["failed"] += 1
        else:
            stats["skipped"] += 1
    
    logger.info(
        f"目录摄入完成: 成功={stats['success']}, "
        f"失败={stats['failed']}, 跳过={stats['skipped']}"
    )
    
    return stats


# ============================================
# 辅助函数
# ============================================

async def _read_text_file(file_path: Path) -> str:
    """读取文本文件"""
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            return f.read()
    except UnicodeDecodeError:
        # 尝试其他编码
        with open(file_path, "r", encoding="gbk") as f:
            return f.read()

class ContentDeduplicator:
    """内容去重器
    用于避免重复插入相同的文档"""
    
    def __init__(self):
        self._seen_hashes: set = set()
    
    def is_duplicate(self, content: str) -> bool:
        """检查内容是否重复"""
        content_hash = hash(content.strip())
        if content_hash in self._seen_hashes:
            return True
        self._seen_hashes.add(content_hash)
        return False
    
    def clear(self):
        """清除记录"""
        self._seen_hashes.clear()


# 全局去重器实例
_deduplicator = ContentDeduplicator()


async def ingest_with_dedup(
    content: str,
    environment: str = "development"
) -> bool:
    """带去重的文本摄入"""
    if _deduplicator.is_duplicate(content):
        logger.debug("检测到重复内容，跳过插入")
        return True
    
    return await ingest_text(content, environment)
