"""
Ingestion 模块
数据摄入和处理
"""
from .loader import (
    ingest_file,
    ingest_text,
    ingest_batch,
    ingest_directory,
    ingest_with_dedup,
    ContentDeduplicator,
)

__all__ = [
    "ingest_file",
    "ingest_text",
    "ingest_batch",
    "ingest_directory",
    "ingest_with_dedup",
    "ContentDeduplicator",
]
