"""Task logic: nested tasks, priority queue, status roll-up, progress."""
import sqlite3
from typing import Any, Dict, List, Optional

from .db import log_event

TYPES = {"feature", "bug", "test", "refactor", "docs", "research", "devops", "design"}
STATUSES = {"todo", "in_progress", "blocked", "done"}

# A leaf has no subtasks. Only leaves are real work; a parent's status is derived from them.
IS_LEAF = "NOT EXISTS (SELECT 1 FROM tasks c WHERE c.parent_id = t.id)"


def _check(value: Any, allowed: set, label: str) -> None:
    if value not in allowed:
        raise ValueError(f"{label} must be one of {sorted(allowed)}; got {value!r}")


def _get(conn: sqlite3.Connection, task_id: int) -> sqlite3.Row:
    row = conn.execute("SELECT * FROM tasks WHERE id = ?", (task_id,)).fetchone()
    if row is None:
        raise ValueError(f"Task {task_id} not found")
    return row


def _roll_up(conn: sqlite3.Connection, parent_id: Optional[int]) -> None:
    """Walk up the tree re-deriving each parent's status: done only when ALL children are done."""
    while parent_id is not None:
        kids = [r["status"] for r in conn.execute(
            "SELECT status FROM tasks WHERE parent_id = ?", (parent_id,))]
        if not kids:
            return
        if all(s == "done" for s in kids):
            new = "done"
        elif any(s in ("in_progress", "done") for s in kids):
            new = "in_progress"
        else:
            new = "todo"
        conn.execute(
            "UPDATE tasks SET status = ?, updated_at = datetime('now'), "
            "completed_at = CASE WHEN ? = 'done' THEN datetime('now') ELSE NULL END "
            "WHERE id = ? AND status != ?",
            (new, new, parent_id, new),
        )
        parent_id = _get(conn, parent_id)["parent_id"]


def _path(conn: sqlite3.Connection, task_id: int) -> str:
    """Ancestor titles, root first, so a task is never read without its context."""
    titles, parent = [], _get(conn, task_id)["parent_id"]
    while parent is not None:
        row = _get(conn, parent)
        titles.append(row["title"])
        parent = row["parent_id"]
    return " > ".join(reversed(titles))


def add_task(conn: sqlite3.Connection, pid: int, title: str, parent_id: Optional[int] = None,
             description: str = "", task_type: str = "feature",
             estimate_hours: Optional[float] = None, urgent: bool = False,
             actor: str = "claude") -> int:
    """Insert one task at the end of the queue (or the front if urgent). Returns its id."""
    title = (title or "").strip()
    if not title:
        raise ValueError("title is required")
    _check(task_type, TYPES, "type")
    if estimate_hours is not None and estimate_hours < 0:
        raise ValueError("estimate_hours must be >= 0")
    if parent_id is not None and _get(conn, parent_id)["project_id"] != pid:
        raise ValueError("parent task belongs to a different project")

    position = conn.execute(
        "SELECT COALESCE(MAX(position), 0) + 1 FROM tasks WHERE project_id = ?", (pid,)
    ).fetchone()[0]
    cur = conn.execute(
        "INSERT INTO tasks (project_id, parent_id, title, description, type, urgent, position, "
        "estimate_hours) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (pid, parent_id, title, description, task_type, int(urgent), position, estimate_hours),
    )
    log_event(conn, pid, actor, "task_added", str(cur.lastrowid), title)
    _roll_up(conn, parent_id)  # a new child re-opens a finished parent
    return cur.lastrowid


def add_tree(conn: sqlite3.Connection, pid: int, nodes: List[Dict[str, Any]],
             parent_id: Optional[int] = None, actor: str = "claude") -> List[int]:
    """Insert a nested plan in one call. Node keys: title, description, type, estimate_hours,
    urgent, children. Returns every created id (depth-first)."""
    ids: List[int] = []
    for n in nodes:
        tid = add_task(conn, pid, n.get("title", ""), parent_id, n.get("description", ""),
                       n.get("type", "feature"), n.get("estimate_hours"),
                       bool(n.get("urgent")), actor)
        ids.append(tid)
        ids.extend(add_tree(conn, pid, n.get("children") or [], tid, actor))
    return ids


