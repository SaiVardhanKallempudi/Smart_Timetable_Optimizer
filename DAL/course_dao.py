
from typing import List, Optional, Any
import logging

logger = logging.getLogger(__name__)

try:
    import mysql.connector
    MYSQL_INTEGRITY_ERROR = mysql.connector.errors.IntegrityError
except Exception:
    class MYSQL_INTEGRITY_ERROR(Exception):
        pass


class CourseDAO:
    def __init__(self, db):
        """
        db is an object exposing execute(query, params) -> cursor,
        fetchall(sql, params) -> list[dict], fetchone(sql, params) -> dict
        and the execute cursor exposes lastrowid.
        """
        self.db = db

    def create(self, course_name: str, course_code: str, credits: int = 3, section: str = "A", teacher_id: int = None):
        cur = self.db.execute(
            "INSERT INTO courses (course_name, course_code, credits, section, teacher_id) VALUES (%s,%s,%s,%s,%s)",
            (course_name, course_code, credits, section, teacher_id)
        )
        last = getattr(cur, "lastrowid", None)
        try:
            cur.close()
        except Exception:
            pass
        return last

    def create_teacher_course(self, course_name: str, course_code: str, credits: int = 3, section: str = "A", teacher_id: int = None, published: bool = True):
        """
        Create a teacher-scoped course row in teacher_courses.
        If teacher_courses table is not present, fall back to legacy courses table.
        On unique-constraint duplicate, return the existing teacher_courses id.
        Default published=True so teacher-created rows appear immediately.
        """
        try:
            cur = self.db.execute(
                "INSERT INTO teacher_courses (course_name, course_code, credits, section, teacher_id, published) VALUES (%s,%s,%s,%s,%s,%s)",
                (course_name, course_code, credits, section, teacher_id, published)
            )
            last = getattr(cur, "lastrowid", None)
            try:
                cur.close()
            except Exception:
                pass
            return last
        except MYSQL_INTEGRITY_ERROR as e:
            # If unique constraint triggered, attempt to return existing id
            try:
                row = self.db.fetchone("SELECT id FROM teacher_courses WHERE course_code=%s AND teacher_id=%s LIMIT 1", (course_code, teacher_id))
                if row:
                    return row.get("id")
            except Exception:
                pass
            # if it's a different programming error re-raise
            raise
        except Exception as e:
            # Table may not exist; fallback to legacy courses table
            msg = str(e).lower()
            if "doesn't exist" in msg or "unknown table" in msg or getattr(e, "errno", None) == 1146:
                logger.debug("teacher_courses missing; falling back to courses table for teacher course creation")
                return self.create(course_name, course_code, credits, section, teacher_id)
            raise

    def list_all(self) -> List[dict]:
        """
        Return admin/global courses from `courses` table.
        """
        try:
            return self.db.fetchall("SELECT id, course_name, course_code, credits, section, teacher_id, NULL AS published FROM courses ORDER BY course_code")
        except Exception:
            logger.exception("list_all failed")
            return []

    def list_teacher_courses(self, teacher_id: Optional[int], only_published: bool = True) -> List[dict]:
        """
        Return courses specifically created under teacher scope.
        If teacher_id is None, return all teacher_courses (useful for admin overview).
        """
        try:
            if teacher_id is None:
                q = "SELECT id, course_name, course_code, credits, section, teacher_id, published FROM teacher_courses"
                if only_published:
                    q += " WHERE published = TRUE"
                q += " ORDER BY course_code"
                return self.db.fetchall(q)
            else:
                q = "SELECT id, course_name, course_code, credits, section, teacher_id, published FROM teacher_courses WHERE teacher_id=%s"
                params = (teacher_id,)
                if only_published:
                    q += " AND published = TRUE"
                q += " ORDER BY course_code"
                return self.db.fetchall(q, params)
        except Exception as e:
            # table missing -> fallback to courses table filtered by teacher_id
            msg = str(e).lower()
            if "doesn't exist" in msg or "unknown table" in msg or getattr(e, "errno", None) == 1146:
                logger.debug("teacher_courses table missing; falling back to courses table")
                if teacher_id is None:
                    return self.list_all()
                return self.db.fetchall("SELECT id, course_name, course_code, credits, section, teacher_id FROM courses WHERE teacher_id=%s ORDER BY course_code", (teacher_id,))
            logger.exception("Unexpected error listing teacher_courses")
            return []

    def list_by_owner(self, owner_type: str = 'admin', owner_id: Optional[int] = None, only_published: bool = True) -> List[dict]:
        """
        owner_type == 'admin' -> admin/global courses
        owner_type == 'teacher' -> teacher-scoped courses for owner_id
        """
        if owner_type == 'admin':
            return self.list_all()
        elif owner_type == 'teacher':
            if owner_id is None:
                return []
            return self.list_teacher_courses(owner_id, only_published=only_published)
        else:
            return []

    def get_by_id(self, course_id: int) -> Optional[dict]:
        # Try teacher_courses first, then courses
        try:
            r = self.db.fetchone("SELECT id, course_name, course_code, credits, section, teacher_id FROM teacher_courses WHERE id=%s LIMIT 1", (course_id,))
            if r:
                return r
        except Exception:
            pass
        return self.db.fetchone("SELECT id, course_name, course_code, credits, section, teacher_id FROM courses WHERE id=%s LIMIT 1", (course_id,))

    def get_teacher_course_by_code(self, code: str, teacher_id: int) -> Optional[dict]:
        """
        Look up a course within teacher_courses for a given teacher.
        """
        try:
            return self.db.fetchone("SELECT id, course_name, course_code, credits, section, teacher_id FROM teacher_courses WHERE course_code=%s AND teacher_id=%s LIMIT 1", (code, teacher_id))
        except Exception:
            return None

    def get_by_code(self, code: str, teacher_scope_id: Optional[int] = None) -> Optional[dict]:
        """
        If teacher_scope_id provided, prefer searching teacher_courses for that teacher first.
        Otherwise return the first match from courses (global).
        """
        if teacher_scope_id:
            try:
                r = self.db.fetchone("SELECT id, course_name, course_code, credits, section, teacher_id FROM teacher_courses WHERE course_code=%s AND teacher_id=%s LIMIT 1", (code, teacher_scope_id))
                if r:
                    return r
            except Exception:
                pass
        return self.db.fetchone("SELECT id, course_name, course_code, credits, section, teacher_id FROM courses WHERE course_code=%s LIMIT 1", (code,))

    def get_by_section(self, section: str) -> List[dict]:
        return self.db.fetchall("SELECT id, course_name, course_code, credits, section, teacher_id FROM courses WHERE section=%s", (section,))

    def update(self, course_id: int, course_name: str = None, course_code: str = None, credits: int = None, section: str = None, teacher_id: int = None, published: Optional[bool] = None):
        # Try to update teacher_courses row; if fails or not present, update courses table
        updated = False
        try:
            # Build dynamic update for teacher_courses
            fields = []
            params = []
            if course_name is not None:
                fields.append("course_name=%s"); params.append(course_name)
            if course_code is not None:
                fields.append("course_code=%s"); params.append(course_code)
            if credits is not None:
                fields.append("credits=%s"); params.append(credits)
            if section is not None:
                fields.append("section=%s"); params.append(section)
            if teacher_id is not None:
                fields.append("teacher_id=%s"); params.append(teacher_id)
            if published is not None:
                fields.append("published=%s"); params.append(published)
            if fields:
                params.append(course_id)
                q = f"UPDATE teacher_courses SET {', '.join(fields)} WHERE id=%s"
                cur = self.db.execute(q, tuple(params))
                # if rows affected > 0 assume updated
                try:
                    rowcount = getattr(cur, "rowcount", None)
                    if rowcount and rowcount > 0:
                        updated = True
                except Exception:
                    updated = True
        except Exception as e:
            msg = str(e).lower()
            if "doesn't exist" in msg or getattr(e, "errno", None) == 1146:
                logger.debug("teacher_courses missing; updating legacy courses instead")
            else:
                logger.exception("Unexpected error updating teacher_courses")

        # fallback to courses table
        if not updated:
            fields = []
            params = []
            if course_name is not None:
                fields.append("course_name=%s"); params.append(course_name)
            if course_code is not None:
                fields.append("course_code=%s"); params.append(course_code)
            if credits is not None:
                fields.append("credits=%s"); params.append(credits)
            if section is not None:
                fields.append("section=%s"); params.append(section)
            if teacher_id is not None:
                fields.append("teacher_id=%s"); params.append(teacher_id)
            if not fields:
                return
            params.append(course_id)
            q = f"UPDATE courses SET {', '.join(fields)} WHERE id=%s"
            self.db.execute(q, tuple(params))

    def delete(self, course_id: int):
        # Prefer deleting from teacher_courses (teacher-scoped) then try legacy courses
        try:
            self.db.execute("DELETE FROM teacher_courses WHERE id=%s", (course_id,))
        except Exception:
            pass
        try:
            self.db.execute("DELETE FROM courses WHERE id=%s", (course_id,))
        except Exception:
            pass