# config.py
# Example config for using SQLite. To use MySQL, set engine='mysql' and provide host/user/password.
DB_CONFIG = {
    "engine": "sqlite",               # "sqlite" or "mysql"
    "database": "smart_timetable.db", # for sqlite this is a file path
    # MySQL settings (used only when engine == "mysql")
    "host": "127.0.0.1",
    "user": "root",
    "password": "**********",
    "raise_on_warnings": True,
}

APP_META = {
    "name": "Smart Timetable Optimizer",
    "version": "0.1.0",
}
