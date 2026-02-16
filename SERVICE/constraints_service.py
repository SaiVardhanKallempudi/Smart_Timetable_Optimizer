import logging
import traceback
from typing import List, Optional

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)


class ConstraintService:
    DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

    def __init__(self, constraint_dao):
        """
        constraint_dao: an instance implementing add(...), list_all(), list_by_owner(...), delete(...)
        The DAO used in this project supports teacher-specific tables via a teacher_id parameter.
        """
        self.dao = constraint_dao

    def add_from_text(self, line: str, owner_type: str = "admin", owner_id: Optional[int] = None, published: bool = True) -> Optional[int]:
        """
        Parse a textual constraint line and persist it with owner metadata.
        This implementation maps teacher ownership to the DAO's teacher_id parameter when owner_type == 'teacher'.
        Expected formats:
          - course_name,day,P1-P3
          - course_name,section,day,P1-P3
          - course_name,section,day,P1-P3,mode
        """
        parts = [p.strip() for p in line.split(",")]
        if len(parts) == 3:
            course_name, day, period_range = parts
            section = "ALL"
            mode = None
        elif len(parts) == 4:
            # disambiguate: second token might be a section (A) or a day (Monday)
            if parts[1].capitalize() in self.DAYS:
                course_name, day, period_range, mode = parts
                section = "ALL"
            else:
                course_name, section, day, period_range = parts
                mode = None
        elif len(parts) == 5:
            course_name, section, day, period_range, mode = parts
        else:
            raise ValueError("Constraint format invalid. Expected 3-5 comma-separated tokens.")

        day = (day or "").strip().capitalize()
        if day not in self.DAYS:
            day = (day or "").strip().title()

        if mode and isinstance(mode, str) and mode.strip().lower() in ("exact", "block", "full"):
            type_ = "Exact"
            description = mode.strip()
        else:
            type_ = "Hard"
            description = None

        # Map owner_type -> teacher_id for DAO if needed
        teacher_id = None
        if owner_type == 'teacher':
            teacher_id = owner_id

        try:
            # DAO.add signature: add(course_name, section, day, period_range, type_, description, teacher_id=None, published=True)
            return self.dao.add(course_name.strip(), section.strip() or "ALL", day, period_range.strip(),
                                type_, description, teacher_id=teacher_id, published=published)
        except Exception:
            logger.error("ConstraintService.add_from_text -> DAO add failed: %s", traceback.format_exc())
            raise

    def add_for_teacher(self, line: str, teacher_id: int, published: bool = False) -> Optional[int]:
        """
        Convenience wrapper for a teacher adding a constraint. Default to draft (published=False).
        """
        return self.add_from_text(line, owner_type="teacher", owner_id=teacher_id, published=published)

    def list_constraints(self) -> List[dict]:
        try:
            if hasattr(self.dao, "list_all"):
                return self.dao.list_all() or []
        except Exception:
            logger.error("ConstraintService.list_constraints failed: %s", traceback.format_exc())
        return []

    def list_constraints_for_teacher(self, teacher_id: int, include_admin: bool = True, only_published: bool = True) -> List[dict]:
        """
        Return constraints that should be considered when a teacher generates their timetable:
        admin constraints (if include_admin=True) + teacher constraints for teacher_id.
        This uses DAO.list_by_owner/list_teacher_constraints depending on what's available.
        """
        result = []
        try:
            if include_admin:
                # admin/global constraints
                if hasattr(self.dao, "list_by_owner"):
                    result.extend(self.dao.list_by_owner('admin', None, only_published=only_published))
                else:
                    result.extend(self.list_constraints())

            # teacher-specific constraints
            if hasattr(self.dao, "list_teacher_constraints"):
                result.extend(self.dao.list_teacher_constraints(teacher_id, only_published=only_published))
            elif hasattr(self.dao, "list_by_owner"):
                result.extend(self.dao.list_by_owner('teacher', teacher_id, only_published=only_published))
            else:
                # fallback: filter list_all by teacher indicator if present
                all_cons = self.list_constraints()
                result.extend([c for c in all_cons if c.get('teacher_id') == teacher_id])
        except Exception:
            logger.error("ConstraintService.list_constraints_for_teacher failed: %s", traceback.format_exc())
        return result

    def delete_constraint(self, constraint_id: int) -> bool:
        """
        High-level delete. Calls DAO.delete (or remove). Returns True if deleted.
        """
        try:
            if hasattr(self.dao, "delete"):
                return bool(self.dao.delete(constraint_id))
            if hasattr(self.dao, "remove"):
                return bool(self.dao.remove(constraint_id))
            raise RuntimeError("Constraint DAO has no delete/remove method")
        except Exception:
            logger.error("ConstraintService.delete_constraint failed for id=%s: %s", constraint_id, traceback.format_exc())
            raise