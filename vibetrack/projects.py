"""Projects: creation, editing, and the one-call overview."""
import json
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


def _rotation(conn: sqlite3.Connection, pid: int) -> Dict[str, Any]:
    row = conn.execute("SELECT ai_rotation, ai_cursor FROM projects WHERE id = ?", (pid,)).fetchone()
    tools = json.loads(row["ai_rotation"])
    return {"tools": tools, "cursor": row["ai_cursor"] % len(tools), "current": tools[row["ai_cursor"] % len(tools)]}


def set_rotation(conn: sqlite3.Connection, pid: int, tools: list, actor: str = "claude") -> Dict[str, Any]:
    """Set which AI tools to rotate through, in order. Names are freeform (whatever you call
    the tool); duplicates and blanks are dropped, order is kept. Resets to the first one."""
    clean, seen = [], set()
    for t in tools:
        t = (t or "").strip().lower()
        if t and t not in seen:
            clean.append(t); seen.add(t)
    if not clean:
        raise ValueError("tools must contain at least one AI name")
    conn.execute("UPDATE projects SET ai_rotation = ?, ai_cursor = 0 WHERE id = ?", (json.dumps(clean), pid))
    log_event(conn, pid, actor, "ai_rotation_set", "", ", ".join(clean))
    return _rotation(conn, pid)


def switch_ai(conn: sqlite3.Connection, pid: int, actor: str, reason: str = "") -> Dict[str, Any]:
    """One call for 'I hit a limit': advances to the next AI in the rotation and returns the
    handoff briefing for it, so the only manual step left is opening that tool and pasting it."""
    from . import handoff  # local import: handoff imports this module, so avoid a import cycle

    before = _rotation(conn, pid)
    new_cursor = before["cursor"] + 1
    conn.execute("UPDATE projects SET ai_cursor = ? WHERE id = ?", (new_cursor, pid))
    after = _rotation(conn, pid)
    log_event(conn, pid, actor, "ai_switched", after["current"],
             reason or f"from {before['current']}")
    return {"you_were_using": before["current"], "open_next": after["current"],
            "rotation": after["tools"], "handoff_markdown": handoff.build(conn, pid)}


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
        "ai_rotation": _rotation(conn, pid),
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
