
import re
from typing import List, Optional

EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

class CourseService:
    def __init__(self, course_dao):
        self.dao = course_dao

    def create_course(self, name: str, code: str, credits: int = 3, section: str = "A", teacher_id: int = None) -> int:
        """
        Create a global/admin course (stored in the `courses` table).
        This calls the DAO.create() legacy insert which writes to the admin `courses` table.
        """
        # Keep simple uniqueness check using DAO.get_by_code (legacy behavior)
        try:
            existing = None
            if hasattr(self.dao, "get_by_code"):
                existing = self.dao.get_by_code(code)
            if existing:
                raise ValueError(f"Course code '{code}' already exists.")
        except Exception:
            # If DAO check fails for any reason, allow DAO to raise on insert
            existing = None

        return self.dao.create(name, code, credits, section, teacher_id)

    def create_for_teacher(self, name: str, code: str, section: str, teacher_owner_id: int, credits: int = 1, teacher_id: int = None) -> int:
        """
        Create a teacher-scoped course.
        Writes into teacher_courses via DAO.create_teacher_course when available.
        teacher_owner_id: the teacher who owns the course (owner/author).
        teacher_id: optional assigned teacher reference (may be same as owner).
        """
        # Prefer teacher-scoped creation API on the DAO
        if hasattr(self.dao, "create_teacher_course"):
            return self.dao.create_teacher_course(name, code, credits, section, teacher_id or teacher_owner_id, published=False)
        # Fallback: use legacy create with teacher_id column
        return self.dao.create(name, code, credits, section, teacher_id or teacher_owner_id)

    def list_all(self) -> List[dict]:
        return self.dao.list_all()

    def list_by_section(self, section: str):
        return self.dao.get_by_section(section)

    def list_by_owner(self, owner_type: str = 'admin', owner_id: int = None, only_published: bool = True):
        """
        Helper used by UI/services to load either admin/global courses or teacher-specific courses.
        """
        if hasattr(self.dao, "list_by_owner"):
            return self.dao.list_by_owner(owner_type, owner_id, only_published)
        # Fallbacks:
        if owner_type == 'admin':
            return self.dao.list_all()
        if owner_type == 'teacher' and owner_id is not None:
            if hasattr(self.dao, "list_teacher_courses"):
                return self.dao.list_teacher_courses(owner_id, only_published=only_published)
            # last-resort fallback: filter list_all by teacher_id column
            return [c for c in self.dao.list_all() if c.get('teacher_id') == owner_id]
        return []

    def update_course(self, course_id: int, **kwargs):
        return self.dao.update(course_id, **kwargs)