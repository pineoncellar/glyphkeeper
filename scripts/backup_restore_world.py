import sys
import os
import shutil
import asyncio
import tarfile
import json
import csv
import io
import argparse
import uuid
from pathlib import Path
from datetime import datetime
from sqlalchemy import text, inspect
from sqlalchemy.ext.asyncio import AsyncSession

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.core.config import get_settings, PROJECT_ROOT
from src.memory.database import DatabaseManager, Base
# Import models to ensure they are registered in Base.metadata for left-brain restoration
from src.memory import models

async def get_public_tables_with_workspace(conn):
    """Find tables in public schema that have a 'workspace' column."""
    query = text("""
        SELECT table_name 
        FROM information_schema.columns 
        WHERE table_schema = 'public' AND column_name = 'workspace';
    """)
    result = await conn.execute(query)
    return [row[0] for row in result.fetchall()]

async def backup_table_to_csv(conn, schema, table, workspace_filter, output_path):
    """Backup a single table to CSV."""
    if workspace_filter:
        query = text(f'SELECT * FROM "{schema}"."{table}" WHERE workspace = :workspace')
        result = await conn.stream(query, parameters={"workspace": workspace_filter})
    else:
        query = text(f'SELECT * FROM "{schema}"."{table}"')
        result = await conn.stream(query)

    # Get column names
    # We can fetch one batch to get keys/cursor description if using stream
    # But stream returns Result object.
    
    # Alternative: use copy command if possible (mostly for psql/expert users), 
    # but here we stick to python logic for portability.
    
    # We need column headers.
    # Let's execute to get columns first or use inspect? 
    # Result object has .keys()
    
    # Re-execute to get columns (stream allows iterating)
    if workspace_filter:
        result = await conn.execute(query, parameters={"workspace": workspace_filter})
    else:
        result = await conn.execute(query)
        
    keys = result.keys()
    
    with open(output_path, 'w', newline='', encoding='utf-8') as csvfile:
        writer = csv.writer(csvfile)
        writer.writerow(keys)
        
        # Write rows
        # For large tables this should be chunked, but for now we iterate
        for row in result:
             # Handle special types if necessary (e.g. JSON/Array to string)
             # csv writer handles basic types. DB output might need stringify for complex types.
             writer.writerow([str(x) if isinstance(x, (dict, list)) else x for x in row])

