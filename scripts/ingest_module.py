import asyncio
import sys
import os
import argparse
from pathlib import Path

# 添加项目根目录到 python path
sys.path.append(str(Path(__file__).parent.parent))

from src.core import get_logger
from src.ingestion.loader import load_module_from_json

logger = get_logger(__name__)

async def main():
    parser = argparse.ArgumentParser(description="Ingest module from JSON file.")
    parser.add_argument(
        "--name",
        type=str,
        help="要摄入的JSON文件路径"
    )
    args = parser.parse_args()
    book_json_path = Path(f"data/intermediate/{args.name}.json")
    if not book_json_path.exists():
        logger.error(f"找不到文件: {book_json_path}")
        return

    logger.info(f"开始摄入: {book_json_path}")
    success = await load_module_from_json(book_json_path)
    
    if success:
        logger.info("摄入成功！")
    else:
        logger.error("摄入失败。")

if __name__ == "__main__":
    asyncio.run(main())
