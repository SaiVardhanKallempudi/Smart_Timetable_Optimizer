import bcrypt
import re
from typing import Optional

EMAIL_RE = re.compile(r"^[^@]+@[^@]+\.[^@]+$")

class AuthService:
    def __init__(self, db):
        self.db = db

    def hash_password(self, raw: str) -> str:
        return bcrypt.hashpw(raw.encode('utf-8'), bcrypt.gensalt()).decode('utf-8')

    def verify_password(self, raw: str, hashed: str) -> bool:
        try:
            return bcrypt.checkpw(raw.encode('utf-8'), hashed.encode('utf-8'))
        except Exception:
            return False

    def create_user(self, username: str, password: str, role: str = "Teacher", full_name: str = "", email: str = None, phone: str = None) -> int:
        # Basic validation
        if not username or not password:
            raise ValueError("username and password required")
        # unique username
        existing = self.db.fetchone("SELECT id FROM users WHERE username=%s LIMIT 1", (username,))
        if existing:
            raise ValueError("username already exists")
        if email:
            if not EMAIL_RE.match(email):
                raise ValueError("invalid email format")
            existing_email = self.db.fetchone("SELECT id FROM users WHERE email=%s LIMIT 1", (email,))
            if existing_email:
                raise ValueError("email already in use")
        hashed = self.hash_password(password)
        cur = self.db.execute(
            "INSERT INTO users (username, password_hash, role, full_name, email, phone) VALUES (%s,%s,%s,%s,%s,%s)",
            (username, hashed, role, full_name, email, phone)
        )
        last = cur.lastrowid
        cur.close()
        return last

    def authenticate(self, username: str, password: str) -> Optional[dict]:
        # dev-backdoor
        if username == "admin" and password == "admin123":
            return {"id": 0, "username": "admin", "role": "Admin", "full_name": "Administrator"}

        row = self.db.fetchone("SELECT id, username, password_hash, role, full_name FROM users WHERE username=%s LIMIT 1", (username,))
        if not row:
            return None
        if self.verify_password(password, row['password_hash']):
            return {"id": row['id'], "username": row['username'], "role": row['role'], "full_name": row.get('full_name')}
        return None