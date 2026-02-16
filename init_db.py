import sqlite3

# Connect or create the database file
conn = sqlite3.connect("smart_timetable.db")
cursor = conn.cursor()

# Read your SQL schema file
with open("create_sqlite_schema.sql", "r", encoding="utf-8") as f:
    sql_script = f.read()

# Execute all SQL statements
cursor.executescript(sql_script)

conn.commit()
conn.close()

print("✅ SQLite database created successfully: smart_timetable.db")
