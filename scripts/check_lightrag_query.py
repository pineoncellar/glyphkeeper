#!/usr/bin/env python3
"""验证 LightRAG 向量查询"""
import asyncio, json
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

async def main():
    from src.memory.vector_store import VectorStore
    vs = await VectorStore.get_instance(domain='world', llm_tier='fast', force_reinit=False)
    
    print('=== LightRAG 查询测试 ===')
    
    r = await vs.query('宅邸大门 铁门 入口', mode='naive', top_k=5)
    ok = 'no-context' not in r
    print(f'[naive] 宅邸大门: {"OK" if ok else "FAIL"}')
    if ok:
        print(f'  结果前100字: {r[:100]}')
    
    r = await vs.query('老管家 秘密 酒窖', mode='hybrid', top_k=5)
    ok = 'no-context' not in r
    print(f'[hybrid] 老管家: {"OK" if ok else "FAIL"}')
    if ok:
        print(f'  结果前100字: {r[:100]}')
    
    r = await vs.query('hidden letter 书信 夹层', mode='local', top_k=5)
    ok = 'no-context' not in r
    print(f'[local] hidden letter: {"OK" if ok else "FAIL"}')
    if ok:
        print(f'  结果前100字: {r[:100]}')
    
    # 检查向量数据文件
    book_dir = Path('data/worlds/book')
    vdb_files = list(book_dir.glob('vdb_*.json'))
    for f in sorted(vdb_files):
        data = json.loads(f.read_text(encoding='utf-8'))
        count = len(data) if isinstance(data, list) else len(data.keys())
        print(f'  {f.name}: {count} items')

asyncio.run(main())
