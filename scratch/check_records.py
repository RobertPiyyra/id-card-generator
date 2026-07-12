import sqlite3
import os
import glob

db_paths = glob.glob('instance/*.db')

for path in db_paths:
    conn = sqlite3.connect(path)
    cursor = conn.cursor()
    cursor.execute("SELECT name FROM sqlite_master WHERE type='table';")
    tables = [t[0] for t in cursor.fetchall()]
    
    records_info = []
    for table in ['students', 'templates', 'serial_batches', 'serial_cards']:
        if table in tables:
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table}")
                count = cursor.fetchone()[0]
                if count > 0:
                    records_info.append(f"{table}: {count}")
            except Exception as e:
                pass
    if records_info:
        print(f"Database {path} has records -> {', '.join(records_info)}")
    conn.close()
