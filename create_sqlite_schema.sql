PRAGMA foreign_keys = ON;

-- users
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    username TEXT UNIQUE NOT NULL,
    password_hash TEXT NOT NULL,
    role TEXT NOT NULL,
    full_name TEXT NOT NULL,
    email TEXT UNIQUE,
    phone TEXT,
    created_at DATETIME DEFAULT (datetime('now'))
);

-- teachers
CREATE TABLE IF NOT EXISTS teachers (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER UNIQUE NOT NULL,
    department TEXT
    -- no FK: optional, declared below if you want:
    -- FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- courses (admin/global)
CREATE TABLE IF NOT EXISTS courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL,
    course_code TEXT NOT NULL,
    credits INTEGER DEFAULT 3 NOT NULL,
    section TEXT NOT NULL,
    teacher_id INTEGER,
    UNIQUE (course_name, section)
    -- no FK on teacher_id here either to avoid strict enforcement in SQLite; optionally add
    -- FOREIGN KEY (teacher_id) REFERENCES teachers(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_courses_code ON courses(course_code);

-- global constraints
CREATE TABLE IF NOT EXISTS constraints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL,
    section TEXT NOT NULL,
    day TEXT NOT NULL,
    period_range TEXT NOT NULL,
    type TEXT NOT NULL DEFAULT 'Hard',
    description TEXT,
    owner_type TEXT DEFAULT 'admin',
    owner_id INTEGER,
    published INTEGER DEFAULT 1
);

CREATE INDEX IF NOT EXISTS idx_constraints_owner ON constraints(owner_type, owner_id);

-- timetable
CREATE TABLE IF NOT EXISTS timetable (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    day TEXT NOT NULL,
    period INTEGER NOT NULL,
    course_name TEXT NOT NULL,
    teacher_name TEXT NOT NULL,
    section TEXT NOT NULL,
    UNIQUE (day, period, section)
);

-- teacher_courses WITHOUT FOREIGN KEY to teachers
CREATE TABLE IF NOT EXISTS teacher_courses (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL,
    course_code TEXT NOT NULL,
    credits INTEGER DEFAULT 3 NOT NULL,
    section TEXT NOT NULL,
    teacher_id INTEGER,
    published INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT (datetime('now')),
    UNIQUE (course_code, teacher_id),
    UNIQUE (course_name, section, teacher_id)
);

-- teacher_constraints WITHOUT FOREIGN KEY to teachers
CREATE TABLE IF NOT EXISTS teacher_constraints (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    course_name TEXT NOT NULL,
    section TEXT DEFAULT 'ALL',
    day TEXT NOT NULL,
    period_range TEXT NOT NULL,
    type TEXT DEFAULT 'Hard',
    description TEXT,
    teacher_id INTEGER,
    published INTEGER DEFAULT 0,
    created_at DATETIME DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_teacher_constraints_teacher ON teacher_constraints(teacher_id);
CREATE INDEX IF NOT EXISTS idx_teacher_constraints_day ON teacher_constraints(day);