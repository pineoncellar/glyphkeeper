import tarfile
import json
import sys
import os

def inspect_backup(file_path):
    if not os.path.exists(file_path):
        print(f"File not found: {file_path}")
        return

    print(f"Inspecting backup: {file_path}")
    
    with tarfile.open(file_path, "r:gz") as tar:
        for member in tar.getmembers():
            if member.isfile() and member.name.endswith('.jsonl'):
                f = tar.extractfile(member)
                if f:
                    content = f.read().decode('utf-8')
                    lines = content.strip().split('\n')
                    header = lines[0] if lines else "EMPTY"
                    row_count = len(lines) - 1 # exclude header
                    
                    print(f"File: {member.name}")
                    print(f"  Size: {member.size} bytes")
                    print(f"  Rows: {max(0, row_count)}")
                    # print(f"  Header: {header[:100]}...")
                    if row_count > 0:
                        print(f"  Sample Row 1: {lines[1][:100]}...")
                    print("-" * 20)

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python inspect_backup.py <backup_file>")
        sys.exit(1)
    
    inspect_backup(sys.argv[1])