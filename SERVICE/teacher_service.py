from typing import List, Optional

class TeacherService:
    def __init__(self, teacher_dao):
        self.dao = teacher_dao

    def create_teacher(self, user_id: int, department: str = None) -> int:
        return self.dao.create(user_id, department)

    def list_teachers(self) -> List[dict]:
        return self.dao.list_all()

    def get_teacher(self, teacher_id: int) -> Optional[dict]:
        return self.dao.get_by_id(teacher_id)

    def get_by_user_id(self, user_id: int) -> Optional[dict]:
        return self.dao.get_by_user_id(user_id)

    def update(self, teacher_id: int, department: str = None):
        return self.dao.update(teacher_id, department)

    def delete_teacher(self, teacher_id: int):
        return self.dao.delete(teacher_id)