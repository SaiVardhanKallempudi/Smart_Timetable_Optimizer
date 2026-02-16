# Updated UI/admin_dashboard.py
# - Improved _locate_solver_runner() to search for solver_runner.py and common misspelling solve_runner.py
# - Error message now mentions both candidates and returns the first existing file found
# - No change to solver payload logic (constraints are normalized/resolved before sending)
# - (All other UI improvements already present in original file retained)
import os
import sys
import json
import subprocess
import logging
import traceback
from pathlib import Path
from typing import List, Dict, Optional, Any

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QMessageBox, QFrame
from PyQt5.QtCore import QObject, QThread, pyqtSignal

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("AdminDashboard")


class SubprocessWorker(QObject):
    finished = pyqtSignal(dict)
    error = pyqtSignal(str)

    def __init__(self, runner_path: str, payload: dict, timeout: int):
        super().__init__()
        self.runner_path = runner_path
        self.payload = payload
        self.timeout = int(timeout)
        self._cancel_requested = False

    def request_cancel(self):
        self._cancel_requested = True

    def _find_python_executable(self) -> str:
        return sys.executable or "python"

    def run(self):
        try:
            if self._cancel_requested:
                self.error.emit("Cancelled before start")
                return

            python = self._find_python_executable()
            cmd = [python, self.runner_path]
            logger.debug("Starting solver subprocess: %s", cmd)

            proc = subprocess.Popen(
                cmd,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True
            )

            input_text = json.dumps(self.payload)
            try:
                stdout, stderr = proc.communicate(input=input_text, timeout=self.timeout + 5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                stdout, stderr = proc.communicate()
                msg = f"Solver subprocess timed out after {self.timeout + 5} seconds."
                if stderr:
                    msg += f"\nStderr:\n{stderr}"
                logger.error(msg)
                self.error.emit(msg)
                return

            if proc.returncode != 0:
                msg = f"Solver subprocess exited with code {proc.returncode}."
                if stderr:
                    msg += f"\nStderr:\n{stderr}"
                logger.error(msg)
                self.error.emit(msg)
                return

            try:
                # solver_runner prints JSON; some versions may print twice — read first JSON object from stdout
                # attempt to parse stdout directly (robust to trailing logs)
                payload_text = stdout.strip()
                # sometimes solver prints two JSON dumps; try to parse last line
                lines = [ln for ln in payload_text.splitlines() if ln.strip()]
                if not lines:
                    raise ValueError("Solver returned empty output")
                last = lines[-1]
                grid = json.loads(last)
                if not isinstance(grid, dict):
                    raise ValueError("Solver output is not a dict")
                self.finished.emit(grid)
            except Exception as e:
                msg = f"Failed to parse solver output: {e}\nFull stdout:\n{stdout}\nStderr:\n{stderr}"
                logger.error(msg)
                self.error.emit(msg)
        except Exception as e:
            tb = traceback.format_exc()
            logger.critical("SubprocessWorker.run unhandled:\n%s", tb)
            try:
                self.error.emit(f"{e}\n{tb}")
            except Exception:
                logger.error("Failed to emit error signal:\n%s", traceback.format_exc())


class AdminDashboard(QtWidgets.QMainWindow):
    def __init__(self, services: dict, user: dict):
        super().__init__()
        self.services = services or {}
        self.user = user or {}
        self.setWindowTitle("Admin • Smart Timetable")
        # Increase overall window height so preview has more room
        self.resize(1280, 980)

        # state
        self._last_grid: Dict[str, List[str]] = {}
        self._last_section = "A"
        self._suppress_cell_change = False
        self._preview_edited = False

        # worker/thread refs
        self._tt_thread: Optional[QThread] = None
        self._tt_worker: Optional[SubprocessWorker] = None

        # generation state
        self._generation_in_progress = False

        # reference to login window when navigating
        self.login_window = None

        self.init_ui()
        self.apply_styles()

        # initial loads
        try:
            self.load_teacher_table()
            self.reload_teacher_choices()
            self.load_course_table()
            self.load_constraints_table()
        except Exception:
            logger.error("Initial load error:\n%s", traceback.format_exc())

    def init_ui(self):
        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        outer = QtWidgets.QVBoxLayout(central)
        outer.setContentsMargins(12, 12, 12, 12)
        outer.setSpacing(12)

        # Header
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(f"Welcome, {self.user.get('full_name') or self.user.get('username')}")
        title.setObjectName("header_title")
        header.addWidget(title)
        header.addStretch()

        # Right-aligned action buttons
        header_btns = QtWidgets.QHBoxLayout()
        settings_btn = QtWidgets.QPushButton("Settings")
        settings_btn.setObjectName("secondary")
        settings_btn.setFixedHeight(30)
        self.logout_btn = QtWidgets.QPushButton("Logout")
        self.logout_btn.clicked.connect(self.logout)
        self.logout_btn.setObjectName("secondary")
        self.logout_btn.setFixedHeight(30)
        header_btns.addWidget(settings_btn); header_btns.addWidget(self.logout_btn)
        header.addLayout(header_btns)

        outer.addLayout(header)

        # Top tabs
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setTabPosition(QtWidgets.QTabWidget.North)
        outer.addWidget(self.tabs)

        # ---------------- Teacher Management Tab ----------------
        self.teacher_tab = QtWidgets.QWidget()
        t_layout = QtWidgets.QVBoxLayout(self.teacher_tab)
        t_ctrl = QtWidgets.QHBoxLayout()
        self.create_teacher_btn = QtWidgets.QPushButton("Create Teacher")
        self.create_teacher_btn.clicked.connect(self.create_teacher_dialog)
        self.edit_teacher_btn = QtWidgets.QPushButton("Edit Teacher")
        self.edit_teacher_btn.clicked.connect(self.edit_teacher_dialog)
        self.delete_teacher_btn = QtWidgets.QPushButton("Delete Teacher")
        self.delete_teacher_btn.clicked.connect(self.delete_teacher)
        for b in (self.create_teacher_btn, self.edit_teacher_btn, self.delete_teacher_btn):
            b.setFixedHeight(34); b.setMinimumWidth(120)
        t_ctrl.addWidget(self.create_teacher_btn); t_ctrl.addWidget(self.edit_teacher_btn); t_ctrl.addWidget(self.delete_teacher_btn)
        t_ctrl.addStretch()
        t_layout.addLayout(t_ctrl)

        self.teacher_table = QtWidgets.QTableWidget(); self.teacher_table.setColumnCount(4)
        self.teacher_table.setHorizontalHeaderLabels(["ID", "Username", "Full Name", "Department"])
        self.teacher_table.horizontalHeader().setStretchLastSection(True)
        self.teacher_table.setSelectionBehavior(QtWidgets.QTableWidget.SelectRows)
        self.teacher_table.setEditTriggers(QtWidgets.QTableWidget.NoEditTriggers)
        self.teacher_table.setAlternatingRowColors(True)
        self.teacher_table.setMinimumHeight(320)
        t_layout.addWidget(self.teacher_table)
        self.tabs.addTab(self.teacher_tab, "Teacher Management")

        # ---------------- Timetable Generator Tab ----------------
        self.tt_tab = QtWidgets.QWidget()
        g_layout = QtWidgets.QVBoxLayout(self.tt_tab)
        g_layout.setContentsMargins(0,0,0,0)
        g_layout.setSpacing(8)

        # Top config row
        cfg_row = QtWidgets.QHBoxLayout()
        cfg_row.addWidget(QtWidgets.QLabel("Periods/day:"))
        self.periods_spin = QtWidgets.QSpinBox(); self.periods_spin.setRange(1, 12); self.periods_spin.setValue(6)
        self.periods_spin.valueChanged.connect(self.update_timeslot_preview)
        self.periods_spin.setFixedWidth(80)
        cfg_row.addWidget(self.periods_spin); cfg_row.addSpacing(12)

        cfg_row.addWidget(QtWidgets.QLabel("Lunch Period (P#):"))
        self.lunch_spin = QtWidgets.QSpinBox(); self.lunch_spin.setRange(0, 12); self.lunch_spin.setValue(0)
        self.lunch_spin.setFixedWidth(80)
        cfg_row.addWidget(self.lunch_spin); cfg_row.addSpacing(12)

        cfg_row.addWidget(QtWidgets.QLabel("Start time:"))
        self.start_time = QtWidgets.QTimeEdit(QtCore.QTime(9, 0)); self.start_time.setDisplayFormat("HH:mm"); self.start_time.setFixedWidth(100)
        cfg_row.addWidget(self.start_time)

        cfg_row.addWidget(QtWidgets.QLabel("Duration (min):"))
        self.duration_spin = QtWidgets.QSpinBox(); self.duration_spin.setRange(10, 180); self.duration_spin.setValue(50); self.duration_spin.setFixedWidth(80)
        cfg_row.addWidget(self.duration_spin)

        cfg_row.addStretch()
        self.generate_btn = QtWidgets.QPushButton("Generate")
        self.generate_btn.setObjectName("primary")
        self.generate_btn.setFixedHeight(36)
        self.generate_btn.clicked.connect(self.generate_preview)
        cfg_row.addWidget(self.generate_btn)
        self.cancel_gen_btn = QtWidgets.QPushButton("Cancel")
        self.cancel_gen_btn.setFixedHeight(36)
        self.cancel_gen_btn.setEnabled(False); self.cancel_gen_btn.clicked.connect(self.cancel_generation)
        cfg_row.addWidget(self.cancel_gen_btn)
        g_layout.addLayout(cfg_row)

        # Timeslot preview
        timeslot_row = QtWidgets.QHBoxLayout()
        timeslot_row.addWidget(QtWidgets.QLabel("Time slots:"))
        self.timeslot_preview = QtWidgets.QLabel("")
        font = QtGui.QFont(); font.setPointSize(10)
        self.timeslot_preview.setFont(font)
        timeslot_row.addWidget(self.timeslot_preview); timeslot_row.addStretch()
        g_layout.addLayout(timeslot_row)
        self.update_timeslot_preview()

        # Middle area: left course list, right constraints (compact)
        middle_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        middle_splitter.setHandleWidth(8)

        # Left: course add + table (compact)
        left_widget = QtWidgets.QWidget()
        left_widget.setMinimumWidth(220)
        left_layout = QtWidgets.QVBoxLayout(left_widget)
        add_row = QtWidgets.QHBoxLayout()
        self.course_code_input = QtWidgets.QLineEdit(); self.course_code_input.setPlaceholderText("Course Code"); self.course_code_input.setFixedWidth(90)
        self.course_name_input = QtWidgets.QLineEdit(); self.course_name_input.setPlaceholderText("Course Name")
        self.course_section_input = QtWidgets.QLineEdit(); self.course_section_input.setPlaceholderText("Section (A)"); self.course_section_input.setFixedWidth(80)
        self.course_teacher_input = QtWidgets.QComboBox(); self.course_teacher_input.setFixedWidth(160)
        add_btn = QtWidgets.QPushButton("Add"); add_btn.setFixedHeight(28); add_btn.clicked.connect(self.add_course_from_inputs)
        add_row.addWidget(self.course_code_input); add_row.addWidget(self.course_name_input); add_row.addWidget(self.course_section_input)
        add_row.addWidget(self.course_teacher_input); add_row.addWidget(add_btn); add_row.addStretch()
        left_layout.addLayout(add_row)

        self.course_table = QtWidgets.QTableWidget(); self.course_table.setColumnCount(6)
        self.course_table.setHorizontalHeaderLabels(["ID", "Code", "Name", "Section", "Teacher ID", "Actions"])
        self.course_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)
        self.course_table.cellChanged.connect(self.on_course_cell_changed)
        self.course_table.setAlternatingRowColors(True)
        # compact course table so preview gets most vertical space
        self.course_table.setMinimumHeight(100)
        left_layout.addWidget(self.course_table)
        middle_splitter.addWidget(left_widget)

        # Right: constraints (compact)
        right_widget = QtWidgets.QWidget()
        right_layout = QtWidgets.QVBoxLayout(right_widget)
        right_layout.addWidget(QtWidgets.QLabel("Constraints (one per line):"))
        self.constraints_edit = QtWidgets.QPlainTextEdit()
        self.constraints_edit.setPlaceholderText("Format: course_name,section(optional),Day,P1-P3  OR  course_name,Day,P2  (optional ,Exact at end)")
        self.constraints_edit.setMinimumHeight(80)
        right_layout.addWidget(self.constraints_edit)
        add_cons_btn = QtWidgets.QPushButton("Add Constraints"); add_cons_btn.clicked.connect(self.add_constraints)
        add_cons_btn.setFixedHeight(28)
        right_layout.addWidget(add_cons_btn, alignment=QtCore.Qt.AlignRight)

        self.constraints_table = QtWidgets.QTableWidget(); self.constraints_table.setColumnCount(6)
        self.constraints_table.setHorizontalHeaderLabels(["ID", "Course", "Section", "Day", "Period Range", "Actions"])
        self.constraints_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.constraints_table.setAlternatingRowColors(True)
        self.constraints_table.setMinimumHeight(120)
        right_layout.addWidget(self.constraints_table)

        middle_splitter.addWidget(right_widget)

        # Keep this splitter compact by limiting its vertical footprint
        middle_splitter.setMaximumHeight(260)
        g_layout.addWidget(middle_splitter)

        # --- Timetable preview header + table (no QScrollArea wrapper) ---
        header_lbl = QtWidgets.QLabel("Generated Timetable")
        header_lbl.setAlignment(QtCore.Qt.AlignCenter)
        header_lbl.setObjectName("preview_header")
        header_font = QtGui.QFont()
        header_font.setPointSize(12)
        header_font.setBold(True)
        header_lbl.setFont(header_font)
        g_layout.addWidget(header_lbl)

        self.preview_table = QtWidgets.QTableWidget()
        self.preview_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.preview_table.setAlternatingRowColors(True)
        # Make the preview fill remaining space; tuned to be large on typical screens.
        self.preview_table.setMinimumHeight(840)
        self.preview_table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Expanding)
        header_font2 = QtGui.QFont(); header_font2.setBold(True)
        self.preview_table.horizontalHeader().setFont(header_font2)
        self.preview_table.verticalHeader().setFont(header_font2)
        # Allow editing of generated timetable by double click; commit to _last_grid on edit
        self.preview_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)
        self.preview_table.itemChanged.connect(self.on_preview_item_changed)

        # Add preview_table directly into layout and give it large stretch
        g_layout.addWidget(self.preview_table, stretch=10)

        # Save/Export centered row
        se_container = QtWidgets.QWidget()
        se_layout = QtWidgets.QHBoxLayout(se_container)
        se_layout.setContentsMargins(0, 8, 0, 0)
        se_layout.addStretch()
        self.save_btn = QtWidgets.QPushButton("Save Timetable"); self.save_btn.setFixedHeight(34); self.save_btn.clicked.connect(self.save_timetable)
        self.export_btn = QtWidgets.QPushButton("Export PDF"); self.export_btn.setFixedHeight(34); self.export_btn.clicked.connect(self.export_timetable)
        se_layout.addWidget(self.save_btn)
        se_layout.addSpacing(12)
        se_layout.addWidget(self.export_btn)
        se_layout.addStretch()
        g_layout.addWidget(se_container)

        self.tabs.addTab(self.tt_tab, "Timetable Generator")

        # ---------------- Profile tab ----------------
        self.profile_tab = QtWidgets.QWidget()
        pf = QtWidgets.QFormLayout(self.profile_tab)
        pf.addRow("Username:", QtWidgets.QLabel(self.user.get('username') or ""))
        pf.addRow("Full Name:", QtWidgets.QLabel(self.user.get('full_name') or ""))
        pf.addRow("Role:", QtWidgets.QLabel(self.user.get('role') or ""))
        self.tabs.addTab(self.profile_tab, "Profile")

    def apply_styles(self):
        self.setStyleSheet("""
            QMainWindow { background: #f6f7fb; font-family: "Segoe UI", Roboto, Arial; color: #0f172a; }
            #header_title { font-size: 18px; font-weight: 700; color: #0b1220; padding: 6px 0; }
            #preview_header { color: #0b1220; margin-top:8px; margin-bottom:4px; }
            QPushButton#primary { background: #0b1220; color: white; padding: 6px 14px; border-radius: 6px; }
            QPushButton#secondary { background: #e6eef8; color: #0b1220; padding: 6px 10px; border-radius: 6px; }
            QPushButton { background: #ffffff; border: 1px solid #e1e8f0; border-radius: 6px; padding: 6px 10px; }
            QTableWidget { background: white; border: 1px solid #e6e9ee; gridline-color: #eef2f7; }
            QHeaderView::section { background: #f0f4fa; padding: 6px; border: none; color: #0b1220; }
            QLabel { color: #0b1220; }
            QPlainTextEdit { background: #ffffff; border: 1px solid #e6e9ee; padding: 8px; }
        """)

    # ---------------- Teacher / Course Management ----------------
    def logout(self):
        """
        Logout from admin dashboard and navigate to the login page.

        Attempts to instantiate LoginWindow with the current services dict (many LoginWindow
        constructors in the app expect services). Falls back to closing the app only if import fails.
        Also calls auth.logout() if an auth service is present.
        """
        try:
            # Import the login window class (adjust path if your login class lives elsewhere).
            from UI.login_window import LoginWindow  # change path if your login widget class is elsewhere

            # If auth service exposes a logout/clear method, call it to clear session
            try:
                auth = self.services.get("auth")
                if auth and hasattr(auth, "logout"):
                    try:
                        auth.logout()
                    except Exception:
                        logger.debug("auth.logout() raised -- continuing: %s", traceback.format_exc())
            except Exception:
                logger.debug("Auth service logout attempt failed: %s", traceback.format_exc())

            # Instantiate LoginWindow with services (many constructors expect services)
            try:
                self.login_window = LoginWindow(self.services)
            except TypeError:
                # fallback: try without services if the constructor signature is different
                try:
                    self.login_window = LoginWindow()
                except Exception as e:
                    logger.exception("Failed to create LoginWindow with or without services: %s", e)
                    # fallback to just close
                    try:
                        self.close()
                    except Exception:
                        pass
                    return

            # Show login window and close this dashboard
            self.login_window.show()
            self.close()
        except Exception as e:
            logger.exception("Failed to navigate to LoginWindow (%s). Falling back to close(): %s", type(e), e)
            try:
                self.close()
            except Exception:
                pass

    def create_teacher_dialog(self):
        dlg = QtWidgets.QDialog(self)
        dlg.setWindowTitle("Create Teacher"); dlg.setModal(True)
        dlg_layout = QtWidgets.QVBoxLayout(dlg); form = QtWidgets.QFormLayout(); form.setLabelAlignment(QtCore.Qt.AlignLeft)
        username_edit = QtWidgets.QLineEdit(); username_edit.setPlaceholderText("e.g. jsmith")
        full_name_edit = QtWidgets.QLineEdit(); full_name_edit.setPlaceholderText("Full name")
        password_edit = QtWidgets.QLineEdit(); password_edit.setEchoMode(QtWidgets.QLineEdit.Password); password_edit.setPlaceholderText("Password (leave blank for default)")
        email_edit = QtWidgets.QLineEdit(); email_edit.setPlaceholderText("Email (optional)")
        phone_edit = QtWidgets.QLineEdit(); phone_edit.setPlaceholderText("Phone (optional)")
        dept_edit = QtWidgets.QLineEdit(); dept_edit.setPlaceholderText("Department (optional)")
        form.addRow("Username:", username_edit); form.addRow("Full name:", full_name_edit); form.addRow("Password:", password_edit)
        form.addRow("Email:", email_edit); form.addRow("Phone:", phone_edit); form.addRow("Department:", dept_edit)
        dlg_layout.addLayout(form)
        btn_row = QtWidgets.QHBoxLayout(); btn_row.addStretch()
        cancel_btn = QtWidgets.QPushButton("Cancel"); submit_btn = QtWidgets.QPushButton("Create"); submit_btn.setDefault(True)
        btn_row.addWidget(cancel_btn); btn_row.addWidget(submit_btn); dlg_layout.addLayout(btn_row)

        def on_cancel(): dlg.reject()
        def on_submit():
            username = username_edit.text().strip(); full_name = full_name_edit.text().strip()
            password = password_edit.text().strip() or "teacher123"; email = email_edit.text().strip() or None
            phone = phone_edit.text().strip() or None; department = dept_edit.text().strip() or None
            if not username or not full_name:
                QMessageBox.warning(self, "Missing", "Username and Full name are required."); return
            try:
                auth = self.services.get('auth')
                if auth and hasattr(auth, 'create_user'):
                    user_id = auth.create_user(username, password, role="Teacher", full_name=full_name, email=email, phone=phone)
                else:
                    raise RuntimeError("Auth service missing create_user() method.")
                tsvc = self.services.get('teacher')
                if tsvc and hasattr(tsvc, 'create_teacher'):
                    tsvc.create_teacher(user_id, department)
                else:
                    dao = self.services.get('teacher_dao')
                    if not dao or not hasattr(dao, 'create'):
                        raise RuntimeError("Teacher service/DAO not available to create profile.")
                    dao.create(user_id, department)
                QMessageBox.information(self, "Created", f"Teacher '{full_name}' created successfully."); dlg.accept(); self.load_teacher_table(); self.reload_teacher_choices()
            except Exception:
                logger.error("Create teacher error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Failed to create teacher. See console for details.")

        cancel_btn.clicked.connect(on_cancel); submit_btn.clicked.connect(on_submit); dlg.exec_()

    def edit_teacher_dialog(self):
        row = self.teacher_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select", "Select a teacher to edit"); return
        try:
            t_id_item = self.teacher_table.item(row, 0)
            if not t_id_item:
                QMessageBox.warning(self, "Invalid", "Selected row is invalid."); return
            t_id = int(t_id_item.text())
        except Exception:
            QMessageBox.warning(self, "Invalid", "Selected row is invalid."); return

        dao = self.services.get('teacher_dao')
        current = {}
        if dao and hasattr(dao, 'get_by_id'):
            try:
                current = dao.get_by_id(t_id) or {}
            except Exception:
                logger.debug("teacher_dao.get_by_id failed, falling back to table values")

        curr_name = current.get('full_name') if current else (self.teacher_table.item(row, 2).text() if self.teacher_table.item(row, 2) else "")
        curr_dept = current.get('department') if current else (self.teacher_table.item(row, 3).text() if self.teacher_table.item(row, 3) else "")

        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Edit Teacher"); layout = QtWidgets.QVBoxLayout(dlg)
        form = QtWidgets.QFormLayout(); name_edit = QtWidgets.QLineEdit(curr_name or ""); dept_edit = QtWidgets.QLineEdit(curr_dept or "")
        form.addRow("Full name:", name_edit); form.addRow("Department:", dept_edit); layout.addLayout(form)
        btn_row = QtWidgets.QHBoxLayout(); btn_row.addStretch(); save_btn = QtWidgets.QPushButton("Save"); cancel_btn = QtWidgets.QPushButton("Cancel")
        btn_row.addWidget(cancel_btn); btn_row.addWidget(save_btn); layout.addLayout(btn_row)

        def on_cancel(): dlg.reject()
        def on_save():
            new_name = name_edit.text().strip(); new_dept = dept_edit.text().strip() or None
            try:
                tsvc = self.services.get('teacher')
                if tsvc and hasattr(tsvc, 'update'):
                    tsvc.update(t_id, new_name, new_dept)
                else:
                    if dao and hasattr(dao, 'update'):
                        dao.update(t_id, full_name=new_name, department=new_dept)
                    else:
                        raise RuntimeError("Teacher update API not available.")
                QMessageBox.information(self, "Updated", "Teacher updated"); dlg.accept(); self.load_teacher_table(); self.reload_teacher_choices()
            except Exception:
                logger.error("Edit teacher error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Update failed. See console for details.")

        cancel_btn.clicked.connect(on_cancel); save_btn.clicked.connect(on_save); dlg.exec_()

    def delete_teacher(self):
        row = self.teacher_table.currentRow()
        if row < 0:
            QMessageBox.warning(self, "Select", "Select a teacher to delete"); return
        try:
            t_id = int(self.teacher_table.item(row, 0).text())
        except Exception:
            QMessageBox.warning(self, "Invalid", "Selected row is invalid."); return
        confirm = QMessageBox.question(self, "Delete Teacher", f"Delete teacher id {t_id}? This will remove the user and profile.")
        if confirm != QtWidgets.QMessageBox.Yes and confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            tsvc = self.services.get('teacher')
            if tsvc and hasattr(tsvc, 'delete_teacher'):
                tsvc.delete_teacher(t_id)
            else:
                dao = self.services.get('teacher_dao')
                if dao and hasattr(dao, 'delete'):
                    dao.delete(t_id)
                else:
                    raise RuntimeError("Teacher delete API not available.")
            QMessageBox.information(self, "Deleted", "Teacher deleted"); self.load_teacher_table(); self.reload_teacher_choices()
        except Exception:
            logger.error("Delete teacher error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Delete failed. See console for details.")

    def load_teacher_table(self):
        teachers = []
        try:
            tsvc = self.services.get('teacher')
            if tsvc and hasattr(tsvc, 'list_teachers'):
                teachers = tsvc.list_teachers()
            else:
                dao = self.services.get('teacher_dao')
                if dao and hasattr(dao, 'list_all'):
                    teachers = dao.list_all()
        except Exception:
            logger.error("load_teacher_table error:\n%s", traceback.format_exc()); teachers = []
        self.teacher_table.setRowCount(len(teachers))
        for i, t in enumerate(teachers):
            self.teacher_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(t.get('id') or t.get('user_id') or "")))
            self.teacher_table.setItem(i, 1, QtWidgets.QTableWidgetItem(t.get('username') or ""))
            self.teacher_table.setItem(i, 2, QtWidgets.QTableWidgetItem(t.get('full_name') or ""))
            self.teacher_table.setItem(i, 3, QtWidgets.QTableWidgetItem(t.get('department') or ""))

    def reload_teacher_choices(self):
        self.course_teacher_input.clear(); self.course_teacher_input.addItem("Unassigned", None)
        teachers = []
        try:
            tsvc = self.services.get('teacher')
            if tsvc and hasattr(tsvc, 'list_teachers'):
                teachers = tsvc.list_teachers()
            else:
                dao = self.services.get('teacher_dao')
                if dao and hasattr(dao, 'list_all'):
                    teachers = dao.list_all()
        except Exception:
            logger.error("reload_teacher_choices error:\n%s", traceback.format_exc()); teachers = []
        for t in teachers:
            display = f"{t.get('id') or t.get('user_id')} - {t.get('full_name') or t.get('username')}"
            self.course_teacher_input.addItem(display, t.get('id') or t.get('user_id'))

    def add_course_from_inputs(self):
        code = self.course_code_input.text().strip(); name = self.course_name_input.text().strip()
        section = self.course_section_input.text().strip() or "A"
        teacher_id = self.course_teacher_input.currentData()
        if not code or not name:
            QMessageBox.warning(self, "Missing", "Course code and name required"); return
        try:
            course_svc = self.services.get('course')
            if course_svc and hasattr(course_svc, 'create_course'):
                try:
                    course_svc.create_course(name, code, credits=1, section=section, teacher_id=teacher_id)
                except TypeError:
                    course_svc.create_course(name, code, section, teacher_id)
            else:
                dao = self.services.get('course_dao')
                if dao and hasattr(dao, 'create'):
                    try:
                        dao.create(name, code, 1, section, teacher_id)
                    except TypeError:
                        dao.create(name, code, section, teacher_id)
                else:
                    raise RuntimeError("Course creation API not available.")
            QMessageBox.information(self, "Created", f"Course '{name}' added.")
        except Exception:
            logger.error("Add course error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Failed to add course. See console for details.")
        finally:
            try:
                self.load_course_table(); self.reload_teacher_choices()
            except Exception:
                logger.error("Refresh after add course failed:\n%s", traceback.format_exc())

    def load_course_table(self):
        courses = []
        try:
            course_svc = self.services.get('course')
            if course_svc and hasattr(course_svc, 'list_all'):
                courses = course_svc.list_all()
            else:
                dao = self.services.get('course_dao')
                if dao and hasattr(dao, 'list_all'):
                    courses = dao.list_all()
        except Exception:
            logger.error("load_course_table error:\n%s", traceback.format_exc()); courses = []

        self.course_table.blockSignals(True)
        try:
            self.course_table.setRowCount(len(courses))
            for i, c in enumerate(courses):
                self.course_table.setItem(i, 0, QtWidgets.QTableWidgetItem(str(c.get('id') or "")))
                self.course_table.setItem(i, 1, QtWidgets.QTableWidgetItem(c.get('course_code') or ""))
                self.course_table.setItem(i, 2, QtWidgets.QTableWidgetItem(c.get('course_name') or ""))
                self.course_table.setItem(i, 3, QtWidgets.QTableWidgetItem(c.get('section') or ""))
                self.course_table.setItem(i, 4, QtWidgets.QTableWidgetItem(str(c.get('teacher_id') or "")))
                w = QtWidgets.QWidget(); row_h = QtWidgets.QHBoxLayout(w); row_h.setContentsMargins(0, 0, 0, 0)
                edit_btn = QtWidgets.QPushButton("Edit"); edit_btn.setProperty("course_id", c.get('id')); edit_btn.clicked.connect(self.on_edit_course_clicked)
                del_btn = QtWidgets.QPushButton("Delete"); del_btn.setProperty("course_id", c.get('id')); del_btn.clicked.connect(self._on_delete_course_clicked)
                for btn in (edit_btn, del_btn):
                    btn.setFixedHeight(26); btn.setMinimumWidth(60)
                row_h.addWidget(edit_btn); row_h.addWidget(del_btn); self.course_table.setCellWidget(i, 5, w)
        finally:
            self.course_table.blockSignals(False)

    def on_edit_course_clicked(self):
        """
        Open edit dialog for the selected course (sender is the Edit button).
        Uses course_dao.get_by_id and dao.update if available, otherwise warns.
        """
        btn = self.sender()
        if not btn:
            return
        course_id = btn.property("course_id")
        if not course_id:
            return
        try:
            dao = self.services.get('course_dao')
            if not dao or not hasattr(dao, 'get_by_id'):
                QMessageBox.warning(self, "Not available", "Course DAO not available for editing")
                return
            course = dao.get_by_id(course_id) or {}
            dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Edit Course"); layout = QtWidgets.QVBoxLayout(dlg)
            form = QtWidgets.QFormLayout()
            code_edit = QtWidgets.QLineEdit(course.get('course_code') or ""); name_edit = QtWidgets.QLineEdit(course.get('course_name') or "")
            section_edit = QtWidgets.QLineEdit(course.get('section') or ""); teacher_cb = QtWidgets.QComboBox(); teacher_cb.addItem("Unassigned", None)
            # populate teachers
            teachers = []
            try:
                tsvc = self.services.get('teacher')
                if tsvc and hasattr(tsvc, 'list_teachers'):
                    teachers = tsvc.list_teachers()
                else:
                    tdao = self.services.get('teacher_dao')
                    if tdao and hasattr(tdao, 'list_all'):
                        teachers = tdao.list_all()
            except Exception:
                logger.debug("Failed to load teachers for edit dialog:\n%s", traceback.format_exc())
            for t in teachers:
                teacher_cb.addItem(f"{t.get('id')} - {t.get('full_name')}", t.get('id'))
            if course.get('teacher_id') is not None:
                idx = teacher_cb.findData(course.get('teacher_id'))
                if idx >= 0:
                    teacher_cb.setCurrentIndex(idx)
            form.addRow("Code:", code_edit); form.addRow("Name:", name_edit); form.addRow("Section:", section_edit); form.addRow("Teacher:", teacher_cb)
            layout.addLayout(form)
            btn_row = QtWidgets.QHBoxLayout(); btn_row.addStretch(); save_btn = QtWidgets.QPushButton("Save"); cancel_btn = QtWidgets.QPushButton("Cancel")
            btn_row.addWidget(cancel_btn); btn_row.addWidget(save_btn); layout.addLayout(btn_row)

            def on_cancel(): dlg.reject()
            def on_save():
                new_code = code_edit.text().strip(); new_name = name_edit.text().strip(); new_section = section_edit.text().strip()
                new_teacher = teacher_cb.currentData()
                try:
                    existing = dao.get_by_code(new_code) if hasattr(dao, 'get_by_code') else None
                    if existing and existing.get('id') != course_id:
                        QMessageBox.warning(self, "Duplicate", "Course code already exists"); return
                    # Try updating with common kwargs; dao.update signature may vary
                    try:
                        dao.update(course_id, course_name=new_name, course_code=new_code, section=new_section, teacher_id=new_teacher)
                    except TypeError:
                        # fallback: positional or other signature; attempt minimal update
                        if hasattr(dao, 'update'):
                            dao.update(course_id, new_name)
                    dlg.accept(); self.load_course_table()
                except Exception:
                    logger.error("Edit course error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Update failed. See console for details.")

            cancel_btn.clicked.connect(on_cancel); save_btn.clicked.connect(on_save)
            dlg.exec_()
        except Exception:
            logger.error("on_edit_course_clicked error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Failed to open edit dialog. See console for details.")

    def on_course_cell_changed(self, row, col):
        if self._suppress_cell_change:
            return
        try:
            self._suppress_cell_change = True
            course_id_item = self.course_table.item(row, 0)
            if not course_id_item:
                return
            course_id = int(course_id_item.text())
            val_item = self.course_table.item(row, col)
            if val_item is None:
                return
            val = val_item.text()
            dao = self.services.get('course_dao')
            if dao is None:
                QMessageBox.warning(self, "Missing", "Course DAO unavailable for inline update"); return
            # columns: 0 ID,1 Code,2 Name,3 Section,4 TeacherID,5 Actions
            if col == 1:
                existing = dao.get_by_code(val) if hasattr(dao, 'get_by_code') else None
                if existing and existing.get('id') != course_id:
                    QMessageBox.warning(self, "Duplicate", "Course code already exists"); self.load_course_table(); return
                dao.update(course_id, course_code=val)
            elif col == 2:
                dao.update(course_id, course_name=val)
            elif col == 3:
                dao.update(course_id, section=val)
            elif col == 4:
                tid = int(val) if val.isdigit() else None
                dao.update(course_id, teacher_id=tid)
        except Exception:
            logger.error("on_course_cell_changed error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Failed to update course. See console for details.")
        finally:
            self._suppress_cell_change = False

    def _on_delete_course_clicked(self):
        btn = self.sender(); course_id = btn.property("course_id")
        if not course_id:
            return
        confirm = QMessageBox.question(self, "Delete course", f"Delete course id {course_id}? This cannot be undone.")
        if confirm != QtWidgets.QMessageBox.Yes and confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            dao = self.services.get('course_dao')
            if dao and hasattr(dao, 'delete'):
                dao.delete(course_id)
            else:
                svc = self.services.get('course')
                if svc and hasattr(svc, 'delete'):
                    svc.delete(course_id)
                else:
                    raise RuntimeError("Course delete API not available.")
            QMessageBox.information(self, "Deleted", "Course deleted"); self.load_course_table()
        except Exception:
            logger.error("_on_delete_course_clicked error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Delete failed. See console for details.")

    # ---------------- Constraints table methods ----------------
    def load_constraints_table(self):
        items = []
        try:
            svc = self.services.get("constraint")
            if svc and hasattr(svc, "list_constraints"):
                items = svc.list_constraints()
            else:
                dao = self.services.get('constraint_dao')
                if dao and hasattr(dao, 'list_all'):
                    items = dao.list_all()
        except Exception:
            logger.error("Failed to load constraints for table:\n%s", traceback.format_exc()); items = []

        self.constraints_table.blockSignals(True)
        try:
            self.constraints_table.setRowCount(len(items))
            for r, c in enumerate(items):
                cid = c.get("id") or ""
                course = (c.get("course_name") or c.get("course_code") or "").strip()
                section = (c.get("section") or "ALL").strip()
                day = (c.get("day") or "").strip()
                pr = (c.get("period_range") or c.get("periods") or "").strip()
                self.constraints_table.setItem(r, 0, QtWidgets.QTableWidgetItem(str(cid)))
                self.constraints_table.setItem(r, 1, QtWidgets.QTableWidgetItem(course))
                self.constraints_table.setItem(r, 2, QtWidgets.QTableWidgetItem(section))
                self.constraints_table.setItem(r, 3, QtWidgets.QTableWidgetItem(day))
                self.constraints_table.setItem(r, 4, QtWidgets.QTableWidgetItem(pr))
                # Actions: Delete button
                w = QtWidgets.QWidget(); row_h = QtWidgets.QHBoxLayout(w); row_h.setContentsMargins(0, 0, 0, 0)
                del_btn = QtWidgets.QPushButton("Delete"); del_btn.setProperty("constraint_id", c.get("id"))
                del_btn.setFixedHeight(26); del_btn.setMinimumWidth(70); del_btn.clicked.connect(self._on_delete_constraint_clicked)
                row_h.addWidget(del_btn)
                row_h.addStretch()
                self.constraints_table.setCellWidget(r, 5, w)
                # make cells non-editable
                for col_idx in range(0, 5):
                    item = self.constraints_table.item(r, col_idx)
                    if item:
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
        finally:
            self.constraints_table.blockSignals(False)

    def _on_delete_constraint_clicked(self):
        """
        Delete a constraint when the per-row Delete button is clicked.
        Prefer service.delete_constraint(), fallback to dao.delete().
        """
        btn = self.sender()
        if not btn:
            return
        c_id = btn.property("constraint_id")
        if not c_id:
            return

        confirm = QMessageBox.question(self, "Delete constraint", f"Delete constraint id {c_id}?")
        if confirm != QtWidgets.QMessageBox.Yes and confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return

        # 1) Try service.delete_constraint(constraint_id)
        try:
            svc = self.services.get("constraint")
            if svc and hasattr(svc, "delete_constraint"):
                svc.delete_constraint(c_id)
                logger.debug("Deleted constraint %s using constraint.delete_constraint()", c_id)
                QMessageBox.information(self, "Deleted", "Constraint deleted")
                self.load_constraints_table()
                return
        except Exception as e:
            logger.warning("_on_delete_constraint_clicked: constraint service.delete_constraint failed: %s\n%s", e, traceback.format_exc())

        # 2) Fallback to DAO.delete(constraint_id)
        try:
            dao = self.services.get("constraint_dao")
            if dao:
                if hasattr(dao, "delete"):
                    dao.delete(c_id)
                    logger.debug("Deleted constraint %s using constraint_dao.delete()", c_id)
                    QMessageBox.information(self, "Deleted", "Constraint deleted")
                    self.load_constraints_table()
                    return
                # also accept alternate DAO names if you prefer
                if hasattr(dao, "remove"):
                    dao.remove(c_id)
                    logger.debug("Deleted constraint %s using constraint_dao.remove()", c_id)
                    QMessageBox.information(self, "Deleted", "Constraint deleted")
                    self.load_constraints_table()
                    return
        except Exception as e:
            logger.warning("_on_delete_constraint_clicked: constraint_dao delete/remove failed: %s\n%s", e, traceback.format_exc())

        # Nothing worked
        logger.error("_on_delete_constraint_clicked error: no delete API available for constraint id=%s", c_id)
        QMessageBox.critical(self, "Error", "Constraint delete API not available. Ensure ConstraintService.delete_constraint or ConstraintDAO.delete exists.")
    
    def add_constraints(self):
        raw = self.constraints_edit.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "Missing", "Enter constraint lines"); return
        svc = self.services.get('constraint'); dao = self.services.get('constraint_dao')
        errors = []
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                if svc and hasattr(svc, 'add_from_text'):
                    # admin/global constraints -> use service
                    svc.add_from_text(ln)
                elif dao and hasattr(dao, 'add'):
                    parts = [p.strip() for p in ln.split(",")]
                    if len(parts) == 3:
                        course_name, day, pr = parts; section = "ALL"; mode = None
                    elif len(parts) == 4:
                        # If second token is day, treat as (course, day, pr, mode) otherwise treat as (course, section, day, pr)
                        if parts[1].capitalize() in ["Monday","Tuesday","Wednesday","Thursday","Friday"]:
                            course_name, day, pr, mode = parts; section = "ALL"
                        else:
                            course_name, section, day, pr = parts; mode = None
                    elif len(parts) == 5:
                        course_name, section, day, pr, mode = parts
                    else:
                        raise ValueError("Bad format")
                    day = day.strip().capitalize()
                    if mode and mode.strip().lower() in ("exact","block","full"):
                        dao.add(course_name.strip(), section.strip() or "ALL", day, pr.strip(), "Exact", mode.strip())
                    else:
                        dao.add(course_name.strip(), section.strip() or "ALL", day, pr.strip(), "Hard", None)
                else:
                    raise RuntimeError("Constraint service/DAO not available.")
            except Exception as e:
                errors.append(f"{ln} -> {e}")
        if errors:
            QMessageBox.warning(self, "Errors", "\n".join(errors))
        else:
            QMessageBox.information(self, "Done", "Constraints added")
            try:
                self.constraints_edit.clear()
            except Exception:
                pass
            try:
                self.load_constraints_table()
            except Exception:
                logger.error("Failed to refresh constraints table:\n%s", traceback.format_exc())

    # ---------------- Timetable generation helpers ----------------
    def compute_timeslots(self, periods: int, start: QtCore.QTime, duration_min: int) -> List[str]:
        slots: List[str] = []
        t = QtCore.QTime(start.hour(), start.minute())
        for _ in range(periods):
            end = t.addSecs(duration_min * 60)
            slots.append(f"{t.toString('HH:mm')}-{end.toString('HH:mm')}")
            t = end
        return slots

    def update_timeslot_preview(self):
        periods = self.periods_spin.value(); start = self.start_time.time(); duration = self.duration_spin.value()
        slots = self.compute_timeslots(periods, start, duration)
        self.timeslot_preview.setText(" | ".join([f"P{idx+1}: {s}" for idx, s in enumerate(slots)]))

    def _build_placeholder_grid(self, periods: int, lunch: int) -> Dict[str, List[str]]:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        try:
            course_svc = self.services.get('course')
            if course_svc and hasattr(course_svc, 'list_all'):
                courses = course_svc.list_all()
            else:
                dao = self.services.get('course_dao')
                courses = dao.list_all() if dao and hasattr(dao, 'list_all') else []
            labels = [ (c.get('course_name') or c.get('course_code') or f"C{c.get('id')}").strip() for c in courses ] or ["Free"]
        except Exception:
            logger.error("Failed to load courses for placeholder:\n%s", traceback.format_exc()); labels = ["Free"]
        grid = {}
        idx = 0
        for d in days:
            row = []
            for p in range(periods):
                if lunch and (p + 1) == int(lunch):
                    row.append("LUNCH")
                else:
                    row.append(labels[idx % len(labels)])
                    idx += 1
            grid[d] = row
        return grid

    def _build_course_label_map(self) -> Dict:
        mapping = {}
        try:
            course_list = []
            course_svc = self.services.get('course')
            if course_svc and hasattr(course_svc, 'list_all'):
                course_list = course_svc.list_all()
            else:
                dao = self.services.get('course_dao')
                if dao and hasattr(dao, 'list_all'):
                    course_list = dao.list_all()
        except Exception:
            logger.error("Failed to load courses for label map:\n%s", traceback.format_exc()); course_list = []
        for c in course_list:
            cid = c.get('id')
            code = (c.get('course_code') or "").strip()
            name = (c.get('course_name') or "").strip()
            display = name if name else code if code else f"Course-{cid}"
            if cid is not None:
                mapping[str(cid)] = display; mapping[cid] = display
            if code:
                mapping[code] = display
            if name:
                mapping[name] = name
        return mapping

    def _normalize_grid_labels(self, grid: dict) -> dict:
        # Runner already returns "Subject - Teacher" if teacher provided; keep as-is
        if not isinstance(grid, dict):
            return grid
        normalized = {}
        for day, row in grid.items():
            new_row = []
            for cell in row:
                if cell is None:
                    new_row.append(""); continue
                if isinstance(cell, str) and cell.strip().upper() == "LUNCH":
                    new_row.append("LUNCH"); continue
                new_row.append(str(cell))
            normalized[day] = new_row
        return normalized

    def _parse_period_range(self, pr: str) -> List[int]:
        pr = pr.strip().upper()
        if "-" in pr:
            a, b = pr.split("-", 1)
            try:
                start = int(a.strip().lstrip("P")); end = int(b.strip().lstrip("P"))
                if start > end: start, end = end, start
                return list(range(start, end + 1))
            except Exception:
                return []
        else:
            try:
                return [int(pr.lstrip("P"))]
            except Exception:
                return []

    def _validate_grid_constraints(self, grid: dict):
        msgs = []; ok = True
        try:
            dao = self.services.get('constraint_dao')
            if not dao or not hasattr(dao, 'list_all'):
                return True, []
            constraints = dao.list_all()
        except Exception:
            logger.error("_validate_grid_constraints: failed to load constraints:\n%s", traceback.format_exc()); return True, []
        grid_norm = {str(d).strip().capitalize(): row for d, row in grid.items()}

        def _normalize_text(s):
            if not isinstance(s, str):
                return ""
            return " ".join(s.split()).strip().lower()

        for c in constraints:
            try:
                c_type = (c.get('type') or "Hard").strip()
                if c_type.upper() not in ("HARD", "EXACT"):
                    continue
                course_name = (c.get('course_name') or "").strip(); section = (c.get('section') or "ALL").strip()
                day = (c.get('day') or "").strip().capitalize(); pr = (c.get('period_range') or "").strip()
                if not course_name or not day or not pr: continue
                if section.upper() != "ALL" and section != self._last_section: continue
                periods = self._parse_period_range(pr)
                if not periods: continue
                row = grid_norm.get(day)
                if row is None:
                    msgs.append(f"Constraint for {course_name} on {day} ({pr}) — day missing"); ok = False; continue
                found = False
                target = _normalize_text(course_name)
                for p in periods:
                    idx = p - 1
                    if 0 <= idx < len(row):
                        cell = row[idx]
                        if not cell:
                            continue
                        subj = str(cell).split(" - ")[0].strip()
                        subj_norm = _normalize_text(subj)
                        # tolerant compare: exact, containment both ways
                        if subj_norm == target or target in subj_norm or subj_norm in target:
                            found = True; break
                if not found:
                    # gather what is present in those slots for better debugging
                    present = []
                    for p in periods:
                        idx = p - 1
                        if 0 <= idx < len(row):
                            present.append(str(row[idx] or ""))
                    msgs.append(f"Hard constraint violated: '{course_name}' expected on {day} in {pr}. Found: {present}")
                    ok = False
            except Exception:
                logger.error("Error validating constraint %s:\n%s", c, traceback.format_exc())
        return ok, msgs

    def _locate_solver_runner(self) -> str:
        # Try a list of likely candidate filenames and locations, including a common misspelling
        candidates = [
            ("tools/solver_runner.py", Path.cwd() / "tools" / "solver_runner.py"),
            ("tools/solver_runner.py (repo)", Path(__file__).resolve().parent.parent / "tools" / "solver_runner.py"),
            ("solver_runner.py", Path.cwd() / "solver_runner.py"),
            ("solver_runner.py (repo)", Path(__file__).resolve().parent.parent / "solver_runner.py"),
            # common misspelling some environments may use
            ("tools/solve_runner.py", Path.cwd() / "tools" / "solve_runner.py"),
            ("solve_runner.py", Path.cwd() / "solve_runner.py"),
            # neighbors relative to where the process was started
            ("argv_dir/tools/solver_runner.py", Path(sys.argv[0]).resolve().parent / "tools" / "solver_runner.py"),
            ("argv_dir/solver_runner.py", Path(sys.argv[0]).resolve().parent / "solver_runner.py"),
        ]
        checked = []
        for name, p in candidates:
            try:
                checked.append(str(p))
                if p.exists():
                    logger.debug("_locate_solver_runner: found runner at %s (candidate %s)", p, name)
                    return str(p)
            except Exception:
                pass
        # If none found, return the default expected path (legacy) and log checked candidates
        logger.debug("_locate_solver_runner: none of the candidates exist. Checked: %s", checked)
        # default fallback to expected canonical path (used in older installs)
        return os.path.join("tools", "solver_runner.py")
    
    def _resolve_constraint_course_name(self, constraint: dict, course_label_map: Dict[str, str]) -> Optional[str]:
        """
        Try to resolve the free-text constraint['course_name'] to a canonical course label from course_label_map.
        course_label_map keys are normalized strings (lowercase, collapsed spaces) and values are the canonical label.
        Returns the canonical label if matched, otherwise returns None.
        """
        try:
            cname = (constraint.get("course_name") or "").strip()
            if not cname:
                return None
            norm = " ".join(cname.split()).strip().lower()
            # 1) direct exact match
            if norm in course_label_map:
                return course_label_map[norm]
            # 2) try containment matches (target in name or name in target)
            for k, v in course_label_map.items():
                if norm in k or k in norm:
                    return v
            # 3) try numeric/code match (if constraint contains digits and code exists)
            if any(ch.isdigit() for ch in norm):
                for k, v in course_label_map.items():
                    if any(char.isdigit() for char in k) and (norm in k or k in norm):
                        return v
        except Exception:
            logger.debug("_resolve_constraint_course_name error:\n%s", traceback.format_exc())
        return None

    def generate_preview(self):
        logger.debug("generate_preview: scheduled start (subprocess)")
        self._generation_in_progress = True
        self.generate_btn.setEnabled(False)
        self.cancel_gen_btn.setEnabled(False)

        periods = int(self.periods_spin.value()); lunch = int(self.lunch_spin.value()); time_limit_seconds = 20

        # fetch courses and attach teacher_name for runner convenience
        try:
            courses = []
            course_svc = self.services.get('course')
            if course_svc and hasattr(course_svc, 'list_all'):
                courses = course_svc.list_all()
            else:
                dao = self.services.get('course_dao')
                if dao and hasattr(dao, 'list_all'):
                    courses = dao.list_all()
        except Exception:
            logger.error("Failed to load courses for payload:\n%s", traceback.format_exc()); courses = []

        # build canonical course label map (normalized -> canonical)
        course_label_map = {}
        try:
            for c in courses:
                name = (c.get('course_name') or "").strip()
                code = (c.get('course_code') or "").strip()
                # choose display label preference: name, then code
                label = name if name else code if code else f"C{c.get('id')}"
                norm_name = " ".join(label.split()).strip().lower()
                course_label_map[norm_name] = label
                # also add canonical keys for raw name and code separately
                if name:
                    course_label_map[" ".join(name.split()).strip().lower()] = label
                if code:
                    course_label_map[" ".join(code.split()).strip().lower()] = label
        except Exception:
            logger.debug("Failed to build course_label_map:\n%s", traceback.format_exc())

        # attach teacher names (kept for compatibility but not used in display)
        teacher_map = {}
        try:
            tsvc = self.services.get('teacher')
            if tsvc and hasattr(tsvc, 'list_teachers'):
                for t in tsvc.list_teachers():
                    teacher_map[t.get('id')] = t.get('full_name')
            else:
                tdao = self.services.get('teacher_dao')
                if tdao and hasattr(tdao, 'list_all'):
                    for t in tdao.list_all():
                        teacher_map[t.get('id')] = t.get('full_name')
        except Exception:
            logger.debug("Could not load teacher names for payload")

        for c in courses:
            tid = c.get('teacher_id') or c.get('teacher') or None
            c['teacher_name'] = teacher_map.get(tid, "")

        # load constraints (raw from DAO/service)
        try:
            constraints = []
            cons_svc = self.services.get('constraint')
            if cons_svc and hasattr(cons_svc, 'list_constraints'):
                constraints = cons_svc.list_constraints()
            else:
                cons_dao = self.services.get('constraint_dao')
                if cons_dao and hasattr(cons_dao, 'list_all'):
                    constraints = cons_dao.list_all()
        except Exception:
            logger.error("Failed to load constraints for payload:\n%s", traceback.format_exc()); constraints = []

        # Normalize and resolve constraint course names against course_label_map
        cleaned_constraints = []
        try:
            for c in constraints:
                cleaned = dict(c)  # shallow copy
                for k in ("course_name", "section", "day", "period_range", "type", "mode", "description"):
                    if k in cleaned and isinstance(cleaned[k], str):
                        cleaned[k] = " ".join(cleaned[k].split())
                # If course_name does not exactly match any canonical course label, try to resolve
                resolved = self._resolve_constraint_course_name(cleaned, course_label_map)
                if resolved:
                    # replace with canonical label so solver matching works reliably
                    cleaned['course_name'] = resolved
                    logger.debug("Resolved constraint course '%s' -> '%s'", c.get('course_name'), resolved)
                else:
                    logger.debug("Constraint course '%s' could not be resolved to a course label", c.get('course_name'))
                cleaned_constraints.append(cleaned)
        except Exception:
            logger.error("Failed to normalize/resolve constraints:\n%s", traceback.format_exc())
            # fallback: use raw list
            cleaned_constraints = constraints

        payload = {"courses": courses, "constraints": cleaned_constraints, "periods": periods, "lunch": lunch, "time_limit": time_limit_seconds}

        # Debug: log the payload constraints so we can verify what is sent to solver
        try:
            logger.debug("Constraints payload (for debug): %s", json.dumps(cleaned_constraints, indent=2))
        except Exception:
            logger.debug("Constraints payload (debug) (non-serializable): %s", str(cleaned_constraints))

        # show placeholder immediately
        try:
            placeholder = self._build_placeholder_grid(periods, lunch)
            # display editable placeholder to allow manual edits
            self._on_generation_finished(placeholder)
        except Exception:
            logger.error("Failed to build placeholder:\n%s", traceback.format_exc())

        runner = self._locate_solver_runner()
        if not Path(runner).exists():
            QMessageBox.warning(self, "Runner missing", f"Solver runner not found at: {runner}\nSearched typical locations. Make sure tools/solver_runner.py (or tools/solve_runner.py) exists.")
            self.generate_btn.setEnabled(True); self._generation_in_progress = False; return

        # spawn worker thread
        self._tt_thread = QThread()
        self._tt_worker = SubprocessWorker(runner, payload, time_limit_seconds)
        self._tt_worker.moveToThread(self._tt_thread)
        self.cancel_gen_btn.setEnabled(True)
        self._tt_thread.started.connect(self._tt_worker.run)

        def _on_finished(grid):
            try:
                grid = self._normalize_grid_labels(grid)
                ok, msgs = self._validate_grid_constraints(grid)
                if not ok:
                    text = "Hard constraints were violated by the solver result:\n\n" + "\n".join(msgs)
                    logger.warning(text)
                    resp = QMessageBox.question(self, "Constraints violated", text + "\n\nAccept timetable anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if resp == QtWidgets.QMessageBox.Yes:
                        self._on_generation_finished_replace(grid)
                    else:
                        QMessageBox.information(self, "Kept placeholder", "Kept placeholder timetable. Adjust and try again.")
                else:
                    self._on_generation_finished_replace(grid)
            except Exception:
                logger.error("_on_finished handler error:\n%s", traceback.format_exc())
            finally:
                try:
                    if self._tt_thread:
                        self._tt_thread.quit(); self._tt_thread.wait(2000)
                except Exception:
                    logger.error("Thread quit/wait failed:\n%s", traceback.format_exc())
                try:
                    if self._tt_worker:
                        self._tt_worker.deleteLater()
                    if self._tt_thread:
                        self._tt_thread.deleteLater()
                except Exception:
                    pass
                self._tt_worker = None; self._tt_thread = None
                self.generate_btn.setEnabled(True); self.cancel_gen_btn.setEnabled(False)
                self._generation_in_progress = False
                logger.debug("generate_preview (subprocess): finished and UI updated")

        def _on_error(msg):
            logger.error("Solver subprocess error:\n%s", msg)
            QMessageBox.critical(self, "Generation Error", f"Generation failed:\n{msg}\n\nCheck console for details.")
            try:
                if self._tt_thread:
                    self._tt_thread.quit(); self._tt_thread.wait(2000)
            except Exception:
                pass
            try:
                if self._tt_worker:
                    self._tt_worker.deleteLater()
                if self._tt_thread:
                    self._tt_thread.deleteLater()
            except Exception:
                pass
            self._tt_worker = None; self._tt_thread = None
            self.generate_btn.setEnabled(True); self.cancel_gen_btn.setEnabled(False)
            self._generation_in_progress = False

        self._tt_worker.finished.connect(_on_finished)
        self._tt_worker.error.connect(_on_error)
        self._tt_thread.start()

    def _strip_teacher_label(self, text: str) -> str:
        if not isinstance(text, str):
            return ""
        # If solver returns "Course - Teacher", show only "Course"
        parts = text.split(" - ")
        return parts[0].strip() if parts else text.strip()

    def _on_generation_finished_replace(self, grid: dict):
        try:
            periods = int(self.periods_spin.value())
            slots = self.compute_timeslots(periods, self.start_time.time(), self.duration_spin.value())
            days = list(grid.keys()) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            self.preview_table.blockSignals(True)
            try:
                self.preview_table.clear()
                self.preview_table.setRowCount(len(days)); self.preview_table.setColumnCount(periods)
                self.preview_table.setVerticalHeaderLabels(days)
                headers = [f"P{i+1}\n{slots[i]}" for i in range(periods)]
                self.preview_table.setHorizontalHeaderLabels(headers)
                display_grid = {}
                for i, day in enumerate(days):
                    row_vals = grid.get(day, [""] * periods)
                    if len(row_vals) < periods:
                        row_vals = row_vals + [""] * (periods - len(row_vals))
                    display_row = []
                    for j in range(periods):
                        txt = row_vals[j] or ""
                        # strip teacher suffix if present and show only course label
                        if isinstance(txt, str) and txt.strip().upper() == "LUNCH":
                            display_txt = "LUNCH"
                        else:
                            display_txt = self._strip_teacher_label(txt)
                        item = QtWidgets.QTableWidgetItem(str(display_txt))
                        # Allow editing so admin can adjust manually
                        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
                        self.preview_table.setItem(i, j, item)
                        display_row.append(str(display_txt))
                    display_grid[day] = display_row
            finally:
                self.preview_table.blockSignals(False)
            # store the human-display grid (no teacher suffixes) so edits map directly
            self._last_grid = display_grid
            self.update_timeslot_preview()
            self.preview_table.repaint()
            logger.debug("_on_generation_finished_replace: UI updated with grid")
        except Exception:
            logger.error("_on_generation_finished_replace error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Failed to display timetable. See console for details.")

    def _on_generation_finished(self, grid: dict):
        try:
            periods = int(self.periods_spin.value()); slots = self.compute_timeslots(periods, self.start_time.time(), self.duration_spin.value())
            days = list(grid.keys()) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            self.preview_table.blockSignals(True)
            try:
                self.preview_table.clear()
                self.preview_table.setRowCount(len(days)); self.preview_table.setColumnCount(periods)
                self.preview_table.setVerticalHeaderLabels(days)
                headers = [f"P{i+1}\n{slots[i]}" for i in range(periods)]
                self.preview_table.setHorizontalHeaderLabels(headers)
                display_grid = {}
                for i, day in enumerate(days):
                    row_vals = grid.get(day, [""] * periods)
                    if len(row_vals) < periods:
                        row_vals = row_vals + [""] * (periods - len(row_vals))
                    display_row = []
                    for j in range(periods):
                        txt = row_vals[j] or ""
                        if isinstance(txt, str) and txt.strip().upper() == "LUNCH":
                            display_txt = "LUNCH"
                        else:
                            display_txt = self._strip_teacher_label(txt)
                        item = QtWidgets.QTableWidgetItem(str(display_txt))
                        # make initial placeholder editable
                        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
                        self.preview_table.setItem(i, j, item)
                        display_row.append(str(display_txt))
                    display_grid[day] = display_row
            finally:
                self.preview_table.blockSignals(False)
            self._last_grid = display_grid
            self.update_timeslot_preview()
            self.preview_table.repaint()
            logger.debug("_on_generation_finished: UI updated with placeholder grid")
        except Exception:
            logger.error("_on_generation_finished error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Failed to display timetable. See console for details.")
        finally:
            self.generate_btn.setEnabled(True); self.cancel_gen_btn.setEnabled(False)

    def _update_subject_teacher_list(self):
        # Method retained for compatibility but emptied because subject-teacher display was removed.
        return

    def on_preview_item_changed(self, item: QtWidgets.QTableWidgetItem):
        # Update internal grid representation when user edits a preview cell
        try:
            if self._suppress_cell_change:
                return
            row = item.row()
            col = item.column()
            txt = item.text()
            # update _last_grid structure (days as keys in order)
            try:
                days = list(self._last_grid.keys()) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            except Exception:
                days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            # ensure grid has same dimensions as preview
            if not self._last_grid or set(days) != set([self.preview_table.verticalHeaderItem(r).text() for r in range(self.preview_table.rowCount())]):
                # rebuild _last_grid from preview content
                new_grid = {}
                for r in range(self.preview_table.rowCount()):
                    day = self.preview_table.verticalHeaderItem(r).text()
                    row_vals = []
                    for c in range(self.preview_table.columnCount()):
                        it = self.preview_table.item(r, c)
                        row_vals.append(it.text() if it else "")
                    new_grid[day] = row_vals
                self._last_grid = new_grid
                self._preview_edited = True
                return
            day = self.preview_table.verticalHeaderItem(row).text()
            # ensure list exists
            if day not in self._last_grid:
                self._last_grid[day] = [""] * self.preview_table.columnCount()
            vals = list(self._last_grid[day])
            # extend if necessary
            if len(vals) < self.preview_table.columnCount():
                vals += [""] * (self.preview_table.columnCount() - len(vals))
            vals[col] = txt
            self._last_grid[day] = vals
            self._preview_edited = True
        except Exception:
            logger.error("on_preview_item_changed error:\n%s", traceback.format_exc())

    def _ensure_constraints_loaded_for_debug(self):
        try:
            cons = []
            cons_svc = self.services.get('constraint')
            if cons_svc and hasattr(cons_svc, 'list_constraints'):
                cons = cons_svc.list_constraints()
            else:
                cons_dao = self.services.get('constraint_dao')
                if cons_dao and hasattr(cons_dao, 'list_all'):
                    cons = cons_dao.list_all()
            logger.debug("Constraints payload (for debug): %s", cons)
            return cons
        except Exception:
            logger.debug("Failed to fetch constraints for debug payload:\n%s", traceback.format_exc())
            return []

    def cancel_generation(self):
        if not self._tt_worker and not self._tt_thread:
            return
        self.cancel_gen_btn.setEnabled(False)
        logger.debug("cancel_generation: request cancellation")
        try:
            if self._tt_worker:
                self._tt_worker.request_cancel()
        except Exception:
            logger.error("Failed to request cancel on worker:\n%s", traceback.format_exc())
        try:
            if self._tt_thread:
                self._tt_thread.quit(); self._tt_thread.wait(2000)
        except Exception:
            logger.error("Waiting for thread failed:\n%s", traceback.format_exc())
        logger.debug("cancel_generation: done (best-effort)")

    def closeEvent(self, event):
        if self._generation_in_progress or (self._tt_thread and self._tt_thread.isRunning()):
            resp = QMessageBox.question(self, "Generation in progress", "A timetable generation is currently running. Do you want to wait for it to finish?\n\nChoose 'No' to request cancellation and close, or 'Cancel' to abort closing.", QMessageBox.Yes | QMessageBox.No | QMessageBox.Cancel, QMessageBox.Yes)
            if resp == QMessageBox.Cancel:
                event.ignore(); return
            if resp == QtWidgets.QMessageBox.Yes:
                QMessageBox.information(self, "Please wait", "Waiting for generation to finish. You can cancel generation to close faster."); event.ignore(); return
            if resp == QtWidgets.QMessageBox.No:
                self.cancel_generation()
                if self._tt_thread and self._tt_thread.isRunning():
                    resp2 = QMessageBox.question(self, "Force close?", "Generation did not stop quickly. Force close the window? This may terminate the solver abruptly.", QMessageBox.Yes | QMessageBox.No, QMessageBox.No)
                    if resp2 == QtWidgets.QMessageBox.No:
                        event.ignore(); return
                    try:
                        logger.warning("Forcing thread termination by calling terminate()")
                        self._tt_thread.terminate(); self._tt_thread.wait(500)
                    except Exception:
                        logger.error("Force terminate failed:\n%s", traceback.format_exc())
        try:
            if self._tt_thread:
                try:
                    self._tt_thread.quit(); self._tt_thread.wait(500)
                except Exception:
                    pass
                try:
                    if self._tt_worker:
                        self._tt_worker.deleteLater()
                    self._tt_thread.deleteLater()
                except Exception:
                    pass
        except Exception:
            logger.error("closeEvent cleanup error:\n%s", traceback.format_exc())
        event.accept()

    # ---------------- Save / Export ----------------
    def save_timetable(self):
        # If the preview was edited manually, ensure _last_grid reflects preview widget content
        try:
            if self._preview_edited:
                # rebuild from preview widget
                grid = {}
                for r in range(self.preview_table.rowCount()):
                    day = self.preview_table.verticalHeaderItem(r).text()
                    row_vals = []
                    for c in range(self.preview_table.columnCount()):
                        it = self.preview_table.item(r, c)
                        row_vals.append(it.text() if it else "")
                    grid[day] = row_vals
                self._last_grid = grid
        except Exception:
            logger.error("Failed to capture preview edits before save:\n%s", traceback.format_exc())

        name, ok = QtWidgets.QInputDialog.getText(self, "Timetable name", "Enter timetable name:")
        if not ok or not name.strip():
            return
        try:
            tsvc = self.services.get('timetable')
            if tsvc and hasattr(tsvc, 'save_timetable'):
                tsvc.save_timetable(name.strip(), self._last_grid, section=self._last_section)
            else:
                dao = self.services.get('timetable_dao')
                if dao and hasattr(dao, 'save_entries'):
                    rows = []
                    for day, cells in self._last_grid.items():
                        for idx, txt in enumerate(cells):
                            rows.append({"day": day, "period": idx+1, "course_name": txt, "teacher_name": "", "section": self._last_section})
                    dao.save_entries(rows)
                else:
                    raise RuntimeError("Timetable save API not available.")
            QMessageBox.information(self, "Saved", "Timetable saved")
            self._preview_edited = False
        except Exception:
            logger.error("save_timetable error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Save failed. See console for details.")

    def export_timetable(self):
        # Ensure preview edits are included before export
        try:
            if self._preview_edited:
                grid = {}
                for r in range(self.preview_table.rowCount()):
                    day = self.preview_table.verticalHeaderItem(r).text()
                    row_vals = []
                    for c in range(self.preview_table.columnCount()):
                        it = self.preview_table.item(r, c)
                        row_vals.append(it.text() if it else "")
                    grid[day] = row_vals
                self._last_grid = grid
        except Exception:
            logger.error("Failed to capture preview edits before export:\n%s", traceback.format_exc())

        filename, _ = QtWidgets.QFileDialog.getSaveFileName(self, "Export PDF", "timetable.pdf", "PDF Files (*.pdf)")
        if not filename:
            return
        periods = self.periods_spin.value()
        slots = self.compute_timeslots(periods, self.start_time.time(), self.duration_spin.value())
        tsvc = self.services.get('timetable')
        try:
            headers = slots
            if tsvc and hasattr(tsvc, 'export_to_pdf'):
                tsvc.export_to_pdf(filename, headers, self._last_grid, meta={"title": f"Timetable - {self.user.get('full_name')}", "created_by": self.user.get('full_name')})
            else:
                from pdf_export import export_grid_pdf_template
                export_grid_pdf_template(filename, headers, self._last_grid, meta={"title": f"Timetable - {self.user.get('full_name')}", "created_by": self.user.get('full_name')})
            QMessageBox.information(self, "Exported", "PDF generated")
        except Exception:
            logger.error("export_timetable error:\n%s", traceback.format_exc()); QMessageBox.critical(self, "Error", "Export failed. See console for details.")