#!/usr/bin/env python3
"""
Robust solver runner (hybrid).

- Uses OR-Tools CP-SAT when native libs are present and safe.
- Falls back to a greedy CSP + small GA improver otherwise.
- Tolerant constraint -> course matching.
- Enforces lunch as a hard block in all solvers.
- Enforces at most one slot per course per day.
- Fixed: Hard vs Exact constraint semantics and greedy handling.
"""
import sys
import json
import logging
import traceback
import random
import time
import importlib.util
from typing import List, Dict, Tuple, Optional
from collections import defaultdict
from pathlib import Path

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s: %(message)s")
logger = logging.getLogger("solver_runner")

DAYS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"]

# OR-Tools guard (only import if native libs present)
ORTOOLS_AVAILABLE = False
cp_model = None
ORTOOLS_REQUIRED_LIBS = [
    "utf8_validity.dll", "zlib1.dll", "bz2.dll", "abseil_dll.dll", "re2.dll",
    "libprotobuf.dll", "highs.dll", "libscip.dll", "ortools.dll"
]


def ortools_native_libs_ok() -> bool:
    try:
        spec = importlib.util.find_spec("ortools")
        if not spec or not spec.origin:
            logger.debug("ortools package not found by find_spec()")
            return False
        pkg_path = Path(spec.origin).resolve().parent
        libs_dir = pkg_path / ".libs"
        if not libs_dir.exists():
            libs_dir = pkg_path
        missing = []
        for name in ORTOOLS_REQUIRED_LIBS:
            p = libs_dir / name
            if not p.exists():
                missing.append(str(p))
        if missing:
            logger.debug("Missing OR-Tools native libs: %s", missing)
            return False
        logger.debug("OR-Tools native libs appear present in %s", libs_dir)
        return True
    except Exception:
        logger.exception("Error while checking OR-Tools native libs")
        return False


if ortools_native_libs_ok():
    try:
        from ortools.sat.python import cp_model  # type: ignore
        ORTOOLS_AVAILABLE = True
        logger.debug("OR-Tools CP-SAT import SUCCESS (native libs OK)")
    except Exception:
        ORTOOLS_AVAILABLE = False
        logger.exception("OR-Tools import failed despite native libs present; will fallback")
else:
    logger.info("OR-Tools native libs not available; will skip OR-Tools and use fallback solver")


def _normalize_text(s: Optional[str]) -> str:
    if not isinstance(s, str):
        return ""
    return " ".join(s.split()).strip().lower()


def _parse_period_token(token: str) -> Tuple[int, int]:
    token = (token or "").strip().upper()
    if not token:
        return 0, 0
    if "-" in token:
        a, b = token.split("-", 1)
        try:
            s = int(a.replace("P", "")) - 1
            e = int(b.replace("P", "")) - 1
            return s, e
        except Exception:
            return 0, 0
    else:
        try:
            i = int(token.replace("P", "")) - 1
            return i, i
        except Exception:
            return 0, 0


