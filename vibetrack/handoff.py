"""Builds the markdown context pack that lets a fresh AI (zero prior knowledge) continue a project.

CLI:  python -m vibetrack.handoff <project-slug> > HANDOFF.md
"""
import sqlite3
import sys
from pathlib import Path
from typing import Any, Dict, Iterator, List

from . import decisions, projects, tasks
from .db import connect, project_id

RULES_FILE = Path(__file__).with_name("PROJECT_RULES.md")


def _task_line(t: Dict[str, Any]) -> str:
    where = f" ({t['path']})" if t.get("path") else ""
    est = f", est {t['estimate_hours']}h" if t.get("estimate_hours") is not None else ""
    flag = " **URGENT**" if t.get("urgent") else ""
    return f"- #{t['id']} [{t['type']}] {t['title']}{where}{est}{flag}"


def _decision_lines(nodes: List[Dict[str, Any]], depth: int = 0) -> Iterator[str]:
    for d in nodes:
        pad = "  " * depth
        why = f" — {d['rationale']}" if d["rationale"] else ""
        yield f"{pad}- **{d['question']}** -> {d['chosen']}{why}"
        if d["consequences"]:
            yield f"{pad}  - outcome: {d['consequences']}"
        yield from _decision_lines(d["children"], depth + 1)


def build(conn: sqlite3.Connection, pid: int) -> str:
    """Render the full handoff document for one project."""
    ov = projects.overview(conn, pid)
    p, prog, tl = ov["project"], ov["progress"], ov["timeline"]
    days = f"day {tl['days_elapsed']}" + (f" of {tl['planned_days']}" if "planned_days" in tl else "")

    rot = ov["ai_rotation"]
    out = [
        f"# Handoff: {p['name']} (`{p['slug']}`)",
        f"You are **{rot['current']}**, taking over an in-progress project. Read everything, "
        "then call `next_task`. If you hit a usage limit yourself, call `switch_ai`.",
        f"Tool rotation: {' -> '.join(rot['tools'])} (currently at `{rot['current']}`)",
        "",
        "## Project", p["description"] or "_not set_", "",
        "### Scope", p["scope"] or "_not set_", "",
        "### Conventions (stack, code style, how the user works with AI)",
        p["conventions"] or "_not set_", "",
        f"## Status: {prog['percent_done']}% done — {days}",
        f"Tasks: {prog['by_status']} | hours spent: {prog['hours_spent']}", "",
        "## In progress (resume these first)",
        *([_task_line(t) for t in ov["in_progress"]] or ["_nothing in progress_"]), "",
        "## Blocked",
        *([_task_line(t) for t in ov["blocked"]] or ["_none_"]), "",
        "## Next up (in queue order)",
        *([_task_line(t) for t in ov["next_up"]] or ["_queue is empty_"]), "",
        "## Recently done",
        *([_task_line(t) for t in ov["recently_done"]] or ["_nothing yet_"]), "",
        "## Decisions so far (question -> choice; indented = follows from parent)",
        *(list(_decision_lines(decisions.tree(conn, pid))) or ["_none recorded_"]), "",
        "## Latest progress notes",
        *([f"- {n['created_at']} [{n['actor']}] {n['task']}: {n['note']}"
           for n in tasks.recent_notes(conn, pid)] or ["_none_"]), "",
    ]
    if RULES_FILE.exists():
        out += ["---", "## Working rules (follow these)", RULES_FILE.read_text()]
    return "\n".join(out)


def main() -> None:
    if len(sys.argv) not in (2, 4) or (len(sys.argv) == 4 and sys.argv[2] != "--out"):
        sys.exit("usage: python -m vibetrack.handoff <project-slug> [--out <file>]")
    with connect() as conn:
        text = build(conn, project_id(conn, sys.argv[1]))
    if len(sys.argv) == 4:
        Path(sys.argv[3]).write_text(text)
        print(f"Wrote {sys.argv[3]}")
    else:
        print(text)


if __name__ == "__main__":
    main()
