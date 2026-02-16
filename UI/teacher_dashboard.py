# UI/teacher_dashboard.py
# Teacher Dashboard — teacher-scoped UI with editable teacher_constraints and course add/edit/delete
# Updated: generate_preview includes unpublished teacher courses/constraints and synthetic courses for unmatched constraints.
# Added: History tab — list saved timetables, preview selected set, export selected set to PDF.
import json
import logging
import random
import traceback
import copy
from typing import List, Dict, Optional, Any
from pathlib import Path
import sys
import os

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QMessageBox, QFileDialog
from PyQt5.QtCore import QObject, QThread, pyqtSignal

logger = logging.getLogger("TeacherDashboard")
logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")

# Optional MySQL integrity detection
try:
    import mysql.connector
    MYSQL_INTEGRITY_ERROR = mysql.connector.errors.IntegrityError
except Exception:
    class MYSQL_INTEGRITY_ERROR(Exception):
        pass


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
            import subprocess
            proc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            try:
                input_text = json.dumps(self.payload)
            except Exception:
                input_text = str(self.payload)
            try:
                stdout, stderr = proc.communicate(input=input_text, timeout=self.timeout + 5)
            except subprocess.TimeoutExpired:
                try:
                    proc.kill()
                except Exception:
                    pass
                stdout, stderr = proc.communicate()
                msg = f"Solver timed out after {self.timeout + 5}s"
                if stderr:
                    msg += f"\nStderr:\n{stderr}"
                logger.error(msg)
                self.error.emit(msg)
                return
            if proc.returncode != 0:
                msg = f"Solver exit code {proc.returncode}"
                if stderr:
                    msg += f"\nStderr:\n{stderr}"
                logger.error(msg)
                self.error.emit(msg)
                return
            out = stdout.strip()
            lines = [ln for ln in out.splitlines() if ln.strip()]
            if not lines:
                self.error.emit("Solver returned empty output")
                return
            last = lines[-1]
            try:
                grid = json.loads(last)
            except Exception as e:
                msg = f"Failed to parse solver output: {e}\nStdout:\n{stdout}\nStderr:\n{stderr}"
                logger.error(msg)
                self.error.emit(msg)
                return
            if not isinstance(grid, dict):
                self.error.emit("Solver output is not a dict")
                return
            self.finished.emit(grid)
        except Exception:
            tb = traceback.format_exc()
            logger.exception("SubprocessWorker.run unhandled")
            try:
                self.error.emit(tb)
            except Exception:
                logger.exception("Failed to emit error signal")


