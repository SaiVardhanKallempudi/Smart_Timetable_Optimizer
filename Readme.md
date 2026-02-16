Smart Timetable Generator

Project Type: Desktop Application (Python + PyQt5)
Developed by: K Sai Vardhan

📘 Overview

Smart Timetable Generator is a desktop-based application designed to automatically generate optimized academic timetables for educational institutions.
It minimizes manual effort, avoids scheduling conflicts, and allows both Admin and Teacher roles to manage their respective timetables efficiently.

🧩 Architecture — Three Tier Design
Layer	Components	Responsibility
Presentation (UI)	PyQt5-based Windows (Login, Admin Dashboard, Teacher Dashboard)	User interface and event handling
Business Logic (Service)	Authentication, Course, Constraint, Timetable Services	Core logic, validation, and data flow
Data Access (DAL)	DAOs, DBConnector (SQLite/MySQL)	Database operations and persistence
👥 User Roles
👨‍💼 Admin

Login Credentials:
Username: adminanits
Password: Admin@123

Features:

Teacher Management — Create, Edit, Delete teacher accounts

Timetable Generator —

Inputs: number of periods, lunch period, course code, course name, section

Add constraints (e.g., OOSE Lab, Monday, P1-P3)

Generate optimized timetable

Profile Tab — View admin profile

👩‍🏫 Teacher

Login: Provided by Admin (teacher username/password)
Login Credentials:
Username: sai
Password: 123

Features:

Timetable Generator

Enter course and constraint data

Generate timetable specific to teacher

Adjust generated timetable (double-click to edit cells)

Save timetable for history

Export as PDF to local machine

History Tab — View all saved timetables

Profile Tab — View personal information

About Tab — Information about the app

⚙️ Technologies Used

Programming Language: Python 3.10+

GUI Framework: PyQt5

Database: SQLite (default) / MySQL (optional)

PDF Generation: ReportLab

Optimization Solver: Google OR-Tools (fallback to Greedy CSP if unavailable)

Packaging: PyInstaller

🧩 Installation & Setup

You can install and run this project in two ways — from source code or using the ready-built executable (.exe).

🪶 Option 1 — Run from Source Code
Step 1: Install Python

Download and install Python 3.10+
.
✅ During installation, make sure to check “Add Python to PATH”.

Step 2: Extract Project Files

Unzip the project folder into a location such as:

C:\Users\<yourname>\Documents\SmartTimetableGenerator

Step 3: Open Command Prompt

Navigate to your project folder:

cd C:\Users\<yourname>\Documents\SmartTimetableGenerator

Step 4: Create a Virtual Environment

This keeps dependencies isolated and avoids system conflicts:

python -m venv venv

Step 5: Activate the Virtual Environment

For Windows:

venv\Scripts\activate


For macOS / Linux:

source venv/bin/activate


You’ll know it’s activated when you see (venv) appear in your terminal.

Step 6: Install Required Dependencies

Install all required libraries from the provided requirements.txt:

pip install -r requirements.txt


If you encounter any missing packages, install manually:

pip install PyQt5 bcrypt reportlab

Step 7: Run the Application

Launch the app using:

python main.py


This will open the Login Window of Smart Timetable Generator.

🪟 Option 2 — Run from Executable (.exe)

If you already have the compiled .exe version:

Extract the ZIP package.

Double-click SmartTimetableGenerator.exe.

The application will open instantly — no Python setup needed.

💾 Database Setup

The application includes a ready-to-use SQLite database file smart_timetable.db, which contains:

Admin credentials (adminanits, Admin@123)

Predefined schema for users, courses, constraints, and timetables

If you ever need to reset or recreate it, run:

python init_db.py

🧾 Key Files
File / Folder	Description
main.py	Entry point of the application
config.py	Database and app configuration
UI/	User Interface files (login, dashboards)
SERVICE/	Business logic and service modules
DAL/	Database connectors and DAO classes
smart_timetable.db	Default SQLite database
create_sqlite_schema.sql	SQL schema for initialization
pdf_export.py	PDF export helper
solver_runner.py	Timetable optimization logic
📤 Export and History

After generating a timetable, teachers can:

Save timetables to their History tab.

Export timetables as PDF using the “Export” button.
