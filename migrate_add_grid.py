# DAL/migrate_add_grid.py
import sqlite3
import sys
from pathlib import Path

DB = Path("smart_timetable.db")

if not DB.exists():
    print("Database not found:", DB.resolve())
    sys.exit(1)

conn = sqlite3.connect(str(DB))
cur = conn.cursor()

# get columns for timetable_sets (if table exists)
cur.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='timetable_sets'")
if cur.fetchone() is None:
    print("timetable_sets table does not exist. Nothing to migrate.")
    conn.close()
    sys.exit(0)

cur.execute("PRAGMA table_info(timetable_sets)")
cols = [r[1] for r in cur.fetchall()]

if "grid" in cols:
    print("Column 'grid' already exists in timetable_sets. No action needed.")
else:
    print("Adding 'grid' column to timetable_sets...")
    try:
        cur.execute("ALTER TABLE timetable_sets ADD COLUMN grid TEXT DEFAULT '{}'")
        conn.commit()
        print("Added 'grid' column.")
    except Exception as e:
        print("Failed to add column:", e)
        conn.rollback()
        conn.close()
        sys.exit(1)

# Ensure no NULLs (set empty payload JSON for safety)
try:
    cur.execute("UPDATE timetable_sets SET grid = '{}' WHERE grid IS NULL")
    conn.commit()
    print("Initialized NULL grid values to '{}'.")
except Exception as e:
    print("Failed to initialize grid values:", e)
    conn.rollback()

conn.close()
print("Migration complete.")