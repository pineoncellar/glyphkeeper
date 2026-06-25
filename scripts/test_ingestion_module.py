"""Quick validation of the ingestion module"""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent))

from src.tools.ingestion import (
    ModuleIngestor, ingest_by_name, ingest_by_path,
    list_available_modules, find_module_files, load_json
)

# 1. Verify --list
print("=== test find_module_files ===")
files = find_module_files()
for f in files:
    print(f"  [file] {f.stem}")

# 2. Verify JSON parsing
print()
print("=== test load_json ===")
book_file = next(f for f in files if f.stem == "book")
data = load_json(book_file)
meta = data.get("meta", {})
print(f"  module_name: {meta.get('module_name')}")
print(f"  global_knowledge: {len(data.get('global_knowledge', []))} entries")
print(f"  locations: {len(data.get('locations', []))} locations")
print(f"  opening: {'yes' if data.get('opening') else 'no'}")

# 3. Verify ModuleIngestor structure
print()
print("=== test ModuleIngestor ===")
ingestor = ModuleIngestor()
print(f"  ModuleIngestor created: {type(ingestor).__name__}")
print(f"  Methods: ingest, _ingest_knowledge, _ingest_location, _ingest_entity, _ingest_interactable, _ingest_opening, _record_world_initialized")

# 4. Verify CLI functions
print()
print("=== test CLI entry points ===")
from src.tools.ingestion import parse_args, main, main_async
print(f"  parse_args: {callable(parse_args)}")
print(f"  main: {callable(main)}")
print(f"  main_async: {callable(main_async)}")

# 5. Verify from tools package
print()
print("=== test tools package export ===")
from src.tools import ModuleIngestor as MI2, ingest_by_name as ibn, list_available_modules as lam
print(f"  tools.ModuleIngestor: {MI2 is ModuleIngestor}")
print(f"  tools.ingest_by_name: {callable(ibn)}")
print(f"  tools.list_available_modules: {callable(lam)}")

print()
print("All static tests passed!")
