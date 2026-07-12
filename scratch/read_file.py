import sys
import os

if len(sys.argv) < 2:
    print("Usage: python read_file.py <path>")
    sys.exit(1)

path = sys.argv[1]
if not os.path.exists(path):
    print(f"File not found: {path}")
    sys.exit(1)

with open(path, 'r', encoding='utf-8', errors='ignore') as f:
    print(f.read(), flush=True)
