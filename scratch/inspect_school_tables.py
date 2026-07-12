import sqlite3
import os

path = 'instance/school.db'
if os.path.exists(path):
    print(f"\n--- Scanning all tables in {path} ---")
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [t[0] for t in cursor.fetchall()]
    
    for table in tables:
        try:
            cursor.execute(f"SELECT COUNT(*) FROM [{table}]")
            count = cursor.fetchone()[0]
            print(f"Table '{table}': {count} records")
        except Exception as e:
            print(f"Error reading '{table}': {e}")
    conn.close()
