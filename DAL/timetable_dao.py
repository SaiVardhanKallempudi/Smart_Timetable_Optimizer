# DAL/timetable_dao.py
# Timetable DAO - store named timetable saves in timetable_sets (grid stored as JSON)
from typing import List, Dict, Optional, Any
import logging
import traceback
import json

logger = logging.getLogger(__name__)

class TimetableDAO:
    def __init__(self, db):
        self.db = db

    def _ensure_timetable_sets_table(self):
        try:
            # timetable_sets.grid stores the grid JSON so we don't rely on altering the existing timetable table
            self.db.execute("""
                CREATE TABLE IF NOT EXISTS timetable_sets (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    created_by TEXT,
                    created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
                    grid TEXT NOT NULL
                )
            """, (), commit=True)
        except Exception:
            logger.debug("Could not create timetable_sets (may exist or DB does not support). Ignoring.", exc_info=True)

    def save_entries(self, rows: List[dict], set_id: Optional[int] = None):
        """
        Save timetable rows into the legacy 'timetable' table (no set_id).
        Used as a fallback when callers expect rows to be saved into timetable table.
        """
        for r in rows:
            try:
                # legacy insert - do NOT try to set set_id column (it may not exist)
                self.db.execute(
                    "INSERT INTO timetable (day, period, course_name, teacher_name, section) VALUES (%s,%s,%s,%s,%s)",
                    (r['day'], r['period'], r['course_name'], r.get('teacher_name', ''), r.get('section', ''))
                )
            except Exception:
                logger.error("Failed to insert timetable row %s:\n%s", r, traceback.format_exc())

    def save_timetable(self, name: str, grid: Dict[str, List[str]], section: str = "A", created_by: str = None) -> int:
        """
        Save a named timetable into timetable_sets table storing the grid as JSON.
        Returns set_id if created (int), otherwise 0.
        """
        try:
            self._ensure_timetable_sets_table()
            payload = {
                "section": section,
                "grid": grid
            }
            cur = self.db.execute("INSERT INTO timetable_sets (name, created_by, grid) VALUES (%s,%s,%s)",
                                  (name, created_by, json.dumps(payload)))
            set_id = getattr(cur, "lastrowid", None) or None
            try:
                cur.close()
            except Exception:
                pass
            return int(set_id) if set_id is not None else 0
        except Exception:
            logger.exception("save_timetable failed")
            return 0

    def list_sets(self) -> List[Dict[str, Any]]:
        """
        Return list of named timetable saves (timetable_sets) if table exists.
        """
        try:
            # return id, name, created_by, created_at for UI
            rows = self.db.fetchall("SELECT id, name, created_by, created_at FROM timetable_sets ORDER BY created_at DESC")
            # Ensure standard keys
            result = []
            for r in rows:
                # r might be sqlite3.Row or dict
                try:
                    rec = {
                        "id": r.get("id") if isinstance(r, dict) else r["id"],
                        "name": r.get("name") if isinstance(r, dict) else r["name"],
                        "created_by": r.get("created_by") if isinstance(r, dict) else r["created_by"],
                        "created_at": r.get("created_at") if isinstance(r, dict) else r["created_at"],
                    }
                except Exception:
                    # Fallback generic handling
                    rec = r
                result.append(rec)
            return result
        except Exception:
            logger.debug("timetable_sets not found or query failed", exc_info=True)
            return []

    def get_set_entries(self, set_id: int) -> List[Dict[str, Any]]:
        """
        Return timetable rows for a saved set_id by reading timetable_sets.grid JSON and flattening it into a list of rows:
        [{'day': 'Monday', 'period': 1, 'course_name': 'X', 'teacher_name': '', 'section': 'A'}, ...]
        Falls back to reading timetable table if timetable_sets is not available.
        """
        try:
            # Try reading from timetable_sets
            rows = self.db.fetchone("SELECT grid FROM timetable_sets WHERE id=%s", (set_id,))
            if not rows:
                # fallback: maybe older schema where timetable.set_id existed — try timetable table filtering (will likely fail if set_id col missing)
                try:
                    return self.db.fetchall("SELECT day, period, course_name, teacher_name, section FROM timetable WHERE set_id=%s ORDER BY day, period", (set_id,))
                except Exception:
                    logger.debug("timetable.set_id may not exist or query failed, returning empty list", exc_info=True)
                    return []
            # rows may be dict or sqlite3.Row
            grid_json = rows.get("grid") if isinstance(rows, dict) else rows["grid"]
            payload = json.loads(grid_json)
            grid = payload.get("grid") if isinstance(payload, dict) and "grid" in payload else payload
            section = payload.get("section") if isinstance(payload, dict) else None
            result = []
            # grid is dict of day -> list
            for day, cells in grid.items():
                for idx, txt in enumerate(cells):
                    result.append({
                        "day": day,
                        "period": idx + 1,
                        "course_name": txt,
                        "teacher_name": "",
                        "section": section or ""
                    })
            return result
        except Exception:
            logger.exception("get_set_entries failed")
            # final fallback: try listing all timetable rows
            try:
                return self.db.fetchall("SELECT day, period, course_name, teacher_name, section FROM timetable ORDER BY day, period")
            except Exception:
                logger.exception("Fallback fetch all timetable rows failed")
                return []

    def delete_set(self, set_id: int) -> bool:
        """
        Delete a saved timetable set from timetable_sets.
        Returns True if deletion succeeded.
        """
        try:
            # If table doesn't exist, return False
            self._ensure_timetable_sets_table()
            self.db.execute("DELETE FROM timetable_sets WHERE id=%s", (set_id,))
            return True
        except Exception:
            logger.exception("delete_set failed")
            return False

    def list_all(self) -> List[Dict[str, Any]]:
        """
        Returns all timetable rows (useful for history)
        """
        try:
            return self.db.fetchall("SELECT id, day, period, course_name, teacher_name, section FROM timetable ORDER BY day, period")
        except Exception:
            logger.exception("list_all failed")
            return []