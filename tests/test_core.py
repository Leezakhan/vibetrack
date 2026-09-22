"""Core behaviour tests. Run: pytest -q"""
import pytest

from vibetrack import decisions, handoff, projects, tasks


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