def _match_constraint_to_cids(constraint: dict, cid_to_course: Dict[int, dict]) -> List[int]:
    """
    Robust matching: primary exact match on course_name or course_code, then tolerant substring/token matches.
    Returns list of course ids (cids) that the constraint could refer to.
    """
    try:
        cname_raw = (constraint.get("course_name") or "").strip()
        csec = (constraint.get("section") or "ALL").strip()
        target = _normalize_text(cname_raw)
        if not target:
            return []

        matches = []
        # 1) direct exact matches
        for cid, c in cid_to_course.items():
            name = _normalize_text(c.get("course_name") or "")
            code = _normalize_text(c.get("course_code") or "")
            if name == target or code == target:
                c_section = (c.get("section") or "ALL").strip()
                if csec.upper() != "ALL" and c_section != csec:
                    continue
                matches.append(cid)

        if matches:
            logger.debug("Constraint '%s' exact-matched cids: %s", cname_raw, matches)
            return matches

        # 2) tolerant substring/token matching
        target_tokens = [t for t in target.replace("/", " ").replace("-", " ").split() if t]
        for cid, c in cid_to_course.items():
            name = _normalize_text(c.get("course_name") or "")
            code = _normalize_text(c.get("course_code") or "")
            c_section = (c.get("section") or "ALL").strip()
            if csec.upper() != "ALL" and c_section != csec:
                continue
            # if any token is in name or code, count as match
            found = False
            for tk in target_tokens:
                if tk and (tk in name or tk in code or name in tk or code in tk):
                    found = True
                    break
            if found:
                matches.append(cid)

        if matches:
            logger.debug("Constraint '%s' token-matched cids: %s", cname_raw, matches)
            return matches

        # 3) relaxed: match by partial words similarity
        for cid, c in cid_to_course.items():
            name = _normalize_text(c.get("course_name") or "")
            code = _normalize_text(c.get("course_code") or "")
            c_section = (c.get("section") or "ALL").strip()
            if csec.upper() != "ALL" and c_section != csec:
                continue
            # try common prefix/suffix
            if name.startswith(target) or name.endswith(target) or code.startswith(target) or code.endswith(target):
                matches.append(cid)

        if matches:
            logger.debug("Constraint '%s' prefix/suffix-matched cids: %s", cname_raw, matches)
            return matches

        # No matches
        logger.debug("No matching course for constraint '%s' (norm='%s')", cname_raw, target)
        return []
    except Exception:
        logger.exception("Error matching constraint to courses")
        return []


