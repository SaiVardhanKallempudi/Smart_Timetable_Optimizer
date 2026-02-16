# UI/get_data_path.py
# Helper to resolve data file paths when running normally or when frozen via PyInstaller.
import sys
import os

def get_data_path(relpath: str) -> str:
    """
    Return absolute path to a resource. Use relative path inside project, e.g. 'smart_timetable.db' or 'UI/login_window.py'.
    When frozen with PyInstaller, files added with --add-data are extracted to sys._MEIPASS.
    """
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        # project root (assuming this file is in UI/, step back one level)
        base = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
    return os.path.join(base, relpath)