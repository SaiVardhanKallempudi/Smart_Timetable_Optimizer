import logging
import sys
import traceback
from PyQt5 import QtWidgets, QtCore
from config import DB_CONFIG

from config import DB_CONFIG
from DAL.db_connector import DBConnector
from DAL.teacher_dao import TeacherDAO
from DAL.course_dao import CourseDAO
from DAL.constraints_dao import ConstraintDAO
from DAL.timetable_dao import TimetableDAO

from SERVICE.auth_service import AuthService
from SERVICE.teacher_service import TeacherService
from SERVICE.course_service import CourseService
from SERVICE.constraints_service import ConstraintService
from SERVICE.timetable_service import TimetableService

from UI.login_window import LoginWindow

# main.py (add or replace at top of file)
import sys
import traceback
import logging
from PyQt5 import QtCore, QtWidgets

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s")

def qt_message_handler(mode, context, message):
    # route Qt messages to Python logging
    if mode == QtCore.QtInfoMsg:
        logging.info(message)
    elif mode == QtCore.QtWarningMsg:
        logging.warning(message)
    elif mode == QtCore.QtCriticalMsg:
        logging.critical(message)
    elif mode == QtCore.QtFatalMsg:
        logging.fatal(message)
    else:
        logging.debug(message)

def global_except_hook(exc_type, exc_value, exc_tb):
    tb = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    logging.error("Unhandled exception (global hook):\n%s", tb)
    try:
        app = QtWidgets.QApplication.instance()
        if app:
            QtWidgets.QMessageBox.critical(None, "Unhandled Exception",
                f"{exc_value}\n\nFull traceback printed to console.")
    except Exception:
        pass
    # don't call sys.exit - allow interactive debugging

# install handlers early
sys.excepthook = global_except_hook
QtCore.qInstallMessageHandler(qt_message_handler)

def build_services():
    db = DBConnector(DB_CONFIG)
    teacher_dao = TeacherDAO(db)
    course_dao = CourseDAO(db)
    constraint_dao = ConstraintDAO(db)
    timetable_dao = TimetableDAO(db)

    auth = AuthService(db)
    teacher_svc = TeacherService(teacher_dao)
    course_svc = CourseService(course_dao)
    constraint_svc = ConstraintService(constraint_dao)
    timetable_svc = TimetableService(timetable_dao, course_dao, constraint_dao)

    return {
        "db": db,
        "teacher_dao": teacher_dao,
        "course_dao": course_dao,
        "constraint_dao": constraint_dao,
        "timetable_dao": timetable_dao,
        "auth": auth,
        "teacher": teacher_svc,
        "course": course_svc,
        "constraint": constraint_svc,
        "timetable": timetable_svc,
    }

if __name__ == "__main__":
    # Create application and start
    app = QtWidgets.QApplication(sys.argv)
    services = build_services()
    win = LoginWindow(services)
    win.show()
    sys.exit(app.exec_())