
import logging
from typing import Optional

from PyQt5 import QtWidgets, QtCore, QtGui
from PyQt5.QtWidgets import QMessageBox

logger = logging.getLogger(__name__)


class AuthWorker(QtCore.QObject):
    """
    Worker to perform authentication off the UI thread.
    Expects an auth service with authenticate(username, password) -> user dict or None.
    """
    finished = QtCore.pyqtSignal(object, object)  # user, error

    def __init__(self, auth_service, username: str, password: str):
        super().__init__()
        self.auth = auth_service
        self.username = username
        self.password = password

    @QtCore.pyqtSlot()
    def run(self):
        try:
            if not self.auth:
                raise RuntimeError("Auth service not available")
            user = self.auth.authenticate(self.username, self.password)
            # emit user (may be None) and None for error
            self.finished.emit(user, None)
        except Exception as e:
            logger.exception("AuthWorker.run error")
            self.finished.emit(None, e)


class LoginWindow(QtWidgets.QWidget):
    """
    Modern styled login window. Provide a `services` dict with an 'auth' service.
    The auth service must expose authenticate(username, password).
    """

    def __init__(self, services: dict):
        super().__init__()
        self.services = services or {}
        self.setWindowTitle("Smart Timetable — Login")
        self.setMinimumSize(540, 420)
        self.setAttribute(QtCore.Qt.WA_StyledBackground, True)

        # persistent settings for "remember me"
        self._settings = QtCore.QSettings("SmartTimetable", "SmartTimetableApp")

        # thread handle
        self._auth_thread: Optional[QtCore.QThread] = None
        self._auth_worker: Optional[AuthWorker] = None

        self._build_ui()
        self._apply_styles()
        self._load_settings()

    def _build_ui(self):
        main = QtWidgets.QVBoxLayout(self)
        main.setContentsMargins(28, 24, 28, 24)
        main.setSpacing(12)

        # Header: optional logo + title
        header = QtWidgets.QHBoxLayout()
        header.setSpacing(12)
        logo = QtWidgets.QLabel()
        logo.setFixedSize(56, 56)
        logo.setObjectName("logo")
        # keep empty pixmap; app can style #logo via stylesheet or set a pixmap externally
        logo.setPixmap(QtGui.QPixmap())
        header.addWidget(logo)

        title_v = QtWidgets.QVBoxLayout()
        title = QtWidgets.QLabel("Smart Timetable Optimizer")
        title.setObjectName("title")
        subtitle = QtWidgets.QLabel("Create and manage optimized academic timetables")
        subtitle.setObjectName("subtitle")
        title_v.addWidget(title)
        title_v.addWidget(subtitle)
        header.addLayout(title_v)
        header.addStretch()
        main.addLayout(header)

        main.addStretch(1)

        # Card container (centered)
        card_outer = QtWidgets.QHBoxLayout()
        card_outer.addStretch(1)

        card = QtWidgets.QFrame()
        card.setObjectName("card")
        card.setMinimumWidth(420)
        card.setSizePolicy(QtWidgets.QSizePolicy.Preferred, QtWidgets.QSizePolicy.Fixed)
        card_layout = QtWidgets.QVBoxLayout(card)
        card_layout.setContentsMargins(20, 18, 20, 18)
        card_layout.setSpacing(12)

        # Form layout
        form = QtWidgets.QFormLayout()
        form.setLabelAlignment(QtCore.Qt.AlignLeft)
        form.setFormAlignment(QtCore.Qt.AlignLeft)
        form.setHorizontalSpacing(12)
        form.setVerticalSpacing(10)

        # Username
        self.username = QtWidgets.QLineEdit()
        self.username.setPlaceholderText("Username")
        self.username.setObjectName("input")
        self.username.setMinimumHeight(44)
        self.username.setClearButtonEnabled(True)
        self.username.setAccessibleName("Username")
        self.username.setAccessibleDescription("Enter your username")

        # Password + toggle
        pw_row = QtWidgets.QHBoxLayout()
        self.password = QtWidgets.QLineEdit()
        self.password.setPlaceholderText("Password")
        self.password.setEchoMode(QtWidgets.QLineEdit.Password)
        self.password.setObjectName("input")
        self.password.setMinimumHeight(44)
        self.password.setClearButtonEnabled(True)
        self.password.setAccessibleName("Password")
        self.password.setAccessibleDescription("Enter your password")

        self._pw_toggle = QtWidgets.QToolButton()
        self._pw_toggle.setCheckable(True)
        self._pw_toggle.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self._pw_toggle.setToolTip("Show / hide password")
        eye_icon = self.style().standardIcon(QtWidgets.QStyle.SP_BrowserReload)  # fallback icon
        try:
            # try to use a more suitable icon if available
            eye_icon = QtGui.QIcon.fromTheme("view-password") or eye_icon
        except Exception:
            pass
        self._pw_toggle.setIcon(eye_icon)
        self._pw_toggle.setFixedSize(28, 28)
        self._pw_toggle.clicked.connect(self._on_toggle_password)

        pw_row.addWidget(self.password)
        pw_row.addWidget(self._pw_toggle)

        form.addRow(self._label("Username"), self.username)
        form.addRow(self._label("Password"), pw_row)
        card_layout.addLayout(form)

        # Options row: remember only (demo credentials removed)
        options = QtWidgets.QHBoxLayout()
        self.remember = QtWidgets.QCheckBox("Remember me")
        self.remember.setAccessibleName("Remember me")
        options.addWidget(self.remember)
        options.addStretch()
        card_layout.addLayout(options)

        # Login row: button + progress indicator
        login_row = QtWidgets.QHBoxLayout()
        self.login_btn = QtWidgets.QPushButton("Log in")
        self.login_btn.setObjectName("primary")
        self.login_btn.setCursor(QtGui.QCursor(QtCore.Qt.PointingHandCursor))
        self.login_btn.setMinimumHeight(44)
        self.login_btn.clicked.connect(self.on_login)

        # small status label
        self._status = QtWidgets.QLabel("")
        self._status.setObjectName("status")
        self._status.setAlignment(QtCore.Qt.AlignCenter)
        self._status.setMinimumWidth(160)

        login_row.addWidget(self.login_btn, 2)
        login_row.addWidget(self._status, 1)
        card_layout.addLayout(login_row)

        card_outer.addWidget(card)
        card_outer.addStretch(1)
        main.addLayout(card_outer)

        main.addStretch(1)

        # Footer
        footer = QtWidgets.QLabel("© Smart Timetable — Organized schedules, less fuss")
        footer.setObjectName("footer")
        footer.setAlignment(QtCore.Qt.AlignCenter)
        main.addWidget(footer)

        # Keyboard behaviour
        self.username.returnPressed.connect(self.password.setFocus)
        self.password.returnPressed.connect(self.on_login)

    def _label(self, text: str) -> QtWidgets.QLabel:
        lbl = QtWidgets.QLabel(text)
        lbl.setFont(QtGui.QFont("Segoe UI", 10))
        return lbl

    def _apply_styles(self):
        self.setStyleSheet("""
        QWidget { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #f7f9fb, stop:1 #eef2f6); color: #0f172a; font-family: "Segoe UI", "Roboto", "Arial"; }
        #title { font-size: 20px; font-weight: 800; color: #0f172a; }
        #subtitle { font-size: 12px; color: #6b7280; margin-bottom: 6px; }
        #card { background: white; border-radius: 12px; border: 1px solid rgba(31,41,55,0.06); }
        QLineEdit#input { padding: 10px 12px; border: 1px solid #e5e7eb; border-radius: 8px; background: #ffffff; color: #0b1220; }
        QLineEdit#input::placeholder { color: #9ca3af; }
        QLineEdit#input:focus { border: 1px solid #4f46e5; background: #ffffff; }
        QPushButton#primary { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #4f46e5, stop:1 #6366f1); color: white; padding: 10px; border-radius: 8px; font-weight: 700; border: none; }
        QPushButton#primary:hover { background: qlineargradient(x1:0,y1:0,x2:1,y2:0, stop:0 #5b4ff0, stop:1 #6f6ff5); }
        QPushButton#link { color: #4f46e5; text-decoration: underline; font-size: 12px; }
        #hint { font-size: 11px; color: #9ca3af; margin-top:6px; }
        #footer { font-size: 10px; color: #9ca3af; margin-top: 8px; }
        QLabel#status { font-size: 12px; color: #6b7280; }
        QCheckBox { color: #374151; }
        QToolButton { border: none; background: transparent; }
        #logo { background: qlineargradient(x1:0,y1:0,x2:1,y2:1, stop:0 #eef2ff, stop:1 #ffffff); border-radius: 8px; }
        """)

        # enforce readable palettes for inputs
        pal = self.palette()
        pal.setColor(QtGui.QPalette.Text, QtGui.QColor("#0b1220"))
        self.setPalette(pal)

    def _load_settings(self):
        try:
            remembered = self._settings.value("login/username", "")
            if remembered:
                self.username.setText(remembered)
                self.remember.setChecked(True)
                self.password.setFocus()
            else:
                self.username.setFocus()
        except Exception:
            logger.exception("Failed to load settings")

    def _save_settings(self):
        try:
            if self.remember.isChecked():
                self._settings.setValue("login/username", self.username.text().strip())
            else:
                self._settings.remove("login/username")
            self._settings.sync()
        except Exception:
            logger.exception("Failed to save settings")

    def _on_toggle_password(self, checked: bool):
        # Toggle echo mode
        if self._pw_toggle.isChecked():
            self.password.setEchoMode(QtWidgets.QLineEdit.Normal)
        else:
            self.password.setEchoMode(QtWidgets.QLineEdit.Password)

    def _set_busy(self, busy: bool, status_text: str = ""):
        self.login_btn.setEnabled(not busy)
        self.username.setEnabled(not busy)
        self.password.setEnabled(not busy)
        self.remember.setEnabled(not busy)
        if busy:
            self._status.setText(status_text or "Logging in…")
        else:
            self._status.setText(status_text or "")

    def on_login(self):
        username = self.username.text().strip()
        password = self.password.text().strip()
        if not username or not password:
            QMessageBox.warning(self, "Missing", "Please enter username and password.")
            return

        auth = self.services.get("auth")
        # disable UI and run auth in background
        self._set_busy(True, "Authenticating…")

        # create thread + worker
        self._auth_thread = QtCore.QThread()
        self._auth_worker = AuthWorker(auth, username, password)
        self._auth_worker.moveToThread(self._auth_thread)
        self._auth_thread.started.connect(self._auth_worker.run)
        self._auth_worker.finished.connect(self._on_auth_finished)
        # ensure proper cleanup
        self._auth_worker.finished.connect(self._auth_thread.quit)
        self._auth_worker.finished.connect(self._auth_worker.deleteLater)
        self._auth_thread.finished.connect(self._auth_thread.deleteLater)
        self._auth_thread.start()

    def _on_auth_finished(self, user, error):
        # re-enable UI
        self._set_busy(False)

        # handle error
        if error:
            QMessageBox.critical(self, "Error", f"Authentication error:\n{error}")
            logger.exception("Authentication error: %s", error)
            return

        # no user -> failed credentials
        if not user:
            QMessageBox.warning(self, "Auth failed", "Invalid username or password.")
            return

        # persist remember-me
        try:
            self._save_settings()
        except Exception:
            logger.exception("Failed saving settings after login")

        # open dashboard
        role = user.get("role", "Teacher")
        try:
            if role == "Admin":
                from UI.admin_dashboard import AdminDashboard
                self.dashboard = AdminDashboard(self.services, user)
            else:
                from UI.teacher_dashboard import TeacherDashboard
                self.dashboard = TeacherDashboard(self.services, user)
            self.dashboard.show()
            self.close()
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to open dashboard: {e}")
            logger.exception("Failed to open dashboard after login: %s", e)