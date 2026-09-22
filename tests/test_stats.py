"""Chart data and dashboard API tests."""
import re
from datetime import date

import pytest
from starlette.testclient import TestClient

from vibetrack import decisions, stats, tasks
from vibetrack.dashboard import app

TODAY = date(2026, 9, 10)


def _leaf(conn, pid, title, hours, done_on=None, **kw):
    tid = tasks.add_task(conn, pid, title, estimate_hours=hours, **kw)
    if done_on:
        tasks.update_task(conn, tid, status="done")
        conn.execute("UPDATE tasks SET completed_at = ? WHERE id = ?", (done_on + " 12:00:00", tid))
    return tid


def test_burndown_actual_vs_plan(conn, pid):
    conn.execute("UPDATE projects SET created_at = '2026-09-06 08:00:00', timeline_days = 10 WHERE id = ?", (pid,))
    _leaf(conn, pid, "A", 4, "2026-09-07"); _leaf(conn, pid, "B", 6, "2026-09-09"); _leaf(conn, pid, "C", 10)
    b = stats.burndown(conn, pid, TODAY)
    assert len(b["days"]) == 11 and b["today_index"] == 4 and b["total_hours"] == 20
    assert b["actual"][:6] == [20, 16, 16, 10, 10, None]      # future days have no actual
    assert b["ideal"][4] == 12 and b["ideal"][10] == 0
    assert b["variance_hours"] == -2                          # 2h ahead of plan


def test_burndown_extends_past_deadline(conn, pid):
    conn.execute("UPDATE projects SET created_at = '2026-09-05 08:00:00', timeline_days = 2 WHERE id = ?", (pid,))
    _leaf(conn, pid, "A", 5)
    b = stats.burndown(conn, pid, TODAY)
    assert len(b["days"]) == 6 and b["variance_hours"] == 5    # late: nothing done, plan is at zero


def test_burndown_without_timeline_or_tasks(conn):
    from vibetrack import projects
    pid2 = projects.create(conn, "free", "Free")
    b = stats.burndown(conn, pid2, TODAY)
    assert b["ideal"] is None and b["variance_hours"] is None and b["total_hours"] == 0


def test_epics_do_not_double_count_parent_estimates(conn, pid):
    parent = tasks.add_task(conn, pid, "Backend", estimate_hours=99)      # stale parent estimate
    a = tasks.add_task(conn, pid, "API", parent_id=parent, estimate_hours=3)
    tasks.add_task(conn, pid, "DB", parent_id=parent, estimate_hours=2)
    tasks.update_task(conn, a, hours_spent=1.5)
    assert stats.epics(conn, pid) == [{"title": "Backend", "estimate": 5.0, "spent": 1.5}]


def test_activity_and_types(conn, pid):
    tasks.add_task(conn, pid, "T1", actor="claude", task_type="bug")
    tasks.add_task(conn, pid, "T2", actor="gpt")
    conn.execute("UPDATE events SET created_at = '2026-09-09 10:00:00'")
    a = stats.activity(conn, pid, TODAY)
    assert len(a["days"]) == 14 and a["days"][-1] == "2026-09-10"
    assert {s["actor"] for s in a["series"]} == {"claude", "gpt"}      # 'system' is hidden
    assert sum(sum(s["counts"]) for s in a["series"]) == 2
    assert {t["type"] for t in stats.by_type(conn, pid)} == {"bug", "feature"}


@pytest.fixture
def client(conn, pid):
    tasks.add_task(conn, pid, "<img src=x onerror=alert(1)>")             # hostile title must stay text
    decisions.record(conn, pid, "DB?", "SQLite")
    conn.commit()                      # the app opens its own connection, so the data must be committed
    return TestClient(app, base_url="http://localhost")


def test_api_endpoints(client):
    assert client.get("/api/projects").json()[0]["slug"] == "demo"
    for name in ("overview", "charts", "tasks", "decisions", "handoff"):
        assert client.get(f"/api/projects/demo/{name}").status_code == 200
    assert "Handoff: Demo" in client.get("/api/projects/demo/handoff").json()["markdown"]
    assert client.get("/api/projects/nope/overview").status_code == 404


def test_dashboard_is_read_only_and_locked_down(client):
    assert client.post("/api/projects").status_code == 405                # GET/HEAD only
    assert client.get("/api/projects", headers={"Host": "evil.example.com"}).status_code == 400
    page = client.get("/")
    assert page.status_code == 200 and "script-src 'self'" in page.headers["content-security-policy"]
    js = client.get("/app.js").text                                        # untrusted text is never parsed as HTML
    assert not re.search(r"\.(inner|outer)HTML\b|insertAdjacentHTML|document\.write", js)
