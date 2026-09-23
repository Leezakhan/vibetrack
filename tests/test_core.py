"""Core behaviour tests. Run: pytest -q"""
import pytest

from vibetrack import db, decisions, handoff, projects, tasks


PLAN = [
    {"title": "Backend", "children": [
        {"title": "API", "estimate_hours": 4, "children": [
            {"title": "Endpoints", "estimate_hours": 2}, {"title": "Auth", "estimate_hours": 2}]},
        {"title": "Tests", "type": "test", "estimate_hours": 2}]},
    {"title": "Frontend", "estimate_hours": 4},
]


def test_tree_rollup_and_progress(conn, pid):
    ids = tasks.add_tree(conn, pid, PLAN)
    assert len(ids) == 6
    endpoints, auth = ids[2], ids[3]
    tasks.update_task(conn, endpoints, status="done")
    assert tasks.tree(conn, pid)[0]["status"] == "in_progress"      # parent derived
    tasks.update_task(conn, auth, status="done")
    api = tasks.tree(conn, pid)[0]["children"][0]
    assert api["status"] == "done"                                   # all children done
    assert tasks.progress(conn, pid)["percent_done"] == 40.0         # 4h of 10h leaf weight
    with pytest.raises(ValueError):
        tasks.update_task(conn, ids[1], status="done")               # cannot set a parent directly


def test_queue_order_and_urgent(conn, pid):
    ids = tasks.add_tree(conn, pid, PLAN)
    assert tasks.next_task(conn, pid)["id"] == ids[2]                # first leaf in FIFO order
    tasks.update_task(conn, ids[3], status="in_progress")
    assert tasks.next_task(conn, pid)["id"] == ids[3]                # resume in-progress work
    hot = tasks.add_task(conn, pid, "Prod bug", task_type="bug", urgent=True)
    assert tasks.next_task(conn, pid)["id"] == hot                   # urgent jumps the queue
    assert tasks.next_task(conn, pid)["path"] == ""


def test_new_child_reopens_done_parent(conn, pid):
    parent = tasks.add_task(conn, pid, "Feature")
    only = tasks.add_task(conn, pid, "Step 1", parent_id=parent)
    tasks.update_task(conn, only, status="done")
    assert tasks.tree(conn, pid)[0]["status"] == "done"
    tasks.add_task(conn, pid, "Step 2", parent_id=parent)
    assert tasks.tree(conn, pid)[0]["status"] == "in_progress"


def test_validation(conn, pid):
    with pytest.raises(ValueError):
        tasks.add_task(conn, pid, "x", task_type="nonsense")
    with pytest.raises(ValueError):
        tasks.add_task(conn, pid, "   ")
    with pytest.raises(ValueError):
        projects.create(conn, "Bad Slug!", "n")
    with pytest.raises(ValueError):
        projects.create(conn, "demo", "dup")


def test_decisions_and_handoff(conn, pid):
    d1 = decisions.record(conn, pid, "DB?", "SQLite", ["SQLite", "Postgres"], "CPU-only box")
    decisions.record(conn, pid, "ORM?", "none", parent_id=d1)
    decisions.set_outcome(conn, d1, "works fine")
    t = decisions.tree(conn, pid)
    assert t[0]["children"][0]["question"] == "ORM?" and t[0]["consequences"] == "works fine"

    tid = tasks.add_task(conn, pid, "Build API", actor="gpt")
    tasks.update_task(conn, tid, status="in_progress", note="half done", actor="gpt", hours_spent=1.5)
    text = handoff.build(conn, pid)
    for expected in ("Handoff: Demo", "Build API", "half done", "SQLite", "outcome: works fine"):
        assert expected in text
    who = {c["actor"] for c in projects.overview(conn, pid)["contributors"]}
    assert "gpt" in who and "system" not in who      # the internal actor is hidden


def test_ai_rotation_and_switch(conn, pid):
    ov = projects.overview(conn, pid)
    assert ov["ai_rotation"]["current"] == "claude"       # built-in default, first in line

    projects.set_rotation(conn, pid, ["ChatGPT", " Gemini", "chatgpt", ""])   # messy input
    ov = projects.overview(conn, pid)
    assert ov["ai_rotation"] == {"tools": ["chatgpt", "gemini"], "cursor": 0, "current": "chatgpt"}

    result = projects.switch_ai(conn, pid, actor="chatgpt", reason="hit daily limit")
    assert result["you_were_using"] == "chatgpt" and result["open_next"] == "gemini"
    assert "Handoff:" in result["handoff_markdown"] and "gemini" in result["handoff_markdown"]
    assert projects.overview(conn, pid)["ai_rotation"]["current"] == "gemini"

    # wraps back to the start after the last one
    projects.switch_ai(conn, pid, actor="gemini")
    assert projects.overview(conn, pid)["ai_rotation"]["current"] == "chatgpt"

    with pytest.raises(ValueError):
        projects.set_rotation(conn, pid, ["", "  "])       # nothing usable


def test_upgrading_an_old_database_adds_missing_columns_without_losing_data(tmp_path, monkeypatch):
    import sqlite3
    old_path = tmp_path / "old.db"
    conn = sqlite3.connect(old_path)
    conn.executescript('''
        CREATE TABLE projects (
          id INTEGER PRIMARY KEY, slug TEXT UNIQUE NOT NULL, name TEXT NOT NULL,
          description TEXT NOT NULL DEFAULT '', scope TEXT NOT NULL DEFAULT '',
          conventions TEXT NOT NULL DEFAULT '', timeline_days INTEGER,
          created_at TEXT NOT NULL DEFAULT (datetime('now'))
        );
    ''')
    conn.execute("INSERT INTO projects (slug, name, timeline_days) VALUES ('old-proj', 'Old Proj', 15)")
    conn.commit(); conn.close()

    monkeypatch.setattr(db, "DB_PATH", old_path)
    with db.connect() as c:
        ov = projects.overview(c, projects.SLUG_RE and 1)  # id 1: the only row inserted above
        assert ov["project"]["slug"] == "old-proj"          # pre-existing data survives
        assert ov["ai_rotation"]["current"] == "claude"       # new column gets a sane default
    with db.connect() as c:                                  # reconnecting must not error either
        assert c.execute("SELECT ai_cursor FROM projects").fetchone()["ai_cursor"] == 0
