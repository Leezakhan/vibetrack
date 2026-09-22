"""MCP server: exposes VibeTrack as tools any MCP-capable AI can call (stdio transport).

Run:  python -m vibetrack.server
Every tool takes the project `slug`, so one server serves all your projects.
`actor` = the calling tool's own name ('claude', 'codex', 'gemini'...); it feeds the contributor log,
so every AI must pass its own. Callers that forget show up as 'unknown'.
"""
from typing import Any, Dict, List, Optional

from mcp.server.fastmcp import FastMCP

from . import decisions, handoff, projects, tasks
from .db import connect, project_id

mcp = FastMCP("vibetrack")


# ---------- Projects ----------
@mcp.tool()
def create_project(slug: str, name: str, description: str = "", scope: str = "",
                   conventions: str = "", timeline_days: Optional[int] = None) -> Dict[str, Any]:
    """Register a new project (once, at the start). `slug` = short lowercase id like 'medrag'.
    `conventions` = stack, code style and how the user likes to work. Ask the user for
    timeline_days before planning tasks."""
    with connect() as conn:
        pid = projects.create(conn, slug, name, description, scope, conventions, timeline_days)
        return projects.overview(conn, pid)


@mcp.tool()
def update_project(project: str, name: Optional[str] = None, description: Optional[str] = None,
                   scope: Optional[str] = None, conventions: Optional[str] = None,
                   timeline_days: Optional[int] = None, actor: str = "unknown") -> Dict[str, Any]:
    """Edit project details. Only the fields you pass are changed."""
    with connect() as conn:
        pid = project_id(conn, project)
        projects.update(conn, pid, {"name": name, "description": description, "scope": scope,
                                    "conventions": conventions, "timeline_days": timeline_days}, actor)
        return projects.overview(conn, pid)


@mcp.tool()
def get_project_overview(project: str) -> Dict[str, Any]:
    """Full status in one call: scope, % done, timeline, in-progress/blocked/next/done tasks,
    contributors, decision count."""
    with connect() as conn:
        return projects.overview(conn, project_id(conn, project))


@mcp.tool()
def get_handoff_context(project: str) -> str:
    """Markdown briefing for a fresh AI taking over: project, conventions, status, decisions,
    notes and the working rules. Call this when your context is running out or on a new session."""
    with connect() as conn:
        return handoff.build(conn, project_id(conn, project))


# ---------- Tasks ----------
@mcp.tool()
def add_task(project: str, title: str, parent_id: Optional[int] = None, description: str = "",
             type: str = "feature", estimate_hours: Optional[float] = None,
             urgent: bool = False, actor: str = "unknown") -> Dict[str, Any]:
    """Add one task (or subtask via parent_id). type: feature|bug|test|refactor|docs|research|
    devops|design. urgent=true puts it at the front of the queue."""
    with connect() as conn:
        pid = project_id(conn, project)
        tid = tasks.add_task(conn, pid, title, parent_id, description, type,
                             estimate_hours, urgent, actor)
        return {"task_id": tid}


@mcp.tool()
def add_task_tree(project: str, plan: List[Dict[str, Any]], parent_id: Optional[int] = None,
                  actor: str = "unknown") -> Dict[str, Any]:
    """Add a whole nested plan in one call. Each node: {title, description?, type?,
    estimate_hours?, urgent?, children?: [...]}. Use this to plan the full timeline
    (build + tests + fixes + docs)."""
    with connect() as conn:
        ids = tasks.add_tree(conn, project_id(conn, project), plan, parent_id, actor)
        return {"created": len(ids), "task_ids": ids}


@mcp.tool()
def update_task(task_id: int, status: Optional[str] = None, note: Optional[str] = None,
                hours_spent: Optional[float] = None, title: Optional[str] = None,
                description: Optional[str] = None, estimate_hours: Optional[float] = None,
                urgent: Optional[bool] = None, type: Optional[str] = None,
                actor: str = "unknown") -> Dict[str, Any]:
    """Update a task: status (todo|in_progress|blocked|done), a progress `note`, hours_spent
    (added to the total), or any field. Always leave a note saying what was done and what's left.
    Parent status is automatic; only update leaf tasks."""
    with connect() as conn:
        return tasks.update_task(conn, task_id, actor, status, note, hours_spent, title,
                                 description, estimate_hours, urgent, type)


@mcp.tool()
def next_task(project: str) -> Dict[str, Any]:
    """The one task to work on now (urgent first, then in-progress, then queue order),
    with its parent path and latest notes."""
    with connect() as conn:
        return tasks.next_task(conn, project_id(conn, project)) or {"message": "No open tasks."}


@mcp.tool()
def get_task_queue(project: str, limit: int = 10) -> List[Dict[str, Any]]:
    """Upcoming tasks in the order they'll be worked on."""
    with connect() as conn:
        return tasks.queue(conn, project_id(conn, project), max(1, min(limit, 50)))


@mcp.tool()
def list_tasks(project: str) -> List[Dict[str, Any]]:
    """The full nested task tree with statuses and estimates."""
    with connect() as conn:
        return tasks.tree(conn, project_id(conn, project))


# ---------- Decisions ----------
@mcp.tool()
def record_decision(project: str, question: str, chosen: str, options: Optional[List[str]] = None,
                    rationale: str = "", consequences: str = "",
                    parent_decision_id: Optional[int] = None, actor: str = "unknown") -> Dict[str, Any]:
    """Log a decision the user made (question, options offered, what they chose, why).
    parent_decision_id nests it under the earlier decision it follows from."""
    with connect() as conn:
        did = decisions.record(conn, project_id(conn, project), question, chosen, options,
                               rationale, consequences, parent_decision_id, actor)
        return {"decision_id": did}


@mcp.tool()
def update_decision_outcome(decision_id: int, consequences: str, actor: str = "unknown") -> str:
    """Record what actually happened after a decision was carried out."""
    with connect() as conn:
        decisions.set_outcome(conn, decision_id, consequences, actor)
    return "ok"


@mcp.tool()
def get_decision_tree(project: str) -> List[Dict[str, Any]]:
    """All decisions as a nested tree with their outcomes."""
    with connect() as conn:
        return decisions.tree(conn, project_id(conn, project))


# ---------- Switching AI tools (free-tier quota rotation) ----------
@mcp.tool()
def switch_ai(project: str, reason: str = "", actor: str = "unknown") -> Dict[str, Any]:
    """Call this the moment you (or the user) hit a usage limit and need to switch tools. Advances
    to the next AI in the project's rotation and returns its name plus the full handoff briefing,
    so the only step left for the user is opening that tool and pasting/attaching the briefing.
    Write the same briefing to HANDOFF.md too (see PROJECT_RULES.md), then tell the user which
    tool to open next."""
    with connect() as conn:
        return projects.switch_ai(conn, project_id(conn, project), actor, reason)


@mcp.tool()
def set_ai_rotation(project: str, tools: List[str], actor: str = "claude") -> Dict[str, Any]:
    """Set which AI tools to rotate through when one hits a limit, in the order to try them
    (e.g. ["claude", "codex", "gemini", "chatgpt"]). Ask the user which tools they actually have
    available before setting this, rather than assuming."""
    with connect() as conn:
        return projects.set_rotation(conn, project_id(conn, project), tools, actor)


def main() -> None:
    mcp.run()  # stdio: the AI client launches this process itself


if __name__ == "__main__":
    main()