def update_task(conn: sqlite3.Connection, task_id: int, actor: str = "claude",
                status: Optional[str] = None, note: Optional[str] = None,
                hours_spent: Optional[float] = None, title: Optional[str] = None,
                description: Optional[str] = None, estimate_hours: Optional[float] = None,
                urgent: Optional[bool] = None, task_type: Optional[str] = None) -> Dict[str, Any]:
    """Change any subset of a task's fields and/or append a progress note.
    hours_spent is ADDED to the running total."""
    task = _get(conn, task_id)
    sets: Dict[str, Any] = {}  # column -> value; column names come from this function only

    if status is not None:
        _check(status, STATUSES, "status")
        if conn.execute("SELECT 1 FROM tasks WHERE parent_id = ?", (task_id,)).fetchone():
            raise ValueError("A parent task's status is derived from its subtasks; update those.")
        sets["status"] = status
    if title is not None:
        sets["title"] = title.strip()
    if description is not None:
        sets["description"] = description
    if estimate_hours is not None:
        sets["estimate_hours"] = estimate_hours
    if urgent is not None:
        sets["urgent"] = int(urgent)
    if task_type is not None:
        _check(task_type, TYPES, "type")
        sets["type"] = task_type
    if hours_spent is not None:
        if hours_spent < 0:
            raise ValueError("hours_spent must be >= 0")
        sets["spent_hours"] = task["spent_hours"] + hours_spent

    assignments = [f"{col} = ?" for col in sets] + ["updated_at = datetime('now')"]
    if status is not None:
        assignments.append("completed_at = " + ("datetime('now')" if status == "done" else "NULL"))
    conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE id = ?",
                 (*sets.values(), task_id))

    if note:
        conn.execute("INSERT INTO task_notes (task_id, actor, note) VALUES (?, ?, ?)",
                     (task_id, actor, note))
    changed = f"status -> {status}" if status else ("note" if note else "edited")
    log_event(conn, task["project_id"], actor, "task_updated", str(task_id), changed)
    _roll_up(conn, task["parent_id"])
    return dict(_get(conn, task_id))


def notes_for(conn: sqlite3.Connection, task_id: int, limit: int = 3) -> List[Dict[str, Any]]:
    rows = conn.execute(
        "SELECT actor, note, created_at FROM task_notes WHERE task_id = ? ORDER BY id DESC LIMIT ?",
        (task_id, limit))
    return [dict(r) for r in rows]


def recent_notes(conn: sqlite3.Connection, pid: int, limit: int = 10) -> List[Dict[str, Any]]:
    """Latest notes across the whole project (used by the handoff pack)."""
    rows = conn.execute(
        "SELECT n.actor, n.note, n.created_at, t.title AS task FROM task_notes n "
        "JOIN tasks t ON t.id = n.task_id WHERE t.project_id = ? ORDER BY n.id DESC LIMIT ?",
        (pid, limit))
    return [dict(r) for r in rows]


def queue(conn: sqlite3.Connection, pid: int, limit: int = 5) -> List[Dict[str, Any]]:
    """Actionable leaf tasks in work order: urgent first, then resume in-progress, then FIFO."""
    rows = conn.execute(
        f"SELECT t.* FROM tasks t WHERE t.project_id = ? AND t.status IN ('todo', 'in_progress') "
        f"AND {IS_LEAF} "
        f"ORDER BY t.urgent DESC, (t.status = 'in_progress') DESC, t.position LIMIT ?",
        (pid, limit)).fetchall()
    return [{**dict(r), "path": _path(conn, r["id"])} for r in rows]


def next_task(conn: sqlite3.Connection, pid: int) -> Optional[Dict[str, Any]]:
    """The single task to work on now, with its breadcrumb path and latest notes."""
    top = queue(conn, pid, 1)
    if not top:
        return None
    return {**top[0], "recent_notes": notes_for(conn, top[0]["id"])}


def leaves(conn: sqlite3.Connection, pid: int, status: str, limit: int = 5,
           newest_first: bool = False) -> List[Dict[str, Any]]:
    """Leaf tasks with a given status (e.g. what is in progress, what was just finished)."""
    _check(status, STATUSES, "status")
    order = "t.completed_at DESC" if newest_first else "t.position"  # one of two constants
    rows = conn.execute(
        f"SELECT t.* FROM tasks t WHERE t.project_id = ? AND t.status = ? AND {IS_LEAF} "
        f"ORDER BY {order} LIMIT ?", (pid, status, limit))
    return [{**dict(r), "path": _path(conn, r["id"])} for r in rows]


def progress(conn: sqlite3.Connection, pid: int) -> Dict[str, Any]:
    """Percent done, weighted by estimate (tasks with no estimate count as 1 hour)."""
    rows = conn.execute(
        f"SELECT t.status, COALESCE(t.estimate_hours, 1) AS w FROM tasks t "
        f"WHERE t.project_id = ? AND {IS_LEAF}", (pid,)).fetchall()
    total = sum(r["w"] for r in rows)
    done = sum(r["w"] for r in rows if r["status"] == "done")
    by_status = {s: 0 for s in sorted(STATUSES)}
    for r in rows:
        by_status[r["status"]] += 1
    spent = conn.execute("SELECT COALESCE(SUM(spent_hours), 0) FROM tasks WHERE project_id = ?",
                         (pid,)).fetchone()[0]
    return {"percent_done": round(100 * done / total, 1) if total else 0.0,
            "leaf_tasks": len(rows), "by_status": by_status, "hours_spent": round(spent, 1)}


def tree(conn: sqlite3.Connection, pid: int) -> List[Dict[str, Any]]:
    """Whole task tree as nested dicts (compact fields only, to keep AI context small)."""
    keep = ("id", "title", "status", "type", "urgent", "estimate_hours", "spent_hours")
    children: Dict[Optional[int], List[sqlite3.Row]] = {}
    for r in conn.execute("SELECT * FROM tasks WHERE project_id = ? ORDER BY position", (pid,)):
        children.setdefault(r["parent_id"], []).append(r)

    def build(parent: Optional[int]) -> List[Dict[str, Any]]:
        return [{**{k: r[k] for k in keep}, "children": build(r["id"])}
                for r in children.get(parent, [])]

    return build(None)
