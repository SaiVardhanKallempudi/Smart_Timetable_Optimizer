# DAL/db_connector.py
# Unified DBConnector supporting both MySQL (mysql.connector) and SQLite (sqlite3).
# - Configure in config.py with DB_CONFIG['engine'] == 'sqlite' or 'mysql'.
# - Existing DAOs that use '%s' placeholders will keep working; placeholders are translated automatically for SQLite.
from typing import Optional, Tuple, Any
import logging
import threading
import sys
import os

logger = logging.getLogger(__name__)

# helper to replace %s with ? for sqlite parameter placeholders while leaving other SQL intact.
def _convert_placeholders_for_sqlite(sql: str) -> str:
    # A simple approach: replace '%s' with '?'
    # This works because DAOs consistently use %s for parameters.
    return sql.replace("%s", "?")

class DBConnector:
    def __init__(self, config: dict):
        self.config = config or {}
        self.engine = (self.config.get("engine") or "mysql").lower()
        self._lock = threading.RLock()
        self.connection = None
        # only import drivers when needed
        if self.engine == "mysql":
            try:
                import mysql.connector as mysql_connector  # type: ignore
                self.mysql_connector = mysql_connector
            except Exception as e:
                logger.exception("mysql.connector import failed: %s", e)
                raise
        else:
            import sqlite3  # type: ignore
            self.sqlite3 = sqlite3

        # create connection immediately
        self.connect()

    def connect(self):
        with self._lock:
            if self.connection:
                # try to ensure it's alive (MySQL has is_connected)
                try:
                    if self.engine == "mysql":
                        if getattr(self.connection, "is_connected", lambda: True)():
                            return
                    else:
                        # sqlite3: assume alive
                        return
                except Exception:
                    # recreate
                    try:
                        self.connection.close()
                    except Exception:
                        pass
                    self.connection = None

            if self.engine == "mysql":
                cfg = {
                    "host": self.config.get("host", "127.0.0.1"),
                    "user": self.config.get("user"),
                    "password": self.config.get("password"),
                    "database": self.config.get("database"),
                    "raise_on_warnings": self.config.get("raise_on_warnings", True),
                }
                try:
                    self.connection = self.mysql_connector.connect(**cfg)
                    logger.info("✅ MySQL connected")
                except Exception:
                    logger.exception("MySQL connection failed")
                    raise
            else:
                # sqlite
                # Use get_data_path helper when running frozen with PyInstaller so bundled DB is found.
                db_path_config = self.config.get("database", "smart_timetable.db")
                db_path = None
                try:
                    # Try to import helper (may not exist in older installs)
                    from UI.get_data_path import get_data_path  # type: ignore
                except Exception:
                    get_data_path = None

                if getattr(sys, "frozen", False) and get_data_path:
                    # When frozen, resources added with --add-data are extracted to sys._MEIPASS
                    try:
                        db_path = get_data_path(os.path.basename(db_path_config))
                    except Exception:
                        db_path = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(sys.executable)), os.path.basename(db_path_config))
                else:
                    # running normally: resolve absolute path relative to repo root / working dir
                    db_path = os.path.abspath(db_path_config)

                # allow multi-threaded access; careful with concurrency
                try:
                    # check_same_thread False allows access from threads; ensure your app handles concurrency
                    conn = self.sqlite3.connect(db_path, check_same_thread=False)
                    conn.row_factory = self.sqlite3.Row
                    # enable foreign keys
                    conn.execute("PRAGMA foreign_keys = ON;")
                    # reasonable busy timeout to reduce "database is locked" errors
                    conn.execute("PRAGMA busy_timeout = 2500;")  # milliseconds
                    self.connection = conn
                    logger.info("✅ SQLite connected to %s", db_path)
                except Exception:
                    logger.exception("SQLite connection failed (db_path=%s)", db_path)
                    raise

    def cursor(self, dictionary: bool = True):
        # Return a cursor appropriate for the engine
        self.connect()
        if self.engine == "mysql":
            return self.connection.cursor(dictionary=dictionary)
        else:
            # sqlite3 returns rows as sqlite3.Row when row_factory set; for write cursor keep default
            cur = self.connection.cursor()
            return cur

    def execute(self, query: str, params: Tuple[Any, ...] = None, commit: bool = True):
        """
        For INSERT/UPDATE/DELETE - returns a cursor-like object.
        Accepts DAOs querying using %s placeholders. When using sqlite, placeholders are converted.
        """
        self.connect()
        params = params or ()
        if self.engine == "sqlite":
            q = _convert_placeholders_for_sqlite(query)
            cur = self.connection.cursor()
            try:
                cur.execute(q, params)
                if commit:
                    self.connection.commit()
            except Exception:
                logger.exception("SQLite execute failed: %s ; params=%s", q, params)
                raise
            return cur
        else:
            cur = self.connection.cursor(dictionary=False)
            try:
                cur.execute(query, params or ())
                if commit:
                    self.connection.commit()
            except Exception:
                logger.exception("MySQL execute failed: %s ; params=%s", query, params)
                raise
            return cur

    def fetchone(self, query: str, params: Tuple[Any, ...] = None):
        self.connect()
        params = params or ()
        if self.engine == "sqlite":
            q = _convert_placeholders_for_sqlite(query)
            cur = self.connection.cursor()
            cur.execute(q, params)
            row = cur.fetchone()
            if row is None:
                return None
            # convert sqlite3.Row -> dict for compatibility
            return dict(row) if hasattr(row, "keys") else row
        else:
            cur = self.connection.cursor(dictionary=True)
            cur.execute(query, params or ())
            row = cur.fetchone()
            cur.close()
            return row

    def fetchall(self, query: str, params: Tuple[Any, ...] = None):
        self.connect()
        params = params or ()
        if self.engine == "sqlite":
            q = _convert_placeholders_for_sqlite(query)
            cur = self.connection.cursor()
            cur.execute(q, params)
            rows = cur.fetchall()
            # convert to list[dict]
            result = []
            for r in rows:
                try:
                    result.append(dict(r))
                except Exception:
                    # r may be tuple
                    result.append(r)
            return result
        else:
            cur = self.connection.cursor(dictionary=True)
            cur.execute(query, params or ())
            rows = cur.fetchall()
            cur.close()
            return rows

    def close(self):
        with self._lock:
            if self.connection:
                try:
                    self.connection.close()
                except Exception:
                    logger.exception("Error closing DB connection")
                self.connection = None
                logger.info("🔌 DB connection closed")