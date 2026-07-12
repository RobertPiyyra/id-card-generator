import os

for root, dirs, files in os.walk('.'):
    # Skip venv and git
    if '.git' in root or 'venv' in root or 'node_modules' in root:
        continue
    for file in files:
        filepath = os.path.join(root, file)
        try:
            with open(filepath, 'rb') as f:
                header = f.read(16)
                if b'SQLite format 3' in header:
                    print(f"SQLite DB found: {filepath} (size: {os.path.getsize(filepath)} bytes)")
        except Exception:
            pass
