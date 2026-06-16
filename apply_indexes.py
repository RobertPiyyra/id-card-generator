"""
One-time script to add missing indexes to the existing database.

Run with: python apply_indexes.py

This adds indexes on frequently-queried columns that were missing from the
original schema. The indexes are created with IF NOT EXISTS so the script
is safe to run multiple times.
"""
import os
import sqlite3

# Determine the database path
db_path = os.path.join(os.path.dirname(__file__), "instance", "school.db")
if not os.path.exists(db_path):
    db_path = os.path.join(os.path.dirname(__file__), "school.db")
if not os.path.exists(db_path):
    db_path = os.environ.get("DATABASE_URL", "").replace("sqlite:///", "")
if not os.path.exists(db_path):
    print("ERROR: Could not find the database file.")
    print("Set DATABASE_URL or ensure instance/school.db exists.")
    exit(1)

print(f"Applying indexes to: {db_path}")

conn = sqlite3.connect(db_path)
cursor = conn.cursor()

indexes = [
    # (index_name, table_name, columns)
    ("ix_students_email", "students", "email"),
    ("ix_students_school_name", "students", "school_name"),
    ("ix_students_template_id", "students", "template_id"),
    ("ix_templates_school_name", "templates", "school_name"),
    ("ix_activity_logs_timestamp", "activity_logs", "timestamp"),
    ("ix_admin_users_school_name", "admin_users", "school_name"),
]

applied = 0
skipped = 0

for idx_name, table, columns in indexes:
    try:
        cursor.execute(f"CREATE INDEX IF NOT EXISTS {idx_name} ON {table} ({columns})")
        if cursor.rowcount >= 0:
            print(f"  OK: {idx_name} on {table}({columns})")
            applied += 1
    except sqlite3.OperationalError as e:
        if "already exists" in str(e):
            print(f"  SKIP: {idx_name} already exists")
            skipped += 1
        else:
            print(f"  ERROR: {idx_name}: {e}")

conn.commit()

# Verify
cursor.execute("SELECT name FROM sqlite_master WHERE type='index' AND name LIKE 'ix_%'")
all_indexes = [row[0] for row in cursor.fetchall()]
print(f"\nTotal custom indexes: {len(all_indexes)}")
for idx in sorted(all_indexes):
    print(f"  - {idx}")

conn.close()
print(f"\nDone. Applied: {applied}, Skipped: {skipped}")
