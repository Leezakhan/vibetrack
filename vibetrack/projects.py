"""Projects: creation, editing, and the one-call overview."""
import re
import sqlite3
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from . import tasks
from .db import log_event

SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{1,39}$")
EDITABLE = {"name", "description", "scope", "conventions", "timeline_days"}


def _check_days(days: Optional[int]) -> None:
    if days is not None and days < 1:
        raise ValueError("timeline_days must be a positive number of days")


def create(conn: sqlite3.Connection, slug: str, name: str, description: str = "",
           scope: str = "", conventions: str = "", timeline_days: Optional[int] = None) -> int:
    if not SLUG_RE.match(slug):
        raise ValueError("slug must be 2-40 chars: lowercase letters, digits, hyphens")
    if not name.strip():
        raise ValueError("name is required")
    _check_days(timeline_days)
    try:
        cur = conn.execute(
            "INSERT INTO projects (slug, name, description, scope, conventions, timeline_days) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (slug, name.strip(), description, scope, conventions, timeline_days))
    except sqlite3.IntegrityError:
        raise ValueError(f"Project '{slug}' already exists") from None
    log_event(conn, cur.lastrowid, "system", "project_created", slug, name)
    return cur.lastrowid


def update(conn: sqlite3.Connection, pid: int, fields: Dict[str, Any], actor: str = "claude") -> None:
    """Update whitelisted columns; None values are ignored."""
    changes = {k: v for k, v in fields.items() if v is not None}
    unknown = set(changes) - EDITABLE
    if unknown:
        raise ValueError(f"Cannot edit {sorted(unknown)}; allowed: {sorted(EDITABLE)}")
    if "timeline_days" in changes:
        _check_days(changes["timeline_days"])
    if not changes:
        return
    assignments = ", ".join(f"{col} = ?" for col in changes)  # column names are whitelisted above
    conn.execute(f"UPDATE projects SET {assignments} WHERE id = ?", (*changes.values(), pid))
    log_event(conn, pid, actor, "project_updated", "", ", ".join(sorted(changes)))


def overview(conn: sqlite3.Connection, pid: int) -> Dict[str, Any]:
    """Everything needed to know where the project stands, in one dict."""
    p = dict(conn.execute("SELECT * FROM projects WHERE id = ?", (pid,)).fetchone())
    started = datetime.fromisoformat(p["created_at"]).replace(tzinfo=timezone.utc)
    timeline: Dict[str, Any] = {"days_elapsed": (datetime.now(timezone.utc) - started).days}
    if p["timeline_days"]:
        timeline["planned_days"] = p["timeline_days"]
        timeline["days_remaining"] = p["timeline_days"] - timeline["days_elapsed"]
        timeline["deadline"] = (started + timedelta(days=p["timeline_days"])).date().isoformat()

    contributors = conn.execute(
        "SELECT actor, COUNT(*) AS actions, MAX(created_at) AS last_active FROM events "
        "WHERE project_id = ? AND actor != 'system' GROUP BY actor ORDER BY actions DESC", (pid,))
    return {
        "project": {k: p[k] for k in ("slug", "name", "description", "scope", "conventions")},
        "timeline": timeline,
        "progress": tasks.progress(conn, pid),
        "in_progress": tasks.leaves(conn, pid, "in_progress"),
        "blocked": tasks.leaves(conn, pid, "blocked"),
        "next_up": tasks.queue(conn, pid, 5),
        "recently_done": tasks.leaves(conn, pid, "done", 5, newest_first=True),
        "contributors": [dict(r) for r in contributors],
        "decisions_recorded": conn.execute(
            "SELECT COUNT(*) FROM decisions WHERE project_id = ?", (pid,)).fetchone()[0],
    }


def list_all(conn: sqlite3.Connection) -> list:
    """One line per project (newest first) for the dashboard's project picker."""
    rows = conn.execute("SELECT id, slug, name, created_at FROM projects ORDER BY id DESC").fetchall()
    return [{"slug": r["slug"], "name": r["name"], "created_at": r["created_at"],
             "percent_done": tasks.progress(conn, r["id"])["percent_done"]} for r in rows]
