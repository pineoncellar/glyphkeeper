import sys
import os
import argparse
import asyncio
from pathlib import Path
from datetime import datetime

# Add project root to path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from src.utils.world_manager import WorldBackupRestore
from src.core.config import PROJECT_ROOT

async def perform_backup(world_name, output_file, remark=None):
    """Delegate backup to WorldBackupRestore"""
    wbr = WorldBackupRestore()
    
    # WorldBackupRestore expects output_file (if provided) to be path or str
    result = await wbr.backup_world(world_name, output_file, remark)
    
    if result:
        print("Backup completed successfully!")
    else:
        print("Backup failed.")

async def perform_list(filter_world=None):
    """Delegate list to WorldBackupRestore and format output"""
    wbr = WorldBackupRestore()
    backups = await wbr.list_backups(world_filter=filter_world)
    
    if not backups:
        print(f"No backups found{' for world: ' + filter_world if filter_world else ''}.")
        return

    print(f"Scanning {len(backups)} backup files...")
    
    print(f"\n{'Filename':<40} | {'World':<15} | {'Date':<20} | {'Remark'}")
    print("-" * 110)
    for b in backups:
        date_str = b.get('timestamp') 
        if not date_str:
            # readable fallback from ctime
            val = b.get('created', 0)
            if isinstance(val, (int, float)):
                dt = datetime.fromtimestamp(val)
            else:
                dt = val
            date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
        else:
            # Try to format the timestamp from metadata (usually YYYYMMDD_HHMMSS)
            try:
                dt = datetime.strptime(date_str, "%Y%m%d_%H%M%S")
                date_str = dt.strftime("%Y-%m-%d %H:%M:%S")
            except ValueError:
                pass # keep as is
        
        remark = b.get('remark', '')
        world = b.get('world', 'Unknown')
        print(f"{b['file']:<40} | {world:<15} | {date_str:<20} | {remark}")
    print("-" * 110)

async def perform_restore(world_name, input_file):
    """Delegate restore to WorldBackupRestore"""
    wbr = WorldBackupRestore()
    
    # WorldBackupRestore checks logic for path existence. 
    # Whatever passes here (filename or path) is handled by it.
    
    success = await wbr.restore_world(world_name, input_file, overwrite=True)
    
    if success:
        print("Restore completed successfully!")
    else:
        print("Restore failed.")


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
