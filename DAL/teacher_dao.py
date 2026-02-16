from typing import List, Optional

class TeacherDAO:
    def __init__(self, db):
        self.db = db

    def create(self, user_id: int, department: str = None) -> int:
        cur = self.db.execute(
            "INSERT INTO teachers (user_id, department) VALUES (%s,%s)",
            (user_id, department)
        )
        last = cur.lastrowid
        cur.close()
        return last

    def list_all(self) -> List[dict]:
        # Join users to return username/full_name
        return self.db.fetchall(
            "SELECT t.id, t.user_id, u.username, u.full_name, t.department "
            "FROM teachers t JOIN users u ON u.id = t.user_id ORDER BY u.full_name"
        )

    def get_by_id(self, teacher_id: int) -> Optional[dict]:
        return self.db.fetchone(
            "SELECT t.id, t.user_id, u.username, u.full_name, t.department "
            "FROM teachers t JOIN users u ON u.id = t.user_id WHERE t.id=%s LIMIT 1",
            (teacher_id,)
        )

    def get_by_user_id(self, user_id: int) -> Optional[dict]:
        return self.db.fetchone(
            "SELECT t.id, t.user_id, u.username, u.full_name, t.department "
            "FROM teachers t JOIN users u ON u.id = t.user_id WHERE t.user_id=%s LIMIT 1",
            (user_id,)
        )

    def update(self, teacher_id: int, department: str = None):
        self.db.execute("UPDATE teachers SET department=%s WHERE id=%s", (department, teacher_id))

    def delete(self, teacher_id: int):
        self.db.execute("DELETE FROM teachers WHERE id=%s", (teacher_id,))