from typing import List, Optional
import logging

try:
    import mysql.connector
    MYSQL_PROG_ERROR = mysql.connector.errors.ProgrammingError
except Exception:
    MYSQL_PROG_ERROR = Exception

logger = logging.getLogger(__name__)


class ConstraintDAO:
    def __init__(self, db):
        self.db = db

    def add(self, course_name: str, section: str, day: str, period_range: str, type_: str = "Hard", description: str = None, teacher_id: Optional[int] = None, published: bool = True):
        """
        If teacher_id passed, insert into teacher_constraints (teacher-local); otherwise insert into global constraints table.
        Falls back to global insert if teacher_constraints table does not exist.
        """
        if teacher_id:
            try:
                cur = self.db.execute(
                    "INSERT INTO teacher_constraints (course_name, section, day, period_range, type, description, teacher_id, published) VALUES (%s,%s,%s,%s,%s,%s,%s,%s)",
                    (course_name, section, day, period_range, type_, description, teacher_id, published)
                )
                last = getattr(cur, "lastrowid", None)
                try:
                    cur.close()
                except Exception:
                    pass
                return last
            except MYSQL_PROG_ERROR as e:
                msg = str(e).lower()
                if "doesn't exist" in msg or getattr(e, "errno", None) == 1146:
                    logger.debug("teacher_constraints table missing; falling back to global constraints table")
                else:
                    raise
        # fallback to global constraints table
        cur = self.db.execute(
            "INSERT INTO constraints (course_name, section, day, period_range, type, description) VALUES (%s,%s,%s,%s,%s,%s)",
            (course_name, section, day, period_range, type_, description)
        )
        last = getattr(cur, "lastrowid", None)
        try:
            cur.close()
        except Exception:
            pass
        return last

    def list_all(self) -> List[dict]:
        """
        Return global constraints -- from `constraints` (admin/global).
        """
        try:
            return self.db.fetchall("SELECT id, course_name, section, day, period_range, type, description FROM constraints ORDER BY id DESC")
        except Exception:
            logger.exception("list_all failed")
            return []

    def list_teacher_constraints(self, teacher_id: int, only_published: bool = True) -> List[dict]:
        """
        Return teacher-local constraints from `teacher_constraints`. Fallback: filter global constraints by owner_id/owner_type if present.
        """
        try:
            q = "SELECT id, course_name, section, day, period_range, type, description, teacher_id, published FROM teacher_constraints WHERE teacher_id=%s"
            params = [teacher_id]
            if only_published:
                q += " AND published = TRUE"
            q += " ORDER BY id DESC"
            return self.db.fetchall(q, tuple(params))
        except MYSQL_PROG_ERROR as e:
            # fallback: try to query constraints table for owner metadata (older schema)
            msg = str(e).lower()
            if "doesn't exist" in msg or getattr(e, "errno", None) == 1146:
                logger.debug("teacher_constraints missing; attempting fallback from constraints table")
                # try owner_type/owner_id columns
                try:
                    return self.db.fetchall("SELECT id, course_name, section, day, period_range, type, description, owner_id, published FROM constraints WHERE owner_type='teacher' AND owner_id=%s ORDER BY id DESC", (teacher_id,))
                except Exception:
                    # try owner_id only
                    try:
                        return self.db.fetchall("SELECT id, course_name, section, day, period_range, type, description, owner_id, published FROM constraints WHERE owner_id=%s ORDER BY id DESC", (teacher_id,))
                    except Exception:
                        return []
            raise

    def list_by_owner(self, owner_type: str = 'admin', owner_id: Optional[int] = None, only_published: bool = True) -> List[dict]:
        if owner_type == 'admin':
            return self.list_all()
        elif owner_type == 'teacher':
            if owner_id is None:
                return []
            return self.list_teacher_constraints(owner_id, only_published=only_published)
        return []

    def delete(self, constraint_id: int) -> bool:
        """
        Delete a constraint by id. Try teacher_constraints first (if table exists), then constraints table.
        Returns True if a row was deleted, False if no matching row was found.
        """
        if constraint_id is None:
            return False

        # Try deleting from teacher_constraints first
        try:
            try:
                cur = self.db.execute("DELETE FROM teacher_constraints WHERE id=%s", (constraint_id,))
                rowcount = getattr(cur, "rowcount", None)
                try:
                    cur.close()
                except Exception:
                    pass
                if rowcount and rowcount > 0:
                    return True
                # If teacher_constraints exists but rowcount == 0, continue to try global table
            except MYSQL_PROG_ERROR as e:
                # If table doesn't exist, fall through to global constraints
                msg = str(e).lower()
                if "doesn't exist" in msg or getattr(e, "errno", None) == 1146:
                    logger.debug("teacher_constraints table missing when deleting; will try global constraints table")
                else:
                    raise

            # Try deleting from global constraints table
            try:
                cur2 = self.db.execute("DELETE FROM constraints WHERE id=%s", (constraint_id,))
                rowcount2 = getattr(cur2, "rowcount", None)
                try:
                    cur2.close()
                except Exception:
                    pass
                if rowcount2 and rowcount2 > 0:
                    return True
                return False
            except MYSQL_PROG_ERROR as e2:
                msg2 = str(e2).lower()
                if "doesn't exist" in msg2 or getattr(e2, "errno", None) == 1146:
                    logger.debug("constraints table missing when attempting delete")
                    return False
                raise
        except Exception:
            logger.exception("ConstraintDAO.delete failed for id=%s", constraint_id)
            raise

    # alias for compatibility
    def remove(self, constraint_id: int) -> bool:
        return self.delete(constraint_id)