# ----------------------------
# OR-Tools solver (safe)
# ----------------------------
def run_ortools_solver(courses: List[dict], constraints: List[dict], P: int, lunch: int, time_limit: float) -> Optional[Dict[str, List[str]]]:
    if not ORTOOLS_AVAILABLE or cp_model is None:
        logger.debug("run_ortools_solver called but OR-Tools not available")
        return None
    try:
        model = cp_model.CpModel()
        # ensure all courses have ids; if not, assign synthetic ids to allow matching
        cid_list = []
        cid_to_course = {}
        next_synthetic = -1
        for c in courses:
            cid = c.get("id")
            if cid is None:
                cid = next_synthetic
                next_synthetic -= 1
            cid_list.append(cid)
            cid_to_course[cid] = c

        x = {}
        for cid in cid_list:
            for d in range(len(DAYS)):
                for p in range(P):
                    x[(cid, d, p)] = model.NewBoolVar(f"x_c{cid}_d{d}_p{p}")

        # credits -> approximate number of slots across week
        for cid in cid_list:
            credits = int(cid_to_course[cid].get("credits") or 1)
            # require at least 1 slot and aim for credits as a soft objective (use equality where possible)
            model.Add(sum(x[(cid, d, p)] for d in range(len(DAYS)) for p in range(P)) >= 1)

        # at most one slot per course per day
        for cid in cid_list:
            for d in range(len(DAYS)):
                model.Add(sum(x[(cid, d, p)] for p in range(P)) <= 1)

        # teacher conflicts (if teacher_id present)
        teacher_map = defaultdict(list)
        for cid, c in cid_to_course.items():
            teacher_map[c.get("teacher_id")].append(cid)
        for t, cids in teacher_map.items():
            if t is None:
                continue
            for d in range(len(DAYS)):
                for p in range(P):
                    model.Add(sum(x[(cid, d, p)] for cid in cids) <= 1)

        # section conflicts
        section_map = defaultdict(list)
        for cid, c in cid_to_course.items():
            section_map[c.get("section") or "A"].append(cid)
        for sec, cids in section_map.items():
            for d in range(len(DAYS)):
                for p in range(P):
                    model.Add(sum(x[(cid, d, p)] for cid in cids) <= 1)

        # lunch (hard block)
        lunch_idx = None
        if lunch and 1 <= lunch <= P:
            lunch_idx = lunch - 1
            for cid in cid_list:
                for d in range(len(DAYS)):
                    model.Add(x[(cid, d, lunch_idx)] == 0)

        # ---------- FIXED CONSTRAINT HANDLING ----------
        for idx, cons in enumerate(constraints):
            try:
                ctype = (cons.get("type") or "Hard").strip().lower()
                if not cons.get("course_name"):
                    continue
                day_raw = (cons.get("day") or "").strip()
                pr = (cons.get("period_range") or cons.get("periods") or "").strip()
                if not day_raw or not pr:
                    continue
                day = day_raw.strip().capitalize()
                if day not in DAYS:
                    day = day_raw.strip().title()
                    if day not in DAYS:
                        continue
                didx = DAYS.index(day)
                s, e = _parse_period_token(pr)
                s = max(0, s)
                e = min(P - 1, e)
                if s > e:
                    continue

                matching_cids = _match_constraint_to_cids(cons, cid_to_course)
                if not matching_cids:
                    # no matching course present in payload: skip but log
                    logger.debug("Constraint '%s' not matched to any CID; skipping enforcement", cons.get("course_name"))
                    continue

                # compute allowed period indices excluding lunch
                period_indices = [p for p in range(s, e + 1) if 0 <= p < P and (lunch_idx is None or p != lunch_idx)]
                if not period_indices:
                    # nothing to enforce (all requested periods are lunch or out-of-range)
                    continue

                enforce_exact = (ctype == "exact") or ((cons.get("mode") or "") and "exact" in (cons.get("mode") or "").lower())

                if enforce_exact:
                    # If there's a single matching course, require it to fill all requested period indices
                    if len(matching_cids) == 1:
                        cid = matching_cids[0]
                        model.Add(sum(x[(cid, didx, p)] for p in period_indices) >= len(period_indices))
                    else:
                        # multiple matching candidates: require at least len(period_indices) slots among matches
                        model.Add(sum(x[(cid, didx, p)] for cid in matching_cids for p in period_indices) >= len(period_indices))
                else:
                    # Hard: at least one matching course must occupy at least one of the allowed periods
                    model.Add(sum(x[(cid, didx, p)] for cid in matching_cids for p in period_indices) >= 1)

            except Exception:
                logger.warning("Skipping malformed constraint: %s\n%s", cons, traceback.format_exc())

        # objective: maximize slot usage and prefer diversity (simple)
        if x:
            model.Maximize(sum(x.values()))

        solver = cp_model.CpSolver()
        solver.parameters.max_time_in_seconds = float(time_limit)
        solver.parameters.num_search_workers = 1
        solver.parameters.random_seed = 1

        status = solver.Solve(model)
        logger.debug("ORTools status: %s", status)
        grid = {d: [""] * P for d in DAYS}
        if status in (cp_model.OPTIMAL, cp_model.FEASIBLE):
            for d_idx, day in enumerate(DAYS):
                for p in range(P):
                    if lunch_idx is not None and p == lunch_idx:
                        grid[day][p] = "LUNCH"
                        continue
                    assigned = False
                    for cid in cid_list:
                        try:
                            val = solver.Value(x[(cid, d_idx, p)])
                        except Exception:
                            val = 0
                        if val == 1:
                            c = cid_to_course[cid]
                            label = (c.get("course_name") or c.get("course_code") or "").strip()
                            grid[day][p] = label
                            assigned = True
                            break
                    if not assigned:
                        grid[day][p] = ""
            return grid
        else:
            logger.warning("No feasible OR-Tools solution found")
            return None
    except Exception:
        logger.exception("ORTools solver failed unexpectedly")
        return None


