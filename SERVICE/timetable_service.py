# SERVICE/timetable_service.py
# TimetableService: generate_for_teacher + save/list/get/export helpers for History tab
import logging
from typing import Dict, List, Optional
from collections import defaultdict

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.DEBUG)

# solver runner (in-process)
from tools.solver_runner import run_solver  # adjust path if necessary

try:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    REPORTLAB_AVAILABLE = True
except Exception:
    REPORTLAB_AVAILABLE = False

class TimetableService:
    def __init__(self, timetable_dao, course_dao, constraint_dao):
        self.timetable_dao = timetable_dao
        self.course_dao = course_dao
        self.constraint_dao = constraint_dao

    def _load_courses(self, owner_type: str = None, owner_id: int = None, section: str = None, only_published: bool = True) -> List[dict]:
        try:
            if owner_type and hasattr(self.course_dao, "list_by_owner"):
                return self.course_dao.list_by_owner(owner_type, owner_id, only_published=only_published) or []
            if section and hasattr(self.course_dao, "get_by_section"):
                return self.course_dao.get_by_section(section) or []
            if hasattr(self.course_dao, "list_all"):
                return self.course_dao.list_all() or []
        except Exception:
            logger.exception("Error loading courses")
        return []

    def _build_payload(self, courses: List[dict], constraints: List[dict], periods: int, lunch: int, time_limit: int) -> Dict:
        teacher_map = {}
        try:
            if hasattr(self.course_dao, "list_teachers"):
                tlist = self.course_dao.list_teachers()
                for t in tlist:
                    teacher_map[t.get('id')] = t.get('full_name')
        except Exception:
            pass

        for c in courses:
            tid = c.get('teacher_id') or c.get('teacher') or None
            c['teacher_name'] = teacher_map.get(tid, "")

        cleaned_cons = []
        for c in constraints:
            cc = dict(c)
            owner = cc.get('owner_type') or 'admin'
            if owner == 'admin':
                cc['_weight'] = 10000
            elif owner == 'teacher':
                cc['_weight'] = 100
            else:
                cc['_weight'] = 500
            if 'periods' in cc and 'period_range' not in cc:
                cc['period_range'] = cc['periods']
            cleaned_cons.append(cc)

        return {"courses": courses, "constraints": cleaned_cons, "periods": periods, "lunch": lunch, "time_limit": time_limit}

    def generate_for_teacher(self, teacher_id: int, periods: int = 6, lunch: int = 0, time_limit: int = 20,
                             include_admin: bool = True, only_published: bool = True) -> Dict[str, List[str]]:
        courses = []
        try:
            if hasattr(self.course_dao, "list_by_owner"):
                teacher_courses = self.course_dao.list_by_owner('teacher', teacher_id, only_published=only_published) or []
                courses.extend(teacher_courses)
                if include_admin:
                    admin_courses = self.course_dao.list_by_owner('admin', None, only_published=only_published) or []
                    existing_ids = {c.get('id') for c in courses if c.get('id') is not None}
                    for c in admin_courses:
                        if c.get('id') not in existing_ids:
                            courses.append(c)
            else:
                all_courses = self.course_dao.list_all() if hasattr(self.course_dao, "list_all") else []
                for c in all_courses:
                    if c.get('teacher_id') == teacher_id:
                        courses.append(c)
                if include_admin:
                    for c in all_courses:
                        if c.get('owner_type') is None or c.get('owner_type') == 'admin':
                            if c not in courses:
                                courses.append(c)
        except Exception:
            logger.exception("Failed to load courses for teacher %s", teacher_id)

        constraints = []
        try:
            if include_admin:
                if hasattr(self.constraint_dao, "list_by_owner"):
                    try:
                        constraints.extend(self.constraint_dao.list_by_owner('admin', None, only_published=only_published))
                    except Exception:
                        logger.debug("constraint_dao.list_by_owner('admin') failed")
                else:
                    if hasattr(self.constraint_dao, "list_all"):
                        constraints.extend([c for c in self.constraint_dao.list_all() if c.get('owner_type') == 'admin' and (not only_published or c.get('published'))])
            if hasattr(self.constraint_dao, "list_by_owner"):
                try:
                    constraints.extend(self.constraint_dao.list_by_owner('teacher', teacher_id, only_published=only_published))
                except Exception:
                    logger.debug("constraint_dao.list_by_owner('teacher') failed")
            else:
                if hasattr(self.constraint_dao, "list_all"):
                    constraints.extend([c for c in self.constraint_dao.list_all() if c.get('owner_type') == 'teacher' and c.get('owner_id') == teacher_id and (not only_published or c.get('published'))])
        except Exception:
            logger.exception("Failed to load constraints for teacher %s", teacher_id)

        # include unpublished teacher-owned items for generation testability
        # This logic mirrors the UI: tutors may want to generate with drafts
        try:
            # Build label map
            label_map = {}
            for c in courses:
                label = (c.get("course_name") or c.get("course_code") or f"C{c.get('id')}").strip()
                if label:
                    label_map[" ".join(label.split()).strip().lower()] = label
            # create synthetic course objects for unmatched constraints so solver can place them
            synthetic_id = -1
            for cons in list(constraints):
                cname = (cons.get("course_name") or "").strip()
                if not cname:
                    continue
                norm = " ".join(cname.split()).strip().lower()
                if norm in label_map:
                    continue
                # create minimal synthetic course
                pr = cons.get("period_range") or cons.get("periods") or ""
                credits = 1
                if pr and "-" in pr:
                    try:
                        a, b = pr.split("-", 1)
                        credits = max(1, int(b.lstrip("P")) - int(a.lstrip("P")) + 1)
                    except Exception:
                        credits = 1
                synthetic = {
                    "id": synthetic_id,
                    "course_name": cname,
                    "course_code": cname,
                    "credits": credits,
                    "section": cons.get("section") or "ALL",
                    "teacher_id": None,
                    "_synthetic": True
                }
                courses.append(synthetic)
                label_map[norm] = cname
                synthetic_id -= 1
            # Build payload
            payload = self._build_payload(courses, constraints, periods, lunch, time_limit)
            logger.debug("Generating timetable for teacher=%s payload constraints=%d courses=%d", teacher_id, len(payload['constraints']), len(payload['courses']))
        except Exception:
            logger.exception("Payload build failed; falling back to simple generation")
            payload = self._build_payload(courses, constraints, periods, lunch, time_limit)

        try:
            grid = run_solver(payload)
            return grid
        except Exception:
            logger.exception("Solver invocation failed for teacher generate")
            labels = [(c.get("course_name") or c.get("course_code") or f"C{c.get('id')}").strip() for c in courses] or ["Free"]
            grid = {}
            idx = 0
            for d in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]:
                row = []
                for p in range(periods):
                    if lunch and (p + 1) == lunch:
                        row.append("LUNCH")
                    else:
                        row.append(labels[idx % len(labels)])
                        idx += 1
                grid[d] = row
            return grid

    # ---------------- history helpers ----------------
    def save_timetable(self, name: str, grid: Dict[str, List[str]], section: str = "A", created_by: Optional[str] = None) -> int:
        """
        Save a named timetable and return the set id (if created), 0 otherwise.
        """
        try:
            if hasattr(self.timetable_dao, "save_timetable"):
                return self.timetable_dao.save_timetable(name, grid, section=section, created_by=created_by)
            # fallback: try to ensure DAO.save_entries
            rows = []
            for day, cells in grid.items():
                for idx, txt in enumerate(cells):
                    rows.append({"day": day, "period": idx+1, "course_name": txt, "teacher_name": created_by or "", "section": section})
            if hasattr(self.timetable_dao, "save_entries"):
                self.timetable_dao.save_entries(rows)
            return 0
        except Exception:
            logger.exception("save_timetable failed")
            return 0

    def list_timetables(self) -> List[Dict]:
        try:
            if hasattr(self.timetable_dao, "list_sets"):
                return self.timetable_dao.list_sets() or []
            return []
        except Exception:
            logger.exception("list_timetables failed")
            return []

    def get_timetable_set(self, set_id: int) -> Dict[str, List[str]]:
        try:
            if hasattr(self.timetable_dao, "get_set_entries"):
                rows = self.timetable_dao.get_set_entries(set_id) or []
                by_day = defaultdict(dict)
                max_period = 0
                for r in rows:
                    day = r.get("day")
                    p = int(r.get("period") or 0)
                    by_day[day][p] = r.get("course_name") or ""
                    max_period = max(max_period, p)
                result = {}
                # ensure consistent day order
                for day in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"]:
                    if day in by_day:
                        row = [by_day[day].get(i, "") for i in range(1, max_period+1)]
                        result[day] = row
                if not result and rows:
                    # fallback grouping by present days
                    days_present = sorted({r.get("day") for r in rows})
                    for day in days_present:
                        mp = {int(r.get("period")): r.get("course_name") for r in rows if r.get("day") == day}
                        max_p = max(mp.keys()) if mp else 0
                        result[day] = [mp.get(i, "") for i in range(1, max_p+1)]
                return result
            return {}
        except Exception:
            logger.exception("get_timetable_set failed")
            return {}

    def export_to_pdf(self, filename: str, headers: List[str], grid: Dict[str, List[str]], meta: Optional[Dict] = None):
        """
        Export grid to PDF. Prefers service's built-in PDF support via reportlab if available; otherwise falls back
        to a simple text-based PDF if reportlab is present.
        """
        try:
            if REPORTLAB_AVAILABLE:
                c = canvas.Canvas(filename, pagesize=letter)
                w, h = letter
                title = (meta or {}).get("title") or "Timetable"
                created_by = (meta or {}).get("created_by") or ""
                c.setFont("Helvetica-Bold", 14)
                c.drawString(40, h - 40, title)
                c.setFont("Helvetica", 9)
                if created_by:
                    c.drawString(40, h - 56, f"Created by: {created_by}")
                y = h - 80
                days = list(grid.keys()) or ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]
                col_w = max(80, (w - 100) / max(1, len(headers)))
                # draw header row
                c.setFont("Helvetica-Bold", 8)
                for i, hdr in enumerate(headers):
                    c.drawString(40 + i * col_w, y, hdr)
                y -= 20
                c.setFont("Helvetica", 9)
                for day in days:
                    c.drawString(40, y, day)
                    row = grid.get(day, [""] * len(headers))
                    for i in range(len(headers)):
                        txt = row[i] if i < len(row) else ""
                        c.drawString(40 + i * col_w, y, str(txt))
                    y -= 18
                    if y < 60:
                        c.showPage()
                        y = h - 40
                c.save()
                return True
            else:
                # fallback: write a minimal text PDF via reportlab stripping if not available -> fail gracefully
                raise RuntimeError("reportlab not available for PDF export")
        except Exception:
            logger.exception("export_to_pdf failed")
            raise
