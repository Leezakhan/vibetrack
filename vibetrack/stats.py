"""Chart data for the dashboard: read-only queries that return JSON-ready dicts.

Dates are UTC days ('YYYY-MM-DD') to match SQLite's datetime('now').
Hours = estimate_hours, and a task with no estimate counts as 1 hour (same rule as progress()).
"""
import sqlite3
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from . import tasks

ACTIVITY_DAYS = 14   # width of the activity chart
MAX_EPICS = 12       # keep the hours-by-epic list readable


def _today() -> date:
    return datetime.now(timezone.utc).date()


def burndown(conn: sqlite3.Connection, pid: int, today: Optional[date] = None) -> Dict[str, Any]:
    """Remaining hours per day (actual) against a straight line to zero at the deadline (ideal)."""
    today = today or _today()
    p = conn.execute("SELECT created_at, timeline_days FROM projects WHERE id = ?", (pid,)).fetchone()
    start = date.fromisoformat(p["created_at"][:10])
    planned = p["timeline_days"]
    # Extend the axis past the deadline if we are late, so an overrun stays visible.
    end = max(start + timedelta(days=planned) if planned else today, today)

    rows = conn.execute(
        f"SELECT COALESCE(t.estimate_hours, 1) AS w, substr(t.completed_at, 1, 10) AS done "
        f"FROM tasks t WHERE t.project_id = ? AND {tasks.IS_LEAF}", (pid,)).fetchall()
    total = sum(r["w"] for r in rows)
    done_on: Dict[str, float] = defaultdict(float)
    for r in rows:
        if r["done"]:
            done_on[r["done"]] += r["w"]

    days: List[str] = []
    actual: List[Optional[float]] = []
    ideal: List[float] = []
    remaining = total
    for i in range((end - start).days + 1):
        d = start + timedelta(days=i)
        days.append(d.isoformat())
        if d <= today:                      # no actuals for the future
            remaining -= done_on.get(d.isoformat(), 0)
            actual.append(round(remaining, 2))
        else:
            actual.append(None)
        if planned:
            ideal.append(round(max(total * (1 - i / planned), 0), 2))

    t_idx = (today - start).days
    variance = round(actual[t_idx] - ideal[t_idx], 1) if planned and total else None
    return {"days": days, "actual": actual, "ideal": ideal or None, "total_hours": round(total, 2),
            "today_index": t_idx, "variance_hours": variance}   # variance > 0 means behind plan


def by_type(conn: sqlite3.Connection, pid: int) -> List[Dict[str, Any]]:
    """Leaf tasks per type with how many are done."""
    rows = conn.execute(
        f"SELECT t.type, COUNT(*) AS total, SUM(t.status = 'done') AS done FROM tasks t "
        f"WHERE t.project_id = ? AND {tasks.IS_LEAF} GROUP BY t.type ORDER BY total DESC", (pid,))
    return [dict(r) for r in rows]


def epics(conn: sqlite3.Connection, pid: int) -> List[Dict[str, Any]]:
    """Estimated vs spent hours per top-level task (its whole subtree rolled up)."""
    rows = conn.execute(
        "SELECT id, parent_id, title, estimate_hours, spent_hours FROM tasks WHERE project_id = ? "
        "ORDER BY id", (pid,)).fetchall()
    parent = {r["id"]: r["parent_id"] for r in rows}
    has_kids = {p for p in parent.values() if p is not None}

    def root(i: int) -> int:
        while parent[i] is not None:
            i = parent[i]
        return i

    totals: Dict[int, Dict[str, float]] = {}
    for r in rows:
        agg = totals.setdefault(root(r["id"]), {"estimate": 0.0, "spent": 0.0})
        if r["id"] not in has_kids:            # estimates live on leaves; parents would double count
            agg["estimate"] += r["estimate_hours"] or 0
        agg["spent"] += r["spent_hours"]
    titles = {r["id"]: r["title"] for r in rows}
    out = [{"title": titles[i], "estimate": round(a["estimate"], 1), "spent": round(a["spent"], 1)}
           for i, a in totals.items()]
    return out[:MAX_EPICS]


def activity(conn: sqlite3.Connection, pid: int, today: Optional[date] = None) -> Dict[str, Any]:
    """Tracker actions per day per actor over the last ACTIVITY_DAYS days."""
    today = today or _today()
    first = today - timedelta(days=ACTIVITY_DAYS - 1)
    rows = conn.execute(
        "SELECT substr(created_at, 1, 10) AS d, actor, COUNT(*) AS n FROM events "
        "WHERE project_id = ? AND actor != 'system' AND created_at >= ? GROUP BY d, actor", (pid, first.isoformat()))
    per_actor: Dict[str, Dict[str, int]] = defaultdict(dict)
    for r in rows:
        per_actor[r["actor"]][r["d"]] = r["n"]
    days = [(first + timedelta(days=i)).isoformat() for i in range(ACTIVITY_DAYS)]
    return {"days": days,
            "series": [{"actor": a, "counts": [by_day.get(d, 0) for d in days]}
                       for a, by_day in per_actor.items()]}


def charts(conn: sqlite3.Connection, pid: int) -> Dict[str, Any]:
    """Everything the dashboard's charts need, in one call."""
    return {"burndown": burndown(conn, pid), "by_type": by_type(conn, pid),
            "epics": epics(conn, pid), "activity": activity(conn, pid)}
