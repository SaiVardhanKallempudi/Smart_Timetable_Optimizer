import unittest
from config import DB_CONFIG
from DAL.db_connector import DBConnector
from SERVICE.auth_service import AuthService


class TestRealAuth(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        # Initialize real DB connection
        cls.db = DBConnector(DB_CONFIG)
        cls.auth = AuthService(cls.db)

    def test_admin_valid_login(self):
        """Test real login using correct credentials."""
        user = self.auth.authenticate("adminanits", "Admin@123")
        self.assertIsNotNone(user, "User should not be None for valid credentials")
        self.assertEqual(user["username"], "adminanits")
        self.assertEqual(user["role"], "Admin")

    def test_wrong_password(self):
        """Ensure wrong password fails."""
        user = self.auth.authenticate("adminanits", "wrongpass")
        self.assertIsNone(user, "Authentication must fail for wrong password")

    def test_unknown_user(self):
        """Ensure unknown username fails."""
        user = self.auth.authenticate("nosuchuser", "anything")
        self.assertIsNone(user, "Authentication must fail for unknown user")


if __name__ == "__main__":
    unittest.main()