async def restore_table_from_csv(conn, schema, table, csv_path):
    """Restore a table from CSV."""
    if not os.path.exists(csv_path):
        print(f"Warning: Backup for table {schema}.{table} not found at {csv_path}")
        return

    # Use pandas if available? Or pure python.
    # We need to handle data types. 
    # Simplest way is let DB handle it via simple INSERTs or COPY.
    # But CSV contains string representations. 
    # Complex types (JSON, Array) might break if just quoted.
    
    # Let's try to use SQLAlchemy bind params to let it handle types.
    # We read keys from CSV header.
    
    with open(csv_path, 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        rows = list(reader) # Read all into memory (careful with large data)
        
        if not rows:
            return

        # Prepare insert statement
        # We assume columns in CSV match columns in Table.
        # Note: CSV values are all strings!
        # This is the tricky part. 'None' string vs Null.
        # And JSON fields. 
        
        # To make this robust without pg_dump, we should use python serialization (pickle or json) instead of CSV
        # JSON is better.
        pass

async def backup_table_to_json(conn, schema, table, workspace_filter, output_path):
    """Backup a single table to JSON Lines (more type safe-ish)."""
    if workspace_filter:
        query = text(f'SELECT * FROM "{schema}"."{table}" WHERE workspace = :workspace')
        result = await conn.execute(query, parameters={"workspace": workspace_filter})
    else:
        query = text(f'SELECT * FROM "{schema}"."{table}"')
        result = await conn.execute(query)

    keys = list(result.keys())
    
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write(json.dumps({"columns": keys}) + "\n") # Checkpoint header
        for row in result:
            # Convert row to dict, serialize JSON/Date
            row_dict = {}
            for idx, val in enumerate(row):
                if isinstance(val, datetime):
                    val = val.isoformat()
                elif isinstance(val, uuid.UUID):
                    val = str(val)
                row_dict[keys[idx]] = val
            f.write(json.dumps(row_dict, default=str) + "\n")

async def restore_table_from_json(conn, schema, table, json_path):
    """Restore table from JSON Lines."""
    if not os.path.exists(json_path):
        return 0

    # Inspect table columns to handle type conversions (especially datetime/uuid for asyncpg)
    def get_col_types(sync_conn):
        inspector = inspect(sync_conn)
        return inspector.get_columns(table, schema=schema)
    
    try:
        columns_info = await conn.run_sync(get_col_types)
    except Exception as e:
        print(f"  Warning: Could not inspect table {schema}.{table}: {e}")
        columns_info = []

    converters = {}
    for col in columns_info:
        col_name = col['name']
        col_type = str(col['type']).upper()
        # Check nullable. Inspector returns 'nullable' key usually boolean.
        # But SQLAlchemy's get_columns result keys may vary slightly by dialect, usually 'nullable' is reliable.
        # Default to True if missing to be safe (allow NULLs) unless we want to force defaults.
        is_nullable = col.get('nullable', True)
        
        # Check for datetime types safely
        is_datetime = False
        try:
            if hasattr(col['type'], 'python_type') and col['type'].python_type is datetime:
                is_datetime = True
        except (NotImplementedError, AttributeError):
            pass

        if is_datetime or 'TIMESTAMP' in col_type or 'DATETIME' in col_type:
             converters[col_name] = lambda x: datetime.fromisoformat(x) if x else None
        elif 'UUID' in col_type:
             converters[col_name] = lambda x: uuid.UUID(x) if x else None
        elif 'JSON' in col_type or 'JSONB' in col_type:
             # asyncpg requires JSONB fields to be passed as JSON strings, not dicts
             # If value is None but column is NOT NULL, return empty json object '{}' to satisfy constraint
             
             # Known JSON columns that must not be null in current schema
             # We force these to fallback to '{}' even if inspector says nullable, 
             # because the model definition (Mapped[dict]) implies NOT NULL strictness.
             known_not_null_json = [
                 'required_check', 'trigger_condition', 'effect_script', 
                 'stats', 'attacks', 'backstory', 'exits'
             ]
             force_not_null = col_name in known_not_null_json
             
             print(f"DEBUG: Configured JSON converter for {col_name} (Type: {col_type}, Nullable: {is_nullable}, ForceNotNull: {force_not_null})")

             converters[col_name] = lambda x, nullable=is_nullable, force=force_not_null: (
                 json.dumps(x, default=str) if isinstance(x, (dict, list)) 
                 else (json.dumps(x) if x is not None else ('{}' if (not nullable or force) else None))
             )

    total_rows = 0
    with open(json_path, 'r', encoding='utf-8') as f:
        # Read header only once
        first_line = f.readline()
        if not first_line:
            return 0
            
        header = json.loads(first_line)
        columns = header['columns']
        
        chunk = []
        BATCH_SIZE = 1000
        
        # Build Insert Statement
        # INSERT INTO schema.table (col1, col2) VALUES (:col1, :col2)
        cols_str = ", ".join([f'"{c}"' for c in columns])
        vals_str = ", ".join([f':{c}' for c in columns])
        stmt = text(f'INSERT INTO "{schema}"."{table}" ({cols_str}) VALUES ({vals_str})')
        
        for line in f:
            line = line.strip()
            if not line:
                continue
                
            data = json.loads(line)
            
            # Convert types
            for k, v in data.items():
                if k in converters:
                    try:
                        # 对于 JSON/JSONB 字段，即使是 dict/list，我们也需要再次序列化为字符串
                        # 因为在 backup_table_to_json 中，它们被反序列化为了对象写入文件的
                        # 而读取时 json.loads 将它们读回了对象
                        # asyncpg 需要 JSON 字符串
                        data[k] = converters[k](v)
                    except (ValueError, TypeError) as e:
                        # If conversion fails, implicit strictness might fail later, but we let it pass here
                        pass
                
                # Special handler for JSON fields that might be null/missing but required by DB
                # If a field is JSON but value is None (and column might be NOT NULL), we can't fix it easily without schema knowledge.
                # However, if 'required_check' is missing or None and it's JSON, maybe default to empty dict?
                # But here we only convert if k is in converters AND value is present.
                
                # Check for explicit None in JSON fields if the column is NOT NULL is hard dynamically.
                # But 'required_check' column in error is failing NotNull check.
                # The value in error log is 'null'.
                pass

            # Pre-processing fix for known issues or NotNull constraints if JSON is missing
            # If the source JSON file had explicit null, we might need to supply default.
            # But restoring EXACTLY what was backed up is the goal.
            # Failure means the backup has null, but schema says NOT NULL.
            # This implies schema drift OR the backup data was invalid? 
            # Or the 'JSON' converter wasn't applied on None, so it stayed None?
            
            # If 'required_check' is in converters (it is JSONB), and data[k] is None.
            # If we want to force empty dict:
            # keys_to_fix = [k for k, val in data.items() if val is None and k in converters and 'required_check' in k]
            # for k in keys_to_fix: data[k] = '{}' 
            
            # Let's inspect the error: "null value in column ... violates not-null"
            # It seems the data in JSONL has null for 'required_check'.

            chunk.append(data)
            if len(chunk) >= BATCH_SIZE:
                await conn.execute(stmt, chunk)
                total_rows += len(chunk)
                chunk = []
        
        if chunk:
            await conn.execute(stmt, chunk)
            total_rows += len(chunk)
            
    return total_rows

async def perform_backup(world_name, output_file, remark=None):
    print(f"Starting backup for world: {world_name}")
    
    # Prepare temp dir
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_dir = PROJECT_ROOT / "tmp" / f"backup_{world_name}_{timestamp}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    
    struct_dir = temp_dir / "structured"
    struct_dir.mkdir()
    
    unstruct_dir = temp_dir / "unstructured"
    unstruct_dir.mkdir()
    
    graph_dir = temp_dir / "graph"
    graph_dir.mkdir()

    db_manager = DatabaseManager()
    
    async with db_manager.engine.begin() as conn:
        # 1. Backup Left Brain (Structured) - schema: world_<world_name>
        schema = f"world_{world_name}"
        
        # Check if schema exists
        check_schema = await conn.execute(text(f"SELECT schema_name FROM information_schema.schemata WHERE schema_name = '{schema}'"))
        if not check_schema.scalar():
            print(f"Schema {schema} does not exist!")
            # Should we continue? Maybe only backup right brain? 
            # Assume fail for now.
            return 
        
        # Get all tables in schema
        tables_query = await conn.execute(text(f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema}'"))
        tables = [row[0] for row in tables_query]
        
        print(f"Backing up {len(tables)} tables from schema {schema}...")
        for table in tables:
            print(f"  Exporting {table}...")
            await backup_table_to_json(conn, schema, table, None, struct_dir / f"{table}.jsonl")
            
        # 2. Backup Right Brain (Unstructured) - schema: public
        public_tables = await get_public_tables_with_workspace(conn)
        print(f"Backing up {len(public_tables)} tables from public schema (workspace={world_name})...")
        for table in public_tables:
            print(f"  Exporting {table}...")
            await backup_table_to_json(conn, "public", table, world_name, unstruct_dir / f"{table}.jsonl")

    # 3. Backup Graph Files
    world_src_dir = PROJECT_ROOT / "data" / "worlds" / world_name
    if world_src_dir.exists():
        print(f"Backing up graph files from {world_src_dir}...")
        for file in world_src_dir.glob("*"):
            if file.is_file():
                shutil.copy2(file, graph_dir)
            
    # 4. Create Archive
    if not output_file:
        output_file = PROJECT_ROOT / "data" / "backups" / f"{world_name}_{timestamp}.zip"
    else:
        output_file = Path(output_file)
        
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    # Add metadata
    meta = {
        "world": world_name,
        "timestamp": timestamp,
        "remark": remark or ""
    }
    with open(temp_dir / "metadata.json", "w", encoding='utf-8') as f:
        json.dump(meta, f)

    print(f"Creating archive: {output_file}")
    with tarfile.open(output_file, "w:gz") as tar:
        tar.add(temp_dir, arcname=world_name)
        
    # Cleanup
    shutil.rmtree(temp_dir)
    print("Backup completed successfully!")

async def perform_list(filter_world=None):
    backup_dir = PROJECT_ROOT / "data" / "backups"
    if not backup_dir.exists():
        print(f"No backups folder found at {backup_dir}")
        return
    
    backups = []
    # Support both .zip and .tar.gz extensions if needed, currently script uses .zip for tar.gz content
    files = list(backup_dir.glob("*.zip")) + list(backup_dir.glob("*.tar.gz"))
    
    print(f"Scanning {len(files)} backup files...")
    
    for file_path in files:
        try:
            info = {
                "file": file_path.name, 
                "created": datetime.fromtimestamp(file_path.stat().st_mtime), 
                "size": file_path.stat().st_size,
                "world": "Unknown",
                "remark": ""
            }
            
            # Try to read metadata from zip
            try:
                with tarfile.open(file_path, "r:gz") as tar:
                    # Look for metadata.json inside any folder
                    # Paths inside are like "world_name/metadata.json" or just "metadata.json"
                    meta_member = None
                    for member in tar.getmembers():
                        if member.name.endswith("metadata.json"):
                            meta_member = member
                            break
                    
                    if meta_member:
                        f = tar.extractfile(meta_member)
                        if f:
                            meta = json.load(f)
                            info.update(meta)
                            # Convert timestamp string back to object if needed for sorting, 
                            # but simple string sort key works for ISO format usually.
                    else:
                        # Legacy Parsing: world_timestamp.zip
                        parts = file_path.stem.split('_')
                        if len(parts) >= 2:
                            info['world'] = parts[0]
            except Exception as e:
                # If corrupt or not a tar
                pass
            
            if filter_world and info.get('world') != filter_world:
                continue
                
            backups.append(info)
        except Exception as e:
            print(f"Error checking {file_path.name}: {e}")
            
    # Sort by created date desc
    backups.sort(key=lambda x: x['created'], reverse=True)
    
    print(f"\n{'Filename':<40} | {'World':<15} | {'Date':<20} | {'Remark'}")
    print("-" * 110)
    for b in backups:
        date_str = b.get('timestamp') 
        if not date_str:
            date_str = b['created'].strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Format timestamp nicely if it is raw 20260107_...
             try:
                 dt = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                 date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
             except:
                 pass

        remark = b.get('remark', '')
        world = b.get('world', 'Unknown')
        print(f"{b['file']:<40} | {world:<15} | {date_str:<20} | {remark}")
    print("-" * 110)

async def perform_restore(world_name, input_file):
    print(f"Starting restore for world: {world_name} from {input_file}")
    input_path = Path("data/backups/" + input_file)
    if not input_path.exists():
        print(f"Input file not found: {input_path}")
        return

    # Extract
    temp_dir = PROJECT_ROOT / "tmp" / f"restore_{world_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    if temp_dir.exists():
        shutil.rmtree(temp_dir)
    temp_dir.mkdir(parents=True)
    
    with tarfile.open(input_path, "r:gz") as tar:
        tar.extractall(temp_dir)
        
    # The archive contains a folder named <world_name> (from arcname in backup)
    # But wait, checking if user renamed backup file or world name differs?
    # We should look for the *content* inside temp_dir.
    # We expect 'structured', 'unstructured', 'graph' dirs inside the root extracted folder.
    
    extracted_root = next(temp_dir.iterdir()) # Assuming single root folder
    if not (extracted_root / "structured").exists():
        # Maybe it was compressed without root folder?
        if (temp_dir / "structured").exists():
            extracted_root = temp_dir
    
    struct_dir = extracted_root / "structured"
    unstruct_dir = extracted_root / "unstructured"
    graph_dir = extracted_root / "graph"

    db_manager = DatabaseManager()

    async with db_manager.engine.begin() as conn:
        schema = f"world_{world_name}"
        
        # 1. Restore Graph Files
        # Do this first safely? Or last? Local files are easy.
        target_world_dir = PROJECT_ROOT / "data" / "worlds" / world_name
        target_world_dir.mkdir(parents=True, exist_ok=True)
        
        if graph_dir.exists():
            print("Restoring graph files...")
            for file in graph_dir.glob("*"):
                shutil.copy2(file, target_world_dir)
        
        # 2. Restore Left Brain
        print(f"Restoring Schema {schema}...")
        
        # Drop Schema
        # Use CASCADE to remove tables
        await conn.execute(text(f"DROP SCHEMA IF EXISTS {schema} CASCADE"))
        await conn.execute(text(f"CREATE SCHEMA {schema}"))
        
        # Recreate tables
        # We need to bind the metadata create_all to this schema.
        # But Base.metadata contains definitions for ALL schemas? 
        # No, 'Base' models don't have schema defined usually, or use dynamic schema.
        # In this project, `manage_worlds.py` sets search_path before `create_all`.
        
        await conn.execute(text(f"SET search_path TO {schema}, public"))
        
        # We need to run create_all synchronously
        await conn.run_sync(Base.metadata.create_all)
        
        # Load Data
        # We need to load in dependency order if we want to be safe, OR disable triggers.
        # PostgreSQL: SET session_replication_role = 'replica'; 
        # (Requires superuser usually).
        # Let's try loading in sorted order from metadata.
        
        sorted_tables = Base.metadata.sorted_tables
        table_names = [t.name for t in sorted_tables]
        
        # We only look for jsonl files
        json_files = list(struct_dir.glob("*.jsonl"))
        
        # Filter files that match actual tables
        files_to_load = []
        for t_name in table_names:
            f = struct_dir / f"{t_name}.jsonl"
            if f in json_files:
                files_to_load.append((t_name, f))
                
        # Also load any tables that weren't in sorted_tables (maybe pure M2M tables or dynamic ones?)
        # But Base.metadata should have them.
        
        print(f"Restoring {len(files_to_load)} tables in {schema}...")
        
        # Note: restore_summary init moved to after this block in previous edit (my mistake in tool usage order), 
        # but since 'files_to_load' loop runs BEFORE 'Right Brain' block, I can't access 'restore_summary' if it's defined later.
        # I need to fix the order.
        
        # Actually, let's just initialize it here.
        restore_summary = {}
        
        for table_name, file_path in files_to_load:
            # We must insert into schema.table
            # But search_path is already set.
            print(f"  Importing {table_name} from {file_path.name}...")
            row_count = await restore_table_from_json(conn, schema, table_name, file_path)
            print(f"    -> Restored {row_count} rows.")
            restore_summary[f"{schema}.{table_name}"] = row_count



        # 3. Restore Right Brain
        print("Restoring Public (LightRAG) tables...")
        # Get public tables to verify
        public_tables = await get_public_tables_with_workspace(conn)
        
        # restore_summary already initiated

        for file_path in unstruct_dir.glob("*.jsonl"):
            table_name = file_path.stem
            key = f"public.{table_name}"
            if table_name in public_tables:
                print(f"  Cleaning old data for {table_name} workspace={world_name}...")
                await conn.execute(text(f'DELETE FROM public."{table_name}" WHERE workspace = :w'), {"w": world_name})
                
                print(f"  Importing {table_name} from {file_path.name}...")
                row_count = await restore_table_from_json(conn, "public", table_name, file_path)
                print(f"    -> Restored {row_count} rows.")
                restore_summary[key] = row_count
            else:
                print(f"  Skipping {table_name} (not found in DB or valid public table)")
                restore_summary[key] = "Skipped (Target missing)"

    shutil.rmtree(temp_dir)
    print("\n=== Restore Summary ===")
    for table, count in restore_summary.items():
        print(f"{table}: {count} rows")
    print("=======================")
    print("Restore completed successfully!")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Backup and Restore World Data")
    parser.add_argument("action", choices=["backup", "restore", "list"], help="Action to perform: backup, restore, or list")
    
    # made world_name optional for 'list'
    parser.add_argument("world_name", nargs="?", help="Name of the world (Required for backup/restore)")
    
    # added optional remark
    parser.add_argument("remark", nargs="?", help="Optional note/remark for the backup")
    
    parser.add_argument("--file", "-f", help="Path to backup file (input for restore, output for backup)")
    
    args = parser.parse_args()
    
    if args.action == "list":
        asyncio.run(perform_list(args.world_name))
        
    elif args.action == "backup":
        if not args.world_name:
            print("Error: world_name is required for backup")
            sys.exit(1)
        asyncio.run(perform_backup(args.world_name, args.file, args.remark))
        
    elif args.action == "restore":
        if not args.world_name:
            print("Error: world_name is required for restore")
            sys.exit(1)
        if not args.file:
            print("Error: --file argument is required for restore")
            sys.exit(1)
        asyncio.run(perform_restore(args.world_name, args.file))
