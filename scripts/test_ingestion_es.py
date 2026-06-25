"""Test EventStore part of ingestion (no LLM needed)"""
import asyncio
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.memory.event_store import EventStore
from src.tools.ingestion import load_json, find_module_files, ModuleIngestor


async def test():
    es = EventStore()

    # 1. Test EventStore write
    evt = await es.append(
        session_id="00000000-0000-0000-0000-000000000000",
        event_type="TestEvent",
        data={"msg": "ingestion module test"},
        source_node="test",
    )
    print(f"Event written: type={evt['type']}, id={evt['id'][:8]}...")

    # 2. Test EventStore read
    events = await es.get_events("00000000-0000-0000-0000-000000000000")
    print(f"Events in template session: {len(events)}")

    # 3. Read module JSON
    files = find_module_files()
    book_file = next(f for f in files if f.stem == "book")
    data = load_json(book_file)
    print(f"Module loaded: {data['meta']['module_name']}")

    # 4. Test ingestor initialization (no VectorStore)
    ingestor = ModuleIngestor(event_store=es)
    print(f"Ingestor ready with EventStore")

    # 5. Test _ingest_opening (no VectorStore needed)
    print(f"Testing opening ingestion...")
    ok = await ingestor._ingest_opening(data["meta"]["module_name"], data["opening"])
    print(f"Opening ingested: {ok}")

    # 6. Test _record_world_initialized
    print(f"Testing WorldInitialized event...")
    await ingestor._record_world_initialized(data["meta"]["module_name"], data)

    # 7. Verify events
    events = await es.get_events("00000000-0000-0000-0000-000000000000")
    print(f"Events after ingestion: {len(events)}")
    for e in events:
        print(f"  [{e['type']}] ver={e['version']}")

    await es.close()
    print()
    print("EventStore ingestion test PASSED!")


asyncio.run(test())
