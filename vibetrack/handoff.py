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


def push(text: str, slug: str, url: str, token: str, who: str = "cli") -> str:
    """POST the briefing to a remote VibeTrack server's /handoff box; returns its reply."""
    import json
    import urllib.error
    import urllib.parse
    import urllib.request

    if not url.startswith(("https://", "http://127.0.0.1", "http://localhost")):
        raise ValueError("refusing to send a handoff over plain http to a non-local address")
    endpoint = f"{url.rstrip('/')}/handoff?" + urllib.parse.urlencode({"project": slug, "token": token})
    # Hosts like Render sit behind Cloudflare, which rejects Python's default "Python-urllib"
    # User-Agent with a 403 before the request ever reaches our server. curl isn't blocked, which
    # is why the same request works there. A plain, honest name of our own avoids it.
    req = urllib.request.Request(endpoint, data=json.dumps({"markdown": text, "pushed_by": who}).encode(),
                                 headers={"Content-Type": "application/json",
                                          "User-Agent": "vibetrack-cli/1.0"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as res:
            return res.read().decode()
    except urllib.error.HTTPError as exc:                    # keep the server's own explanation
        detail = exc.read().decode(errors="replace")[:300].strip()
        raise RuntimeError(f"HTTP {exc.code}: {detail or exc.reason}") from None


def main() -> None:
    import argparse
    import os

    ap = argparse.ArgumentParser(description="Build the handoff briefing for a project")
    ap.add_argument("slug")
    ap.add_argument("--out", help="write the briefing to this file instead of printing it")
    ap.add_argument("--push", metavar="URL", help="also push it to a remote VibeTrack server, e.g. "
                    "https://your-app.onrender.com (token from $VIBETRACK_REMOTE_TOKEN)")
    ap.add_argument("--as", dest="who", default="cli", help="name to record as the pusher (default: cli)")
    args = ap.parse_args()

    with connect() as conn:
        text = build(conn, project_id(conn, args.slug))
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out}")
    if args.push:
        token = os.environ.get("VIBETRACK_REMOTE_TOKEN")
        if not token:
            sys.exit("Set VIBETRACK_REMOTE_TOKEN to your remote server's token first "
                     "(kept in an env var so it never lands in shell history or a repo).")
        try:
            print(push(text, args.slug, args.push, token, args.who))
        except Exception as exc:
            sys.exit(f"push failed: {exc}")
    if not args.out and not args.push:
        print(text)


if __name__ == "__main__":
    main()
