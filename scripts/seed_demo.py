"""Fill a THROWAWAY database with a demo project so you can preview the dashboard without an AI.

Usage:  VIBETRACK_DB=/tmp/demo.db python scripts/seed_demo.py
        VIBETRACK_DB=/tmp/demo.db python -m vibetrack.dashboard
"""
import os
import sys

from vibetrack import decisions, projects, tasks
from vibetrack.db import connect

if "VIBETRACK_DB" not in os.environ:   # never touch the real database by accident
    sys.exit("Set VIBETRACK_DB to a throwaway file first, e.g. VIBETRACK_DB=/tmp/demo.db")

PLAN = [
    {"title": "Data pipeline", "children": [
        {"title": "Scrape sources", "estimate_hours": 4, "children": [
            {"title": "Site A scraper", "estimate_hours": 3}, {"title": "Site B scraper", "estimate_hours": 3}]},
        {"title": "Clean and store", "estimate_hours": 3},
        {"title": "Pipeline tests", "type": "test", "estimate_hours": 2}]},
    {"title": "Backend API", "children": [
        {"title": "Auth", "estimate_hours": 3, "type": "feature"},
        {"title": "Search endpoint", "estimate_hours": 5},
        {"title": "Rate limiting", "estimate_hours": 2, "type": "devops"},
        {"title": "API tests", "type": "test", "estimate_hours": 3}]},
    {"title": "Frontend", "children": [
        {"title": "Layout and routing", "estimate_hours": 4, "type": "design"},
        {"title": "Search page", "estimate_hours": 5},
        {"title": "Results table", "estimate_hours": 4}]},
    {"title": "Hardening", "children": [
        {"title": "Security review", "type": "research", "estimate_hours": 3},
        {"title": "Fix bugs from testing", "type": "bug", "estimate_hours": 6},
        {"title": "Write README", "type": "docs", "estimate_hours": 2}]},
]

with connect() as conn:
    pid = projects.create(conn, "demo", "Company search app",
                          "Search app over scraped company data.", "Scraping, API, search UI.",
                          "Python + React, minimal code, comments on the why.", 15)
    conn.execute("UPDATE projects SET created_at = datetime('now', '-6 days') WHERE id = ?", (pid,))
    ids = tasks.add_tree(conn, pid, PLAN)
    by_title = {r["title"]: r["id"] for r in conn.execute("SELECT id, title FROM tasks WHERE project_id = ?", (pid,))}

    def finish(title, days_ago, hours, actor="claude"):
        tasks.update_task(conn, by_title[title], actor, "done", f"Finished {title}.", hours)
        conn.execute("UPDATE tasks SET completed_at = datetime('now', ?) WHERE id = ?", (f"-{days_ago} days", by_title[title]))

    finish("Site A scraper", 5, 3.5); finish("Site B scraper", 4, 4.5, "gpt")
    finish("Clean and store", 3, 3); finish("Pipeline tests", 2, 2.5)
    finish("Auth", 2, 4, "gpt"); finish("Layout and routing", 1, 3)
    tasks.update_task(conn, by_title["Search endpoint"], "claude", "in_progress", "Query works, ranking still TODO.", 2.5)
    tasks.update_task(conn, by_title["Rate limiting"], "gpt", "blocked", "Waiting on decision about Redis.")
    tasks.add_task(conn, pid, "Fix crash on empty query", task_type="bug", estimate_hours=1, urgent=True)

    d1 = decisions.record(conn, pid, "Which database?", "SQLite", ["SQLite", "Postgres"], "CPU-only machine, one user")
    decisions.record(conn, pid, "Need Redis for rate limits?", "In-memory counter", ["Redis", "In-memory counter"],
                     "Avoids running another service", parent_id=d1)
    decisions.set_outcome(conn, d1, "Setup took 5 minutes; no issues so far.")

    # Spread audit-log events over the last week so the activity chart has something to show.
    conn.execute("UPDATE events SET created_at = datetime('now', '-' || (abs(random()) % 6) || ' days', "
                 "'-' || (abs(random()) % 600) || ' minutes') WHERE project_id = ? AND action != 'project_created'", (pid,))
print("Demo project 'demo' created in", os.environ["VIBETRACK_DB"])
