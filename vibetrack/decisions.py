"""Decision tree: what was decided, why, and what it led to."""
import json
import sqlite3
from typing import Any, Dict, List, Optional

from .db import log_event


def record(conn: sqlite3.Connection, pid: int, question: str, chosen: str,
           options: Optional[List[str]] = None, rationale: str = "", consequences: str = "",
           parent_id: Optional[int] = None, actor: str = "claude") -> int:
    """Store a decision. parent_id links it under the earlier decision it follows from."""
    if not question.strip() or not chosen.strip():
        raise ValueError("question and chosen are required")
    if parent_id is not None:
        row = conn.execute("SELECT project_id FROM decisions WHERE id = ?", (parent_id,)).fetchone()
        if row is None or row["project_id"] != pid:
            raise ValueError(f"Parent decision {parent_id} not found in this project")
    cur = conn.execute(
        "INSERT INTO decisions (project_id, parent_id, question, options, chosen, rationale, "
        "consequences) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (pid, parent_id, question, json.dumps(options or []), chosen, rationale, consequences))
    log_event(conn, pid, actor, "decision_recorded", str(cur.lastrowid), question)
    return cur.lastrowid


def set_outcome(conn: sqlite3.Connection, decision_id: int, consequences: str,
                actor: str = "claude") -> None:
    """Fill in what actually happened after a decision was carried out."""
    row = conn.execute("SELECT project_id FROM decisions WHERE id = ?", (decision_id,)).fetchone()
    if row is None:
        raise ValueError(f"Decision {decision_id} not found")
    conn.execute("UPDATE decisions SET consequences = ? WHERE id = ?", (consequences, decision_id))
    log_event(conn, row["project_id"], actor, "decision_outcome", str(decision_id), consequences[:80])


def tree(conn: sqlite3.Connection, pid: int) -> List[Dict[str, Any]]:
    """All decisions as a nested tree (roots first, children in the order they were made)."""
    children: Dict[Optional[int], List[Dict[str, Any]]] = {}
    for r in conn.execute("SELECT * FROM decisions WHERE project_id = ? ORDER BY id", (pid,)):
        d = dict(r)
        d["options"] = json.loads(d["options"])
        children.setdefault(d["parent_id"], []).append(d)

    def build(parent: Optional[int]) -> List[Dict[str, Any]]:
        return [{**d, "children": build(d["id"])} for d in children.get(parent, [])]

    return build(None)