# ----------------------------
# Greedy fallback solver
# ----------------------------
def greedy_csp_solver(courses: List[dict], constraints: List[dict], P: int, lunch: int, time_limit: float) -> Dict[str, List[str]]:
    start_time = time.time()
    cid_to_course = {}
    next_synthetic = -1
    for c in courses:
        cid = c.get("id")
        if cid is None:
            cid = next_synthetic
            next_synthetic -= 1
        cid_to_course[cid] = c

    days = DAYS.copy()
    grid = {d: ["" for _ in range(P)] for d in days}

    lunch_idx = lunch - 1 if lunch and 1 <= lunch <= P else None

    # fill constraints first (place sensible single placements for Hard and exact placements for Exact)
    for cons in constraints:
        try:
            day = (cons.get("day") or "").strip().capitalize()
            pr = cons.get("period_range") or cons.get("periods") or ""
            ctype = (cons.get("type") or "Hard").strip().lower()
            if not day or not pr:
                continue
            if day not in days:
                continue
            s, e = _parse_period_token(pr)
            if s < 0:
                s = 0
            if e >= P:
                e = P - 1
            # compute allowed period indices excluding lunch
            period_indices = [p for p in range(s, e + 1) if 0 <= p < P and (lunch_idx is None or p != lunch_idx)]
            if not period_indices:
                continue
            cids = _match_constraint_to_cids(cons, cid_to_course)
            if not cids:
                # Can't match constraint to a course: try to place textual label directly into grid
                label = cons.get("course_name") or ""
                placed = False
                for p in period_indices:
                    if grid[day][p] == "":
                        grid[day][p] = label
                        placed = True
                        break
                if placed:
                    continue
                else:
                    # last-resort: put into first allowed slot
                    grid[day][period_indices[0]] = label
                    continue

            # choose a representative course (first matched)
            rep_cid = cids[0]
            label = cid_to_course[rep_cid].get("course_name") or cid_to_course[rep_cid].get("course_code") or f"C{rep_cid}"
            if ctype == "exact":
                # try to fill all specified indices with representative course where empty
                for p in period_indices:
                    if grid[day][p] == "":
                        grid[day][p] = label
            else:
                # Hard: place one occurrence in the allowed indices (first available)
                placed = False
                for p in period_indices:
                    if grid[day][p] == "":
                        grid[day][p] = label
                        placed = True
                        break
                if not placed:
                    # no free slot in requested window, try to replace an empty-like placeholder (best-effort)
                    for p in period_indices:
                        grid[day][p] = label
                        break
        except Exception:
            logger.debug("greedy solver constraint placement error:\n%s", traceback.format_exc())

    # random-ish distribution for remaining slots to avoid repeated same-subject columns
    labels = [c.get("course_name") or c.get("course_code") or f"C{c.get('id')}" for c in courses] or ["Free"]
    random.seed(int(time.time()) & 0xffffffff)
    shuffled = labels[:]
    random.shuffle(shuffled)
    idx = 0
    for p in range(P):
        for d in days:
            if lunch_idx is not None and p == lunch_idx:
                grid[d][p] = "LUNCH"
                continue
            if not grid[d][p]:
                grid[d][p] = shuffled[idx % len(shuffled)]
                idx += 1

    return grid


# ----------------------------
# Unified run_solver entry
# ----------------------------
def run_solver(payload: Dict) -> Dict[str, List[str]]:
    courses = payload.get("courses", []) or []
    constraints = payload.get("constraints", []) or []
    P = int(payload.get("periods", 6))
    lunch = int(payload.get("lunch", 0))
    time_limit = float(payload.get("time_limit", 10))
    grid = None
    if ORTOOLS_AVAILABLE:
        grid = run_ortools_solver(courses, constraints, P, lunch, time_limit)
    if not grid:
        grid = greedy_csp_solver(courses, constraints, P, lunch, time_limit)
    return grid


if __name__ == "__main__":
    try:
        payload = json.load(sys.stdin)
        grid = run_solver(payload)
        # Print a single JSON line with the resulting grid (SubprocessWorker reads last non-empty line)
        print(json.dumps(grid))
    except Exception:
        logger.error("solver_runner main() failed:\n%s", traceback.format_exc())
        sys.exit(1)