class TeacherDashboard(QtWidgets.QMainWindow):
    def __init__(self, services: dict, user: dict):
        super().__init__()
        self.services = services or {}
        self.user = user or {}
        self.setWindowTitle("Smart Timetable — Teacher")
        self.resize(1200, 960)

        # state
        self._last_grid: Dict[str, List[str]] = {}
        self._last_section = "A"
        self._preview_edited = False
        self._suppress_cell_change = False
        self._suppress_constraint_change = False

        # worker
        self._tt_thread: Optional[QThread] = None
        self._tt_worker: Optional[SubprocessWorker] = None
        self._generation_in_progress = False

        # local improvement config
        self._improve_iterations = 600

        self.init_ui()
        try:
            self.reload_course_table()
            self.load_constraints_table()
            self.update_timeslot_preview()
            # load history list if history tab was added in init_ui
            try:
                if hasattr(self, "history_list"):
                    self.load_history_list()
            except Exception:
                logger.exception("Initial load_history_list failed")
        except Exception:
            logger.exception("Initial load failed")

    # ---------------- UI ----------------
    def init_ui(self):
        self.setStyleSheet("""
            QWidget { background: #f6f9fc; color: #0f1724; font-family: "Segoe UI", Roboto, Arial; }
            QPushButton { background: #ffffff; border: 1px solid #dbe8f8; padding: 6px 10px; border-radius: 8px; }
            QPushButton#primary { background:#1f6feb; color:white; border: none; }
            QHeaderView::section { background: #eef6ff; padding: 8px; border: none; }
            QLineEdit, QPlainTextEdit { background: #ffffff; border: 1px solid #e6eef8; padding: 6px; border-radius: 6px; }
            QTableWidget { background: #ffffff; border: 1px solid #e6eef8; }
        """)

        central = QtWidgets.QWidget()
        self.setCentralWidget(central)
        main_v = QtWidgets.QVBoxLayout(central)
        main_v.setContentsMargins(12, 12, 12, 12)
        main_v.setSpacing(10)

        # Header
        header = QtWidgets.QHBoxLayout()
        avatar = QtWidgets.QLabel(self._avatar_text())
        avatar.setFixedSize(52, 52)
        avatar.setAlignment(QtCore.Qt.AlignCenter)
        avatar.setStyleSheet("background:#dbeeff; color:#003153; font-weight:700; font-size:16px; border-radius:26px;")
        header.addWidget(avatar)
        welcome_v = QtWidgets.QVBoxLayout()
        welcome_lbl = QtWidgets.QLabel(f"Welcome, {self.user.get('full_name') or self.user.get('username') or 'Teacher'}")
        welcome_lbl.setStyleSheet("font-size:16px; font-weight:700;")
        welcome_sub = QtWidgets.QLabel(f"Role: {self.user.get('role') or 'Teacher'}")
        welcome_sub.setStyleSheet("color:#4b5563; font-size:12px;")
        welcome_v.addWidget(welcome_lbl); welcome_v.addWidget(welcome_sub)
        header.addLayout(welcome_v)
        header.addStretch()
        self.logout_btn = QtWidgets.QPushButton("Logout"); self.logout_btn.clicked.connect(self.on_logout_clicked)
        header.addWidget(self.logout_btn)
        main_v.addLayout(header)

        # Tabs
        self.tabs = QtWidgets.QTabWidget()
        self.tabs.setDocumentMode(True)
        self.tabs.setTabPosition(QtWidgets.QTabWidget.North)
        main_v.addWidget(self.tabs, stretch=1)

        # Profile tab
        profile_w = QtWidgets.QWidget()
        pf = QtWidgets.QFormLayout(profile_w)
        pf.addRow("Username:", QtWidgets.QLabel(self.user.get("username") or ""))
        pf.addRow("Full name:", QtWidgets.QLabel(self.user.get("full_name") or ""))
        pf.addRow("Email:", QtWidgets.QLabel(self.user.get("email") or ""))
        pf.addRow("Role:", QtWidgets.QLabel(self.user.get("role") or "Teacher"))
        self.tabs.addTab(profile_w, "Profile")

        # Timetable Generator tab
        tt_w = QtWidgets.QWidget()
        tt_v = QtWidgets.QVBoxLayout(tt_w)
        tt_v.setContentsMargins(8, 8, 8, 8)
        tt_v.setSpacing(8)

        # Controls row
        ctrl = QtWidgets.QHBoxLayout()
        ctrl.addWidget(QtWidgets.QLabel("Periods/day:"))
        self.periods_spin = QtWidgets.QSpinBox(); self.periods_spin.setRange(1, 12); self.periods_spin.setValue(6); self.periods_spin.setFixedWidth(80)
        self.periods_spin.valueChanged.connect(self.update_timeslot_preview)
        ctrl.addWidget(self.periods_spin)
        ctrl.addSpacing(12)

        ctrl.addWidget(QtWidgets.QLabel("Lunch Period (P#):"))
        self.lunch_spin = QtWidgets.QSpinBox(); self.lunch_spin.setRange(0, 12); self.lunch_spin.setValue(0); self.lunch_spin.setFixedWidth(80)
        self.lunch_spin.valueChanged.connect(self.update_timeslot_preview)
        ctrl.addWidget(self.lunch_spin)
        ctrl.addSpacing(12)

        ctrl.addWidget(QtWidgets.QLabel("Start:"))
        self.start_time = QtWidgets.QTimeEdit(QtCore.QTime(9, 0)); self.start_time.setDisplayFormat("HH:mm"); self.start_time.setFixedWidth(100)
        self.start_time.timeChanged.connect(self.update_timeslot_preview)
        ctrl.addWidget(self.start_time)
        ctrl.addSpacing(12)

        ctrl.addWidget(QtWidgets.QLabel("Duration (min):"))
        self.duration_spin = QtWidgets.QSpinBox(); self.duration_spin.setRange(10, 180); self.duration_spin.setValue(50); self.duration_spin.setFixedWidth(90)
        self.duration_spin.valueChanged.connect(self.update_timeslot_preview)
        ctrl.addWidget(self.duration_spin)
        ctrl.addStretch()

        self.generate_btn = QtWidgets.QPushButton("Generate"); self.generate_btn.setObjectName("primary"); self.generate_btn.clicked.connect(self.generate_preview)
        ctrl.addWidget(self.generate_btn)
        self.cancel_btn = QtWidgets.QPushButton("Cancel"); self.cancel_btn.setEnabled(False); self.cancel_btn.clicked.connect(self.cancel_generation)
        ctrl.addWidget(self.cancel_btn)
        self.save_btn = QtWidgets.QPushButton("Save Timetable"); self.save_btn.clicked.connect(self.save_timetable)
        ctrl.addWidget(self.save_btn)
        self.export_btn = QtWidgets.QPushButton("Export PDF"); self.export_btn.clicked.connect(self.export_timetable)
        ctrl.addWidget(self.export_btn)

        tt_v.addLayout(ctrl)

        # Timeslot preview label
        tsl_row = QtWidgets.QHBoxLayout()
        tsl_row.addWidget(QtWidgets.QLabel("Time slots:"))
        self.timeslot_preview = QtWidgets.QLabel("")
        tsl_row.addWidget(self.timeslot_preview)
        tsl_row.addStretch()
        tt_v.addLayout(tsl_row)

        # Middle split: courses (left - editable actions + add inputs) and constraints (right - editable cells)
        mid_split = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        mid_split.setHandleWidth(8)

        # LEFT: teacher courses (add inputs + table with Edit/Delete)
        left_panel = QtWidgets.QWidget()
        left_l = QtWidgets.QVBoxLayout(left_panel)
        left_l.setContentsMargins(4, 4, 4, 4)
        left_l.addWidget(QtWidgets.QLabel("Your Teacher Courses"))

        # Add row: course code, name, section, add button
        add_row = QtWidgets.QHBoxLayout()
        self.course_code_input = QtWidgets.QLineEdit(); self.course_code_input.setPlaceholderText("Code"); self.course_code_input.setFixedWidth(100)
        self.course_name_input = QtWidgets.QLineEdit(); self.course_name_input.setPlaceholderText("Course name")
        self.course_section_input = QtWidgets.QLineEdit(); self.course_section_input.setPlaceholderText("Section"); self.course_section_input.setFixedWidth(80)
        add_btn = QtWidgets.QPushButton("Add"); add_btn.setFixedHeight(28); add_btn.clicked.connect(self.add_course_from_inputs)
        add_row.addWidget(self.course_code_input); add_row.addWidget(self.course_name_input); add_row.addWidget(self.course_section_input); add_row.addWidget(add_btn)
        left_l.addLayout(add_row)

        # Table: Code, Name, Section, Actions (no ID column shown)
        self.course_table = QtWidgets.QTableWidget()
        self.course_table.setColumnCount(4)
        self.course_table.setHorizontalHeaderLabels(["Code", "Name", "Section", "Actions"])
        self.course_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)
        self.course_table.setMinimumHeight(160)
        self.course_table.setAlternatingRowColors(True)
        left_l.addWidget(self.course_table)
        mid_split.addWidget(left_panel)

        # RIGHT: teacher constraints (editable cells; saved on edit) with scroll
        right_panel = QtWidgets.QWidget()
        right_l = QtWidgets.QVBoxLayout(right_panel)
        right_l.setContentsMargins(4, 4, 4, 4)
        right_l.addWidget(QtWidgets.QLabel("Your Constraints (teacher_constraints)"))
        self.constraints_edit = QtWidgets.QPlainTextEdit()
        self.constraints_edit.setPlaceholderText("One per line: course_name,section(optional),Day,P1-P3")
        self.constraints_edit.setMinimumHeight(110)
        right_l.addWidget(self.constraints_edit)
        add_cons_btn = QtWidgets.QPushButton("Add Constraints"); add_cons_btn.clicked.connect(self.add_constraints)
        right_l.addWidget(add_cons_btn, alignment=QtCore.Qt.AlignRight)

        # Constraints table: Course, Section, Day, Periods, Actions (no ID displayed)
        self.constraints_table = QtWidgets.QTableWidget()
        self.constraints_table.setColumnCount(5)
        self.constraints_table.setHorizontalHeaderLabels(["Course", "Section", "Day", "Periods", "Actions"])
        self.constraints_table.horizontalHeader().setSectionResizeMode(QtWidgets.QHeaderView.Stretch)

        # Keep a reasonable minimum so the UI looks good, but cap the maximum height so a scrollbar appears
        self.constraints_table.setMinimumHeight(120)
        self.constraints_table.setMaximumHeight(260)
        self.constraints_table.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        # Show vertical scrollbar when needed and use smooth scrolling
        self.constraints_table.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAsNeeded)
        self.constraints_table.setVerticalScrollMode(QtWidgets.QAbstractItemView.ScrollPerPixel)

        # allow double-click editing
        self.constraints_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked | QtWidgets.QAbstractItemView.EditKeyPressed)
        self.constraints_table.itemChanged.connect(self._on_constraint_item_changed)
        self.constraints_table.setAlternatingRowColors(True)
        right_l.addWidget(self.constraints_table)
        mid_split.addWidget(right_panel)

        mid_split.setStretchFactor(0, 1); mid_split.setStretchFactor(1, 1)
        tt_v.addWidget(mid_split)
        

        # Timetable preview (large)
        tt_v.addWidget(QtWidgets.QLabel("Generated Timetable (double-click to edit)"))
        self.preview_table = QtWidgets.QTableWidget()
        self.preview_table.setMinimumHeight(900)
        self.preview_table.setAlternatingRowColors(True)
        self.preview_table.setEditTriggers(QtWidgets.QAbstractItemView.DoubleClicked | QtWidgets.QAbstractItemView.SelectedClicked)
        self.preview_table.itemChanged.connect(self.on_preview_item_changed)
        tt_v.addWidget(self.preview_table, stretch=2)

        self.tabs.addTab(tt_w, "Timetable Generator")

        # Add History tab
        try:
            self.add_history_tab()
        except Exception:
            logger.exception("Failed to add history tab in init_ui")

        # About tab - detailed, fills page
        about_w = QtWidgets.QWidget()
        about_l = QtWidgets.QVBoxLayout(about_w)
        about = QtWidgets.QTextEdit(); about.setReadOnly(True)
        about.setHtml("""
        <h1>Smart Timetable — Teacher</h1>
        <p>This application helps teachers create and preview timetables based on their own teacher-scoped courses and constraints.</p>
        """)
        about_l.addWidget(about)
        self.tabs.addTab(about_w, "About")

    # ---------------- helpers ----------------
    def _avatar_text(self) -> str:
        name = (self.user.get("full_name") or self.user.get("username") or "T").strip()
        parts = name.split()
        if len(parts) == 0:
            return "T"
        if len(parts) == 1:
            return parts[0][0].upper()
        return (parts[0][0] + parts[-1][0]).upper()

    def _get_teacher_id(self) -> Optional[int]:
        try:
            tid = self.user.get("id") or self.user.get("user_id") or self.user.get("teacher_id")
            return int(tid) if tid is not None else None
        except Exception:
            return None

    # ---------------- courses (teacher-scoped) ----------------
    def reload_course_table(self):
        teacher_id = self._get_teacher_id()
        rows: List[Dict[str, Any]] = []
        try:
            dao = self.services.get("course_dao")
            svc = self.services.get("course")
            if dao and hasattr(dao, "list_teacher_courses"):
                rows = dao.list_teacher_courses(teacher_id, only_published=True) or []
            elif svc and hasattr(svc, "list_by_owner"):
                rows = svc.list_by_owner("teacher", teacher_id, only_published=True) or []
            else:
                rows = []
        except Exception:
            logger.exception("reload_course_table failed"); rows = []

        if rows is None:
            rows = []

        self.course_table.blockSignals(True)
        try:
            self.course_table.clearContents()
            self.course_table.setRowCount(0)
            self.course_table.setRowCount(len(rows))
            for i, r in enumerate(rows):
                cid = r.get("id") or ""
                code = r.get("course_code") or ""
                name = r.get("course_name") or ""
                section = r.get("section") or ""
                code_item = QtWidgets.QTableWidgetItem(code)
                code_item.setData(QtCore.Qt.UserRole, cid)  # store id internally
                self.course_table.setItem(i, 0, code_item)
                self.course_table.setItem(i, 1, QtWidgets.QTableWidgetItem(name))
                self.course_table.setItem(i, 2, QtWidgets.QTableWidgetItem(section))
                # Actions: Edit / Delete
                w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w); h.setContentsMargins(0,0,0,0)
                edit_btn = QtWidgets.QPushButton("Edit"); edit_btn.setProperty("course_id", cid); edit_btn.clicked.connect(self._on_edit_course)
                del_btn = QtWidgets.QPushButton("Delete"); del_btn.setProperty("course_id", cid); del_btn.clicked.connect(self._on_delete_course)
                edit_btn.setMinimumWidth(60); del_btn.setMinimumWidth(60)
                h.addWidget(edit_btn); h.addWidget(del_btn); h.addStretch()
                self.course_table.setCellWidget(i, 3, w)
            self.course_table.resizeColumnsToContents(); self.course_table.resizeRowsToContents()
        finally:
            self.course_table.blockSignals(False)

    def add_course_from_inputs(self):
        code = (self.course_code_input.text() or "").strip()
        name = (self.course_name_input.text() or "").strip()
        section = (self.course_section_input.text() or "A").strip() or "A"
        if not code or not name:
            QMessageBox.warning(self, "Missing", "Course code and name required"); return
        dao = self.services.get("course_dao"); svc = self.services.get("course")
        teacher_id = self._get_teacher_id()
        try:
            # Prefer teacher-scoped DAO method
            if dao and hasattr(dao, "create_teacher_course"):
                try:
                    dao.create_teacher_course(name, code, 1, section, teacher_id, published=True)
                except TypeError:
                    # signature may vary
                    dao.create_teacher_course(name, code, section, teacher_id)
            elif svc and hasattr(svc, "create_for_teacher"):
                svc.create_for_teacher(name, code, section, teacher_owner_id=teacher_id)
            elif dao and hasattr(dao, "create"):
                try:
                    dao.create(name, code, 1, section, teacher_id)
                except TypeError:
                    dao.create(name, code, section, teacher_id)
            else:
                raise RuntimeError("No create API available")
            self.course_code_input.clear(); self.course_name_input.clear(); self.course_section_input.clear()
            self.reload_course_table()
        except MYSQL_INTEGRITY_ERROR:
            QMessageBox.warning(self, "Duplicate", "Course already exists"); self.reload_course_table()
        except Exception:
            logger.exception("Failed to create teacher course"); QMessageBox.critical(self, "Error", "Failed to add course. See logs.")

    def _on_edit_course(self):
        btn = self.sender()
        if not btn:
            return
        course_id = btn.property("course_id")
        if not course_id:
            return
        dao = self.services.get("course_dao"); svc = self.services.get("course")
        try:
            course = {}
            if dao and hasattr(dao, "get_by_id"):
                course = dao.get_by_id(int(course_id)) or {}
            elif svc and hasattr(svc, "dao") and hasattr(svc.dao, "get_by_id"):
                course = svc.dao.get_by_id(int(course_id)) or {}
        except Exception:
            logger.debug("Failed to load course for edit", exc_info=True)
            course = {}
        dlg = QtWidgets.QDialog(self); dlg.setWindowTitle("Edit Course"); layout = QtWidgets.QVBoxLayout(dlg)
        form = QtWidgets.QFormLayout()
        code_edit = QtWidgets.QLineEdit(course.get("course_code") or "")
        name_edit = QtWidgets.QLineEdit(course.get("course_name") or "")
        section_edit = QtWidgets.QLineEdit(course.get("section") or "")
        form.addRow("Code:", code_edit); form.addRow("Name:", name_edit); form.addRow("Section:", section_edit)
        layout.addLayout(form)
        btns = QtWidgets.QHBoxLayout(); btns.addStretch()
        save_btn = QtWidgets.QPushButton("Save"); cancel_btn = QtWidgets.QPushButton("Cancel")
        btns.addWidget(cancel_btn); btns.addWidget(save_btn); layout.addLayout(btns)

        def on_cancel(): dlg.reject()
        def on_save():
            try:
                new_code = code_edit.text().strip(); new_name = name_edit.text().strip(); new_section = section_edit.text().strip() or "A"
                if dao and hasattr(dao, "update"):
                    try:
                        dao.update(int(course_id), course_name=new_name, course_code=new_code, section=new_section, published=True)
                    except TypeError:
                        dao.update(int(course_id), new_name)
                elif svc and hasattr(svc, "update_course"):
                    svc.update_course(int(course_id), course_name=new_name, course_code=new_code, section=new_section)
                else:
                    raise RuntimeError("Update API not available")
                dlg.accept(); self.reload_course_table()
            except Exception:
                logger.exception("Failed to update course"); QMessageBox.critical(self, "Error", "Failed to update course")
        save_btn.clicked.connect(on_save); cancel_btn.clicked.connect(on_cancel)
        dlg.exec_()

    def _on_delete_course(self):
        btn = self.sender()
        if not btn:
            return
        cid = btn.property("course_id")
        if not cid:
            return
        confirm = QMessageBox.question(self, "Delete course", "Delete course? This will remove the teacher-scoped course.")
        if confirm != QtWidgets.QMessageBox.Yes and confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        dao = self.services.get("course_dao"); svc = self.services.get("course")
        try:
            if dao and hasattr(dao, "delete"):
                dao.delete(cid)
            elif svc and hasattr(svc, "delete"):
                svc.delete(cid)
            else:
                raise RuntimeError("Delete API not available")
            QMessageBox.information(self, "Deleted", "Course deleted"); self.reload_course_table()
        except Exception:
            logger.exception("Delete course failed"); QMessageBox.critical(self, "Error", "Delete failed")

    # ---------------- teacher constraints ----------------
    def load_constraints_table(self):
        teacher_id = self._get_teacher_id()
        items: List[Dict[str, Any]] = []
        try:
            cons_dao = self.services.get("constraint_dao")
            cons_svc = self.services.get("constraint")
            # Only teacher-scoped constraints shown here; do not merge admin constraints
            if cons_dao and hasattr(cons_dao, "list_teacher_constraints"):
                items = cons_dao.list_teacher_constraints(teacher_id, only_published=True) or []
            elif cons_svc and hasattr(cons_svc, "list_constraints_for_teacher"):
                items = cons_svc.list_constraints_for_teacher(teacher_id, include_admin=False, only_published=True) or []
            else:
                items = []
        except Exception:
            logger.exception("load_constraints_table failed"); items = []

        self.constraints_table.blockSignals(True)
        try:
            self.constraints_table.clearContents()
            self.constraints_table.setRowCount(0)
            self.constraints_table.setRowCount(len(items))
            for i, c in enumerate(items):
                cid = c.get("id") or ""
                course = (c.get("course_name") or c.get("course_code") or "").strip()
                section = (c.get("section") or "ALL").strip()
                day = (c.get("day") or "").strip()
                pr = (c.get("period_range") or c.get("periods") or "").strip()
                course_item = QtWidgets.QTableWidgetItem(course)
                course_item.setData(QtCore.Qt.UserRole, cid)  # store constraint id
                self.constraints_table.setItem(i, 0, course_item)
                self.constraints_table.setItem(i, 1, QtWidgets.QTableWidgetItem(section))
                self.constraints_table.setItem(i, 2, QtWidgets.QTableWidgetItem(day))
                self.constraints_table.setItem(i, 3, QtWidgets.QTableWidgetItem(pr))
                # Actions: Delete
                w = QtWidgets.QWidget(); h = QtWidgets.QHBoxLayout(w); h.setContentsMargins(0,0,0,0)
                del_btn = QtWidgets.QPushButton("Delete"); del_btn.setProperty("constraint_id", cid); del_btn.clicked.connect(self._on_delete_constraint_clicked)
                del_btn.setFixedHeight(26); del_btn.setMinimumWidth(70)
                h.addWidget(del_btn); h.addStretch()
                self.constraints_table.setCellWidget(i, 4, w)
            self.constraints_table.resizeColumnsToContents(); self.constraints_table.resizeRowsToContents()
        finally:
            self.constraints_table.blockSignals(False)

    def add_constraints(self):
        raw = self.constraints_edit.toPlainText().strip()
        if not raw:
            QMessageBox.warning(self, "Missing", "Enter constraint lines"); return
        teacher_id = self._get_teacher_id()
        errors = []
        cons_dao = self.services.get("constraint_dao")
        cons_svc = self.services.get("constraint")
        for ln in raw.splitlines():
            ln = ln.strip()
            if not ln:
                continue
            try:
                if cons_svc and hasattr(cons_svc, "add_for_teacher"):
                    cons_svc.add_for_teacher(ln, teacher_id, published=True)
                elif cons_svc and hasattr(cons_svc, "add_from_text"):
                    try:
                        cons_svc.add_from_text(ln, owner_type="teacher", owner_id=teacher_id, published=True)
                    except TypeError:
                        cons_svc.add_from_text(ln)
                elif cons_dao and hasattr(cons_dao, "add"):
                    parts = [p.strip() for p in ln.split(",")]
                    if len(parts) == 3:
                        course_name, day, pr = parts; section = "ALL"; mode = None
                    elif len(parts) == 4:
                        if parts[1].capitalize() in ["Monday","Tuesday","Wednesday","Thursday","Friday"]:
                            course_name, day, pr, mode = parts; section = "ALL"
                        else:
                            course_name, section, day, pr = parts; mode = None
                    elif len(parts) == 5:
                        course_name, section, day, pr, mode = parts
                    else:
                        raise ValueError("Bad format")
                    day = day.strip().capitalize()
                    typ = "Exact" if mode and mode.strip().lower() in ("exact","block","full") else "Hard"
                    try:
                        cons_dao.add(course_name.strip(), section.strip() or "ALL", day, pr.strip(), typ, mode.strip() if mode else None, teacher_id=teacher_id, published=True)
                    except TypeError:
                        cons_dao.add(course_name.strip(), section.strip() or "ALL", day, pr.strip(), typ, mode.strip() if mode else None)
                else:
                    raise RuntimeError("No teacher-scoped constraint API available")
            except Exception as e:
                logger.exception("Failed to add constraint line: %s", ln)
                errors.append(f"{ln} -> {e}")
        if errors:
            QMessageBox.warning(self, "Errors", "\n".join(errors))
        else:
            QMessageBox.information(self, "Done", "Constraints added")
            self.constraints_edit.clear()
            self.load_constraints_table()

    def _on_delete_constraint_clicked(self):
        btn = self.sender()
        if not btn:
            return
        c_id = btn.property("constraint_id")
        if not c_id:
            return
        confirm = QMessageBox.question(self, "Delete constraint", "Delete this constraint?")
        if confirm != QtWidgets.QMessageBox.Yes and confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        try:
            cons_svc = self.services.get("constraint")
            cons_dao = self.services.get("constraint_dao")
            if cons_svc and hasattr(cons_svc, "delete_teacher_constraint"):
                cons_svc.delete_teacher_constraint(c_id)
            elif cons_svc and hasattr(cons_svc, "delete_constraint"):
                cons_svc.delete_constraint(c_id)
            elif cons_dao and hasattr(cons_dao, "delete"):
                cons_dao.delete(c_id)
            else:
                raise RuntimeError("Constraint delete API not available")
            QMessageBox.information(self, "Deleted", "Constraint deleted"); self.load_constraints_table()
        except Exception:
            logger.exception("Constraint delete failed"); QMessageBox.critical(self, "Error", "Delete failed")

    def _on_constraint_item_changed(self, item: QtWidgets.QTableWidgetItem):
        # Save edits to teacher_constraints when teacher double-clicks and edits cells
        if self._suppress_constraint_change:
            return
        try:
            row = item.row()
            # course cell stores constraint id in UserRole
            course_item = self.constraints_table.item(row, 0)
            if not course_item:
                return
            c_id = course_item.data(QtCore.Qt.UserRole)
            if not c_id:
                # can't update without id
                return
            # gather row values
            course_val = self.constraints_table.item(row, 0).text() if self.constraints_table.item(row, 0) else ""
            section_val = self.constraints_table.item(row, 1).text() if self.constraints_table.item(row, 1) else ""
            day_val = self.constraints_table.item(row, 2).text() if self.constraints_table.item(row, 2) else ""
            pr_val = self.constraints_table.item(row, 3).text() if self.constraints_table.item(row, 3) else ""
            # attempt to update via service/dao
            cons_svc = self.services.get("constraint")
            cons_dao = self.services.get("constraint_dao")
            updated = False
            try:
                if cons_svc and hasattr(cons_svc, "update_constraint"):
                    cons_svc.update_constraint(c_id, course_name=course_val, section=section_val, day=day_val, period_range=pr_val)
                    updated = True
                elif cons_dao and hasattr(cons_dao, "update"):
                    try:
                        cons_dao.update(c_id, course_name=course_val, section=section_val, day=day_val, period_range=pr_val)
                    except TypeError:
                        # fallback: rely on positional minimal update if DAO signature differs
                        try:
                            cons_dao.update(c_id, course_val)
                        except Exception:
                            cons_dao.update(c_id, course_name=course_val)
                    updated = True
            except Exception:
                logger.exception("Failed to update constraint id=%s via primary update methods", c_id)
                updated = False
            if not updated:
                QMessageBox.warning(self, "Update failed", "Constraint edited but save to backend failed. Check logs.")
                # reload to restore persisted values
                self.load_constraints_table()
        except Exception:
            logger.exception("_on_constraint_item_changed error")

    # ---------------- generation helpers (teacher-scoped) ----------------
    def compute_timeslots(self, periods: int, start: QtCore.QTime, duration_min: int) -> List[str]:
        slots: List[str] = []
        t = QtCore.QTime(start.hour(), start.minute())
        for _ in range(periods):
            end = t.addSecs(duration_min * 60)
            slots.append(f"{t.toString('HH:mm')}-{end.toString('HH:mm')}")
            t = end
        return slots

    def update_timeslot_preview(self):
        periods = int(self.periods_spin.value()); start = self.start_time.time(); duration = int(self.duration_spin.value())
        slots = self.compute_timeslots(periods, start, duration)
        lunch = int(self.lunch_spin.value())
        lunch_text = f" | Lunch: P{lunch}" if lunch and 1 <= lunch <= periods else ""
        self.timeslot_preview.setText(" | ".join([f"P{idx+1}: {s}" for idx, s in enumerate(slots)]) + lunch_text)

    def _build_placeholder_grid(self, periods: int, lunch: int) -> Dict[str, List[str]]:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
        try:
            teacher_id = self._get_teacher_id()
            dao = self.services.get("course_dao"); svc = self.services.get("course")
            if dao and hasattr(dao, "list_teacher_courses"):
                courses = dao.list_teacher_courses(teacher_id, only_published=True) or []
            elif svc and hasattr(svc, "list_by_owner"):
                courses = svc.list_by_owner("teacher", teacher_id, only_published=True) or []
            else:
                courses = []
            labels = [(c.get('course_name') or c.get('course_code') or f"C{c.get('id')}").strip() for c in courses] or ["Free"]
        except Exception:
            logger.exception("Failed to load teacher courses for placeholder"); labels = ["Free"]
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

    def _build_course_label_map(self, courses: List[dict]) -> Dict[str, str]:
        mapping = {}
        try:
            for c in courses:
                label = (c.get('course_name') or c.get('course_code') or f"C{c.get('id')}").strip()
                if not label:
                    continue
                norm = " ".join(label.split()).strip().lower()
                mapping[norm] = label
                code = (c.get('course_code') or "").strip().lower()
                name = (c.get('course_name') or "").strip().lower()
                if code: mapping[code] = label
                if name: mapping[name] = label
        except Exception:
            logger.exception("build_course_label_map failed")
        return mapping

    def _normalize_grid_labels(self, grid: dict) -> dict:
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
            if not dao or not hasattr(dao, 'list_teacher_constraints'):
                return True, []
            teacher_id = self._get_teacher_id()
            constraints = dao.list_teacher_constraints(teacher_id, only_published=True) or []
        except Exception:
            logger.exception("_validate_grid_constraints failed"); return True, []
        grid_norm = {str(d).strip().capitalize(): row for d, row in grid.items()}

        def _normalize_text(s):
            if not isinstance(s, str): return ""
            return " ".join(s.split()).strip().lower()

        for c in constraints:
            try:
                c_type = (c.get('type') or "Hard").strip()
                if c_type.upper() not in ("HARD", "EXACT"): continue
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
                        if not cell: continue
                        subj = str(cell).split(" - ")[0].strip()
                        subj_norm = _normalize_text(subj)
                        if subj_norm == target or target in subj_norm or subj_norm in target:
                            found = True; break
                if not found:
                    present = []
                    for p in periods:
                        idx = p - 1
                        if 0 <= idx < len(row):
                            present.append(str(row[idx] or ""))
                    msgs.append(f"Hard constraint violated: '{course_name}' expected on {day} in {pr}. Found: {present}")
                    ok = False
            except Exception:
                logger.exception("Error validating constraint %s", c)
        return ok, msgs

    def _locate_solver_runner(self) -> str:
        paths_to_try = [
            Path.cwd() / "tools" / "solver_runner.py",
            Path.cwd() / "solver_runner.py",
            Path(__file__).resolve().parent.parent / "tools" / "solver_runner.py",
            Path(__file__).resolve().parent / "tools" / "solver_runner.py",
        ]
        for p in paths_to_try:
            try:
                if p.exists():
                    return str(p)
            except Exception:
                pass
        return os.path.join("tools", "solver_runner.py")

    # ---------------- local improvement ----------------
    def _diversity_score(self, grid: Dict[str, List[str]]) -> float:
        try:
            days = list(grid.keys()); periods = len(next(iter(grid.values()))) if grid else 0
            score = 0.0
            for p in range(periods):
                seen = set()
                for d in days:
                    val = grid.get(d, [""] * periods)[p] if d in grid else ""
                    if not val: continue
                    if isinstance(val, str) and val.strip().upper() == "LUNCH": continue
                    seen.add(val.strip().lower())
                score += len(seen)
            return float(score)
        except Exception:
            logger.exception("_diversity_score error"); return 0.0

    def _improve_grid_via_swaps(self, grid: Dict[str, List[str]], max_iters: int, lunch_idx: Optional[int]) -> Dict[str, List[str]]:
        try:
            best = copy.deepcopy(grid); best_score = self._diversity_score(best)
            days = list(best.keys()); periods = len(next(iter(best.values()))) if best else 0
            slots = []
            for di in range(len(days)):
                for p in range(periods):
                    if lunch_idx is not None and p == lunch_idx: continue
                    slots.append((di, p))
            if len(slots) < 2: return best
            for _ in range(max_iters):
                a, b = random.sample(slots, 2)
                da, pa = a; db, pb = b
                day_a = days[da]; day_b = days[db]
                if best[day_a][pa] == best[day_b][pb]: continue
                candidate = copy.deepcopy(best)
                candidate[day_a][pa], candidate[day_b][pb] = candidate[day_b][pb], candidate[day_a][pa]
                valid, _ = self._validate_grid_constraints(candidate)
                if not valid: continue
                sc = self._diversity_score(candidate)
                if sc > best_score:
                    best = candidate; best_score = sc
                    if best_score >= periods * len(days) * 0.7: break
            return best
        except Exception:
            logger.exception("_improve_grid_via_swaps error"); return grid

    # ---------------- generation ----------------
    def generate_preview(self):
        if self._generation_in_progress:
            QMessageBox.information(self, "Generating", "Generation already in progress")
            return

        self._generation_in_progress = True
        self.generate_btn.setEnabled(False)
        self.cancel_btn.setEnabled(True)

        periods = int(self.periods_spin.value())
        lunch = int(self.lunch_spin.value())
        lunch_idx = (lunch - 1) if lunch and 1 <= lunch <= periods else None
        time_limit_seconds = 20

        # load teacher-scoped courses
        teacher_id = self._get_teacher_id()
        try:
            dao = self.services.get("course_dao"); svc = self.services.get("course")
            # IMPORTANT: include unpublished teacher courses for generation (teachers often test drafts)
            if dao and hasattr(dao, "list_teacher_courses"):
                teacher_courses = dao.list_teacher_courses(teacher_id, only_published=False) or []
            elif svc and hasattr(svc, "list_by_owner"):
                teacher_courses = svc.list_by_owner("teacher", teacher_id, only_published=False) or []
            else:
                teacher_courses = []
            # Also attempt to include admin/global courses into payload for matching constraints
            admin_courses = []
            try:
                if dao and hasattr(dao, "list_by_owner"):
                    admin_courses = dao.list_by_owner('admin', None, only_published=True) or []
                elif svc and hasattr(svc, "list_by_owner"):
                    admin_courses = svc.list_by_owner('admin', None, only_published=True) or []
            except Exception:
                admin_courses = []
            # combine and deduplicate by id and by (course_code, section) as fallback
            combined = []
            seen_ids = set()
            seen_keys = set()
            for c in (teacher_courses + admin_courses):
                cid = c.get('id')
                key = (c.get('course_code'), c.get('section'))
                if cid is not None:
                    if cid in seen_ids:
                        continue
                    seen_ids.add(cid)
                else:
                    if key in seen_keys:
                        continue
                    seen_keys.add(key)
                combined.append(c)
            courses_for_payload = combined
        except Exception:
            logger.exception("Failed to load teacher courses for payload"); teacher_courses = []; admin_courses = []; courses_for_payload = []

        # load teacher constraints + admin/global constraints if available
        try:
            cons_dao = self.services.get("constraint_dao"); cons_svc = self.services.get("constraint")
            constraints = []
            # include unpublished teacher constraints for generation (teachers need draft constraints honored)
            if cons_dao and hasattr(cons_dao, "list_teacher_constraints"):
                constraints = cons_dao.list_teacher_constraints(teacher_id, only_published=False) or []
                # include admin constraints as well so teacher generation can honor global admin constraints
                try:
                    if hasattr(cons_dao, "list_by_owner"):
                        admin_cons = cons_dao.list_by_owner('admin', None, only_published=True) or []
                        existing_ids = {c.get("id") for c in constraints}
                        constraints.extend([a for a in admin_cons if a.get("id") not in existing_ids])
                except Exception:
                    logger.debug("Could not fetch admin constraints via dao")
            elif cons_svc and hasattr(cons_svc, "list_constraints_for_teacher"):
                # include admin constraints for generation
                constraints = cons_svc.list_constraints_for_teacher(teacher_id, include_admin=True, only_published=True) or []
            else:
                constraints = []
        except Exception:
            logger.exception("Failed to load teacher constraints for payload"); constraints = []

        # normalize/resolve constraint course names using combined courses (teacher + admin)
        course_label_map = self._build_course_label_map(courses_for_payload)
        cleaned_constraints = []
        try:
            for c in constraints:
                cleaned = dict(c)
                for k in ("course_name", "section", "day", "period_range", "type", "mode", "description"):
                    if k in cleaned and isinstance(cleaned[k], str):
                        cleaned[k] = " ".join(cleaned[k].split())
                cname = (cleaned.get("course_name") or "").strip()
                if cname:
                    norm = " ".join(cname.split()).strip().lower()
                    resolved = course_label_map.get(norm)
                    if not resolved:
                        for k, v in course_label_map.items():
                            if norm in k or k in norm:
                                resolved = v; break
                    # keep the original textual name if not resolved; solver will get a synthetic course below
                    if resolved:
                        cleaned["course_name"] = resolved
                cleaned_constraints.append(cleaned)
        except Exception:
            logger.exception("Constraint normalization failed"); cleaned_constraints = constraints

        # If constraints reference course names not present in courses_for_payload, add synthetic courses
        # Map existing normalized label -> course id (string-normalized)
        label_to_id = {}
        for c in courses_for_payload:
            cid = c.get("id")
            label = (c.get("course_name") or c.get("course_code") or f"C{cid}").strip()
            if label:
                label_to_id[" ".join(label.split()).strip().lower()] = cid

        synthetic_id = -1
        synthetic_created = []
        for cons in cleaned_constraints:
            cname = (cons.get("course_name") or "").strip()
            if not cname:
                continue
            norm_c = " ".join(cname.split()).strip().lower()
            if norm_c in label_to_id:
                continue  # already present
            # Not matched: create a synthetic course that the solver can schedule
            # Determine credits from period_range if exact, else 1
            pr = cons.get("period_range") or cons.get("periods") or ""
            periods_list = self._parse_period_range(pr) if pr else []
            credits = max(1, len(periods_list)) if (cons.get("type","").strip().lower()=="exact" and periods_list) else 1
            synthetic_course = {
                "id": synthetic_id,
                "course_name": cname,
                "course_code": cname,
                "credits": credits,
                "section": cons.get("section") or "ALL",
                "teacher_id": None,
                "_synthetic": True
            }
            courses_for_payload.append(synthetic_course)
            label_to_id[norm_c] = synthetic_id
            synthetic_created.append((synthetic_id, cname))
            synthetic_id -= 1

        if synthetic_created:
            logger.debug("Created synthetic courses for unmatched constraints: %s", synthetic_created)

        payload = {"courses": courses_for_payload, "constraints": cleaned_constraints, "periods": periods, "lunch": lunch, "time_limit": time_limit_seconds}

        # debug: log constraints and course ids for troubleshooting
        try:
            logger.debug("Payload courses count=%d constraints count=%d", len(payload["courses"]), len(payload["constraints"]))
            for cons in payload["constraints"]:
                logger.debug("Constraint: %s | day=%s pr=%s type=%s", cons.get("course_name"), cons.get("day"), cons.get("period_range"), cons.get("type"))
        except Exception:
            pass

        # show placeholder
        try:
            placeholder = self._build_placeholder_grid(periods, lunch)
            self._display_grid(placeholder)
        except Exception:
            logger.exception("Failed to show placeholder")

        runner = self._locate_solver_runner()
        if not Path(runner).exists():
            QMessageBox.warning(self, "Runner missing", f"Solver runner not found at: {runner}")
            self.generate_btn.setEnabled(True); self.cancel_btn.setEnabled(False); self._generation_in_progress = False
            return

        # spawn worker
        self._tt_thread = QThread()
        self._tt_worker = SubprocessWorker(runner, payload, time_limit_seconds)
        self._tt_worker.moveToThread(self._tt_thread)

        def on_finished(grid):
            try:
                grid = self._normalize_grid_labels(grid)
                ok, msgs = self._validate_grid_constraints(grid)
                base_score = self._diversity_score(grid)
                logger.debug("Solver returned valid=%s score=%.2f", ok, base_score)
                improved = self._improve_grid_via_swaps(grid, max_iters=self._improve_iterations, lunch_idx=lunch_idx)
                improved_score = self._diversity_score(improved)
                final = improved if improved_score > base_score else grid
                v_ok, v_msgs = self._validate_grid_constraints(final)
                if not v_ok:
                    text = "Hard constraints violated after optimization:\n\n" + "\n".join(v_msgs)
                    resp = QMessageBox.question(self, "Constraints violated", text + "\n\nShow result anyway?", QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes)
                    if resp != QtWidgets.QMessageBox.Yes:
                        self.generate_btn.setEnabled(True); self.cancel_btn.setEnabled(False); self._generation_in_progress = False
                        try:
                            if self._tt_thread:
                                self._tt_thread.quit(); self._tt_thread.wait(2000)
                        except Exception:
                            pass
                        return
                self._display_grid(final)
            except Exception:
                logger.exception("on_finished error")
            finally:
                try:
                    if self._tt_thread:
                        self._tt_thread.quit(); self._tt_thread.wait(2000)
                except Exception:
                    pass
                self._tt_worker = None; self._tt_thread = None
                self.generate_btn.setEnabled(True); self.cancel_btn.setEnabled(False); self._generation_in_progress = False

        def on_error(msg):
            logger.error("Solver error: %s", msg)
            QMessageBox.critical(self, "Generation Error", f"Generation failed:\n{msg}")
            try:
                if self._tt_thread:
                    self._tt_thread.quit(); self._tt_thread.wait(2000)
            except Exception:
                pass
            self._tt_worker = None; self._tt_thread = None
            self.generate_btn.setEnabled(True); self.cancel_btn.setEnabled(False); self._generation_in_progress = False

        self._tt_worker.finished.connect(on_finished)
        self._tt_worker.error.connect(on_error)
        self._tt_thread.started.connect(self._tt_worker.run)
        self._tt_thread.start()
        self.cancel_btn.setEnabled(True)

    # ---------------- display & editing ----------------
    def _display_grid(self, grid: Dict[str, List[str]]):
        try:
            periods = int(self.periods_spin.value())
            slots = self.compute_timeslots(periods, self.start_time.time(), int(self.duration_spin.value()))
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
                            parts = str(txt).split(" - ")
                            display_txt = parts[0].strip() if parts else str(txt)
                        item = QtWidgets.QTableWidgetItem(str(display_txt))
                        item.setFlags(item.flags() | QtCore.Qt.ItemIsEditable)
                        self.preview_table.setItem(i, j, item)
                        display_row.append(str(display_txt))
                    display_grid[day] = display_row
            finally:
                self.preview_table.blockSignals(False)
            self._last_grid = display_grid
            self._preview_edited = False
            self.preview_table.resizeColumnsToContents(); self.preview_table.resizeRowsToContents()
        except Exception:
            logger.exception("Display grid failed"); QMessageBox.critical(self, "Error", "Failed to display timetable")

    def on_preview_item_changed(self, item: QtWidgets.QTableWidgetItem):
        if self._suppress_cell_change:
            return
        try:
            row = item.row(); col = item.column()
            day = self.preview_table.verticalHeaderItem(row).text()
            if not day:
                return
            if day not in self._last_grid:
                self._last_grid[day] = [""] * self.preview_table.columnCount()
            vals = list(self._last_grid[day])
            if len(vals) < self.preview_table.columnCount():
                vals += [""] * (self.preview_table.columnCount() - len(vals))
            vals[col] = item.text()
            self._last_grid[day] = vals
            self._preview_edited = True
        except Exception:
            logger.exception("on_preview_item_changed error")

    # ---------------- save/export ----------------
    def save_timetable(self):
        try:
            if self._preview_edited:
                grid = {}
                for r in range(self.preview_table.rowCount()):
                    day = self.preview_table.verticalHeaderItem(r).text()
                    row = []
                    for c in range(self.preview_table.columnCount()):
                        it = self.preview_table.item(r, c)
                        row.append(it.text() if it else "")
                    grid[day] = row
                self._last_grid = grid
        except Exception:
            logger.exception("Failed to capture preview edits before save")
        name, ok = QtWidgets.QInputDialog.getText(self, "Timetable name", "Enter timetable name:")
        if not ok or not name.strip():
            return
        try:
            tsvc = self.services.get("timetable")
            if tsvc and hasattr(tsvc, "save_timetable"):
                tsvc.save_timetable(name.strip(), self._last_grid, section=self._last_section)
            else:
                dao = self.services.get("timetable_dao")
                if dao and hasattr(dao, "save_entries"):
                    rows = []
                    for day, cells in self._last_grid.items():
                        for idx, txt in enumerate(cells):
                            rows.append({"day": day, "period": idx+1, "course_name": txt, "teacher_name": "", "section": self._last_section})
                    dao.save_entries(rows)
                else:
                    raise RuntimeError("Timetable save API not available.")
            QMessageBox.information(self, "Saved", "Timetable saved")
            self._preview_edited = False
            # refresh history list if present
            try:
                if hasattr(self, "load_history_list"):
                    self.load_history_list()
            except Exception:
                logger.exception("load_history_list after save failed")
        except Exception:
            logger.exception("save_timetable error"); QMessageBox.critical(self, "Error", "Save failed. See logs for details.")

    def export_timetable(self):
        try:
            if self._preview_edited:
                grid = {}
                for r in range(self.preview_table.rowCount()):
                    day = self.preview_table.verticalHeaderItem(r).text()
                    row = []
                    for c in range(self.preview_table.columnCount()):
                        it = self.preview_table.item(r, c)
                        row.append(it.text() if it else "")
                    grid[day] = row
                self._last_grid = grid
        except Exception:
            logger.exception("Failed to capture preview edits before export")
        filename, _ = QFileDialog.getSaveFileName(self, "Export PDF", "timetable.pdf", "PDF Files (*.pdf)")
        if not filename:
            return
        periods = int(self.periods_spin.value())
        slots = self.compute_timeslots(periods, self.start_time.time(), int(self.duration_spin.value()))
        tsvc = self.services.get("timetable")
        try:
            headers = slots
            if tsvc and hasattr(tsvc, "export_to_pdf"):
                tsvc.export_to_pdf(filename, headers, self._last_grid, meta={"title": f"Timetable - {self.user.get('full_name')}", "created_by": self.user.get('full_name')})
            else:
                from pdf_export import export_grid_pdf_template
                export_grid_pdf_template(filename, headers, self._last_grid, meta={"title": f"Timetable - {self.user.get('full_name')}", "created_by": self.user.get('full_name')})
            QMessageBox.information(self, "Exported", "PDF generated")
        except Exception:
            logger.exception("export_timetable error"); QMessageBox.critical(self, "Error", "Export failed. See logs for details.")

    # ---------------- History tab helpers (added) ----------------
    def add_history_tab(self):
        """
        Build and attach the History tab UI. Call this from init_ui() after setting up other tabs.
        This method only creates widgets and attaches signals; it doesn't change existing functions.
        """
        try:
            hist_w = QtWidgets.QWidget()
            hist_l = QtWidgets.QHBoxLayout(hist_w)
  
            # left: list of saved timetables
            left_hist = QtWidgets.QVBoxLayout()
            left_hist.addWidget(QtWidgets.QLabel("Saved Timetables"))
            self.history_list = QtWidgets.QListWidget()
            self.history_list.itemSelectionChanged.connect(self.on_history_selection_changed)
            left_hist.addWidget(self.history_list)
            reload_btn = QtWidgets.QPushButton("Refresh")
            reload_btn.clicked.connect(self.load_history_list)
            left_hist.addWidget(reload_btn)
            hist_l.addLayout(left_hist, 1)

            # right: preview + actions
            right_hist = QtWidgets.QVBoxLayout()
            right_hist.addWidget(QtWidgets.QLabel("Selected Timetable Preview"))
            self.history_preview_table = QtWidgets.QTableWidget()
            self.history_preview_table.setMinimumHeight(380)
            right_hist.addWidget(self.history_preview_table)

            btn_row = QtWidgets.QHBoxLayout()
            self.history_export_btn = QtWidgets.QPushButton("Export PDF")
            self.history_export_btn.clicked.connect(self.export_selected_history)
            self.history_export_btn.setEnabled(False)
            self.history_delete_btn = QtWidgets.QPushButton("Delete")
            self.history_delete_btn.clicked.connect(self.delete_selected_history)
            self.history_delete_btn.setEnabled(False)
            btn_row.addStretch()
            btn_row.addWidget(self.history_export_btn)
            btn_row.addWidget(self.history_delete_btn)
            right_hist.addLayout(btn_row)
            hist_l.addLayout(right_hist, 2)
            


            # add the tab to the tab widget
            self.tabs.addTab(hist_w, "History")
        except Exception:
            logger.exception("add_history_tab failed")

    def load_history_list(self):
        """
        Populate self.history_list with saved timetable metadata.
        Safe fallback: queries timetable service then DAO.
        """
        try:
            self.history_list.clear()
        except Exception:
            # widget may not exist yet
            return

        tsvc = self.services.get("timetable")
        items = []
        try:
            if tsvc and hasattr(tsvc, "list_timetables"):
                items = tsvc.list_timetables() or []
            else:
                dao = self.services.get("timetable_dao")
                if dao and hasattr(dao, "list_sets"):
                    items = dao.list_sets() or []
        except Exception:
            logger.exception("Failed to load history items")
            items = []

        for meta in items:
            name = meta.get("name") or f"Timetable {meta.get('id')}"
            created_at = meta.get("created_at") or ""
            display = f"{name}  —  {created_at}"
            it = QtWidgets.QListWidgetItem(display)
            it.setData(QtCore.Qt.UserRole, meta)
            self.history_list.addItem(it)

    def on_history_selection_changed(self):
        """
        When a history item is selected, load its grid into history_preview_table.
        Also enables Export and Delete buttons.
        """
        sel = getattr(self, "history_list", None)
        if not sel:
            return
        items = sel.selectedItems()
        if not items:
            try:
                self.history_preview_table.clear()
                self.history_export_btn.setEnabled(False)
                self.history_delete_btn.setEnabled(False)
            except Exception:
                pass
            return

        meta = items[0].data(QtCore.Qt.UserRole)
        set_id = meta.get("id") if meta else None
        if not set_id:
            self.history_preview_table.clear()
            self.history_export_btn.setEnabled(False)
            self.history_delete_btn.setEnabled(False)
            return

        # Load grid via service/dao
        grid = {}
        tsvc = self.services.get("timetable")
        try:
            if tsvc and hasattr(tsvc, "get_timetable_set"):
                grid = tsvc.get_timetable_set(set_id) or {}
            else:
                dao = self.services.get("timetable_dao")
                if dao and hasattr(dao, "get_set_entries"):
                    rows = dao.get_set_entries(set_id) or []
                    by_day = {}
                    max_period = 0
                    for r in rows:
                        d = r.get("day")
                        p = int(r.get("period") or 0)
                        by_day.setdefault(d, {})[p] = r.get("course_name") or ""
                        max_period = max(max_period, p)
                    grid = {}
                    for day, mp in by_day.items():
                        grid[day] = [mp.get(i, "") for i in range(1, max_period+1)]
        except Exception:
            logger.exception("Failed to load timetable set rows")
            grid = {}

        # Display grid in history_preview_table
        try:
            periods = int(self.periods_spin.value()) if hasattr(self, "periods_spin") else 6
            if grid:
                periods = max(periods, max((len(r) for r in grid.values()), default=periods))
            slots = self.compute_timeslots(periods, self.start_time.time(), int(self.duration_spin.value())) if hasattr(self, "compute_timeslots") else [f"P{i+1}" for i in range(periods)]
            days = list(grid.keys()) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
            self.history_preview_table.blockSignals(True)
            try:
                self.history_preview_table.clear()
                self.history_preview_table.setRowCount(len(days))
                self.history_preview_table.setColumnCount(periods)
                self.history_preview_table.setVerticalHeaderLabels(days)
                headers = [f"P{i+1}\n{slots[i] if i < len(slots) else ''}" for i in range(periods)]
                self.history_preview_table.setHorizontalHeaderLabels(headers)
                for i, day in enumerate(days):
                    row_vals = grid.get(day, [""] * periods)
                    if len(row_vals) < periods:
                        row_vals = row_vals + [""] * (periods - len(row_vals))
                    for j in range(periods):
                        txt = row_vals[j] or ""
                        item = QtWidgets.QTableWidgetItem(str(txt))
                        item.setFlags(item.flags() & ~QtCore.Qt.ItemIsEditable)
                        self.history_preview_table.setItem(i, j, item)
            finally:
                self.history_preview_table.blockSignals(False)
            self.history_preview_table.resizeColumnsToContents(); self.history_preview_table.resizeRowsToContents()
            # enable actions
            self.history_export_btn.setEnabled(True)
            self.history_delete_btn.setEnabled(True)
        except Exception:
            logger.exception("Failed to show history preview")
            self.history_export_btn.setEnabled(False)
            self.history_delete_btn.setEnabled(False)

    def export_selected_history(self):
        """
        Export currently selected history set to PDF using timetable service export or fallback.
        """
        sel = getattr(self, "history_list", None)
        if not sel:
            return
        items = sel.selectedItems()
        if not items:
            return
        meta = items[0].data(QtCore.Qt.UserRole)
        set_id = meta.get("id") if meta else None
        if not set_id:
            QMessageBox.warning(self, "Export", "No timetable selected")
            return

        # load grid same as preview
        tsvc = self.services.get("timetable")
        grid = {}
        try:
            if tsvc and hasattr(tsvc, "get_timetable_set"):
                grid = tsvc.get_timetable_set(set_id) or {}
            else:
                dao = self.services.get("timetable_dao")
                if dao and hasattr(dao, "get_set_entries"):
                    rows = dao.get_set_entries(set_id) or []
                    by_day = {}
                    max_period = 0
                    for r in rows:
                        d = r.get("day")
                        p = int(r.get("period") or 0)
                        by_day.setdefault(d, {})[p] = r.get("course_name") or ""
                        max_period = max(max_period, p)
                    grid = {}
                    for day, mp in by_day.items():
                        grid[day] = [mp.get(i, "") for i in range(1, max_period+1)]
        except Exception:
            logger.exception("Failed to load timetable for export")
            QMessageBox.critical(self, "Error", "Failed to load timetable for export")
            return

        filename, _ = QFileDialog.getSaveFileName(self, "Export PDF", "timetable.pdf", "PDF Files (*.pdf)")
        if not filename:
            return

        # build headers using current UI times
        periods = int(self.periods_spin.value()) if hasattr(self, "periods_spin") else (max((len(r) for r in grid.values()), default=6) if grid else 6)
        slots = self.compute_timeslots(periods, self.start_time.time(), int(self.duration_spin.value())) if hasattr(self, "compute_timeslots") else [f"P{i+1}" for i in range(periods)]
        try:
            if tsvc and hasattr(tsvc, "export_to_pdf"):
                tsvc.export_to_pdf(filename, slots, grid, meta={"title": f"Timetable - {meta.get('name')}", "created_by": meta.get("created_by")})
            else:
                from pdf_export import export_grid_pdf_template
                export_grid_pdf_template(filename, slots, grid, meta={"title": f"Timetable - {meta.get('name')}", "created_by": meta.get("created_by")})
            QMessageBox.information(self, "Exported", "PDF generated")
        except Exception:
            logger.exception("export_selected_history error")
            QMessageBox.critical(self, "Error", "Export failed. See logs for details.")

    def delete_selected_history(self):
        """
        Delete the currently selected history set from timetable_sets via service/dao.
        """
        sel = getattr(self, "history_list", None)
        if not sel:
            return
        items = sel.selectedItems()
        if not items:
            return
        meta = items[0].data(QtCore.Qt.UserRole)
        set_id = meta.get("id") if meta else None
        if not set_id:
            QMessageBox.warning(self, "Delete", "No timetable selected")
            return
        confirm = QMessageBox.question(self, "Delete Timetable", f"Delete '{meta.get('name')}'? This cannot be undone.")
        if confirm != QtWidgets.QMessageBox.Yes and confirm != QtWidgets.QMessageBox.StandardButton.Yes:
            return
        tsvc = self.services.get("timetable")
        ok = False
        try:
            if tsvc and hasattr(tsvc, "delete_timetable"):
                ok = tsvc.delete_timetable(set_id)
            else:
                dao = self.services.get("timetable_dao")
                if dao and hasattr(dao, "delete_set"):
                    ok = dao.delete_set(set_id)
        except Exception:
            logger.exception("delete_selected_history error")
            ok = False
        if ok:
            QMessageBox.information(self, "Deleted", "Timetable deleted")
            try:
                self.load_history_list()
            except Exception:
                logger.exception("load_history_list after delete failed")
            try:
                self.history_preview_table.clear()
                self.history_export_btn.setEnabled(False)
                self.history_delete_btn.setEnabled(False)
            except Exception:
                pass
        else:
            QMessageBox.critical(self, "Error", "Delete failed. See logs for details.")

    # ---------------- misc ----------------
    def cancel_generation(self):
        try:
            if self._tt_worker:
                self._tt_worker.request_cancel()
            if self._tt_thread:
                self._tt_thread.quit(); self._tt_thread.wait(2000)
        except Exception:
            logger.exception("cancel_generation error")
        self.cancel_btn.setEnabled(False)
        self.generate_btn.setEnabled(True)
        self._generation_in_progress = False

    def on_logout_clicked(self):
        try:
            auth = self.services.get("auth")
            if auth and hasattr(auth, "logout"):
                try:
                    auth.logout()
                except Exception:
                    logger.exception("auth.logout raised")
        except Exception:
            pass
        try:
            from UI.login_window import LoginWindow
            try:
                lw = LoginWindow(self.services)
            except TypeError:
                lw = LoginWindow()
            lw.show()
        except Exception:
            logger.debug("LoginWindow import/show failed; closing dashboard")
        try:
            self.close()
        except Exception:
            pass
