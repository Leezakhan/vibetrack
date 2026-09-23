"""SQLite access layer: connection helper, schema, and two tiny shared helpers.

Stdlib only. The DB lives at ~/.vibetrack/vibetrack.db (override with VIBETRACK_DB).
Every query in this package uses bound parameters; never build SQL from user input.
"""
import os
import sqlite3
from contextlib import contextmanager
from pathlib import Path

DB_PATH = Path(os.environ.get("VIBETRACK_DB", Path.home() / ".vibetrack" / "vibetrack.db"))

# All timestamps are UTC (SQLite's datetime('now')).
SCHEMA = """
CREATE TABLE IF NOT EXISTS projects (
  id            INTEGER PRIMARY KEY,
  slug          TEXT UNIQUE NOT NULL,
  name          TEXT NOT NULL,
  description   TEXT NOT NULL DEFAULT '',
  scope         TEXT NOT NULL DEFAULT '',
  conventions   TEXT NOT NULL DEFAULT '',   -- stack, code style, how the user works with AI
  timeline_days INTEGER,
  ai_rotation   TEXT NOT NULL DEFAULT '["claude","codex","gemini","chatgpt"]',  -- JSON list, tried in order
  ai_cursor     INTEGER NOT NULL DEFAULT 0,   -- index into ai_rotation of the AI currently in use
  created_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

-- One table for the whole task tree: parent_id nests tasks to any depth.
-- Only leaf tasks (no children) are real work; a parent's status is derived from its children.
CREATE TABLE IF NOT EXISTS tasks (
  id             INTEGER PRIMARY KEY,
  project_id     INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id      INTEGER REFERENCES tasks(id) ON DELETE CASCADE,
  title          TEXT NOT NULL,
  description    TEXT NOT NULL DEFAULT '',
  type           TEXT NOT NULL DEFAULT 'feature',
  status         TEXT NOT NULL DEFAULT 'todo',
  urgent         INTEGER NOT NULL DEFAULT 0,   -- 1 = jumps the queue
  position       INTEGER NOT NULL,             -- queue order within the project
  estimate_hours REAL,
  spent_hours    REAL NOT NULL DEFAULT 0,
  created_at     TEXT NOT NULL DEFAULT (datetime('now')),
  updated_at     TEXT NOT NULL DEFAULT (datetime('now')),
  completed_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_tasks_project ON tasks(project_id, status);
CREATE INDEX IF NOT EXISTS idx_tasks_parent  ON tasks(parent_id);

CREATE TABLE IF NOT EXISTS task_notes (
  id         INTEGER PRIMARY KEY,
  task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  actor      TEXT NOT NULL,
  note       TEXT NOT NULL,
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_notes_task ON task_notes(task_id);

-- Decision tree: parent_id = the earlier decision this one follows from.
CREATE TABLE IF NOT EXISTS decisions (
  id           INTEGER PRIMARY KEY,
  project_id   INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  parent_id    INTEGER REFERENCES decisions(id) ON DELETE CASCADE,
  question     TEXT NOT NULL,
  options      TEXT NOT NULL DEFAULT '[]',     -- JSON list of the options that were on the table
  chosen       TEXT NOT NULL,
  rationale    TEXT NOT NULL DEFAULT '',
  consequences TEXT NOT NULL DEFAULT '',       -- filled in later: what actually happened
  created_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

-- Append-only audit log: powers "who worked on it" now and charts in Phase 2.
CREATE TABLE IF NOT EXISTS events (
  id         INTEGER PRIMARY KEY,
  project_id INTEGER NOT NULL REFERENCES projects(id) ON DELETE CASCADE,
  actor      TEXT NOT NULL,
  action     TEXT NOT NULL,
  ref        TEXT NOT NULL DEFAULT '',
  detail     TEXT NOT NULL DEFAULT '',
  created_at TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_events_project ON events(project_id, created_at);
"""


# Columns added to `projects` after it first shipped. CREATE TABLE IF NOT EXISTS only creates a
# table that doesn't exist yet — it never adds a column to one a person already has on disk. Each
# entry here is added with ALTER TABLE if a database predates it, so an old vibetrack.db keeps
# working after an upgrade instead of raising "no such column".
_PROJECT_COLUMNS_ADDED_SINCE_1_0 = [
    ("ai_rotation", 'TEXT NOT NULL DEFAULT \'["claude","codex","gemini","chatgpt"]\''),
    ("ai_cursor", "INTEGER NOT NULL DEFAULT 0"),
]


def _migrate(conn: sqlite3.Connection) -> None:
    have = {row["name"] for row in conn.execute("PRAGMA table_info(projects)")}
    for name, coltype in _PROJECT_COLUMNS_ADDED_SINCE_1_0:
        if name not in have:
            conn.execute(f"ALTER TABLE projects ADD COLUMN {name} {coltype}")


@contextmanager
def connect():
    """Yield a connection; commit on success, roll back on error, always close."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")  # lets a dashboard read while the AI writes
    conn.executescript(SCHEMA)                 # creates tables that don't exist yet
    _migrate(conn)                             # adds columns to tables that already exist
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def project_id(conn: sqlite3.Connection, slug: str) -> int:
    """Resolve a project slug to its id, with an error the AI can act on."""
    row = conn.execute("SELECT id FROM projects WHERE slug = ?", (slug,)).fetchone()
    if row is None:
        raise ValueError(f"Unknown project '{slug}'. Call create_project first.")
    return row["id"]


def log_event(conn: sqlite3.Connection, pid: int, actor: str, action: str,
              ref: str = "", detail: str = "") -> None:
    """Append one line to the audit log."""
    conn.execute(
        "INSERT INTO events (project_id, actor, action, ref, detail) VALUES (?, ?, ?, ?, ?)",
        (pid, actor, action, ref, detail),
    )
