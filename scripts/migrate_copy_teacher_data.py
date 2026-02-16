
"""
Migration helper: copy teacher-owned rows from existing schema into new teacher_* tables.

- Copies rows from `courses` where teacher_id IS NOT NULL into `teacher_courses`.
- Copies rows from `constraints` where owner_type='teacher' OR owner_id IS NOT NULL into `teacher_constraints`.
- The script is idempotent: it checks for existing duplicates and will not create duplicates.
- Run this AFTER migrations/2025_create_teacher_tables.sql and after backing up your DB.

Usage:
    python scripts/migrate_copy_teacher_data.py
"""
import traceback
from pathlib import Path
import sys
root = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(root))

from config import DB_CONFIG
from DAL.db_connector import DBConnector

def exists(db, q, params=()):
    r = db.fetchone(q, params)
    return bool(r)

def main():
    db = DBConnector(DB_CONFIG)
    try:
        # 1) Copy teacher-assigned courses from courses -> teacher_courses
        # Only if teacher_courses table exists
        tc_exists = exists(db, "SELECT 1 FROM information_schema.tables WHERE TABLE_SCHEMA=%s AND TABLE_NAME='teacher_courses'", (DB_CONFIG['database'],))
        if not tc_exists:
            print("teacher_courses table does not exist. Run SQL migration first.")
        else:
            # select teacher rows from courses
            rows = db.fetchall("SELECT id, course_name, course_code, credits, section, teacher_id, published FROM courses WHERE teacher_id IS NOT NULL")
            print(f"Found {len(rows)} rows in courses with teacher_id set.")
            for r in rows:
                # check duplicate into teacher_courses by (course_code, teacher_id)
                dup = db.fetchone("SELECT id FROM teacher_courses WHERE course_code=%s AND teacher_id=%s LIMIT 1", (r['course_code'], r['teacher_id']))
                if dup:
                    continue
                db.execute(
                    "INSERT INTO teacher_courses (course_name, course_code, credits, section, teacher_id, published) VALUES (%s,%s,%s,%s,%s,%s)",
                    (r['course_name'], r['course_code'], r['credits'], r['section'], r['teacher_id'], bool(r.get('published')))
                )
            print("teacher_courses copy complete.")

        # 2) Copy teacher constraints from constraints -> teacher_constraints
        tcons_exists = exists(db, "SELECT 1 FROM information_schema.tables WHERE TABLE_SCHEMA=%s AND TABLE_NAME='teacher_constraints'", (DB_CONFIG['database'],))
        if not tcons_exists:
            print("teacher_constraints table does not exist. Run SQL migration first.")
        else:
            # Try to detect owner_type/owner_id columns presence
            has_owner_type = exists(db, "SELECT 1 FROM information_schema.columns WHERE TABLE_SCHEMA=%s AND TABLE_NAME='constraints' AND COLUMN_NAME='owner_type'", (DB_CONFIG['database'],))
            has_owner_id = exists(db, "SELECT 1 FROM information_schema.columns WHERE TABLE_SCHEMA=%s AND TABLE_NAME='constraints' AND COLUMN_NAME='owner_id'", (DB_CONFIG['database'],))
            if has_owner_type:
                rows = db.fetchall("SELECT id, course_name, section, day, period_range, type, description, owner_id, published FROM constraints WHERE owner_type = 'teacher'")
            elif has_owner_id:
                rows = db.fetchall("SELECT id, course_name, section, day, period_range, type, description, owner_id, published FROM constraints WHERE owner_id IS NOT NULL")
            else:
                # No owner metadata: attempt to interpret rows where section or description mention teacher? (not safe)
                rows = []
                print("No owner metadata found in constraints table; skipping auto-copy for constraints. You may copy manually.")
            print(f"Found {len(rows)} teacher constraint rows to consider.")
            for r in rows:
                tid = r.get('owner_id') or r.get('owner_id')
                if not tid:
                    continue
                dup = db.fetchone("SELECT id FROM teacher_constraints WHERE course_name=%s AND teacher_id=%s AND day=%s AND period_range=%s LIMIT 1", (r['course_name'], tid, r['day'], r['period_range']))
                if dup:
                    continue
                db.execute(
                    "INSERT INTO teacher_constraints (course_name, section, day, period_range, type, description, teacher_id, published) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (r['course_name'], r.get('section') or 'ALL', r.get('day'), r.get('period_range'), r.get('type') or 'Hard', r.get('description'), tid, bool(r.get('published')))
                )
            print("teacher_constraints copy complete.")

    except Exception:
        print("Migration failed:")
        traceback.print_exc()

if __name__ == '__main__':
    main()