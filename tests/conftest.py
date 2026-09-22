"""Shared fixtures: every test gets its own throwaway database."""
import pytest

from vibetrack import db, projects


@pytest.fixture
def conn(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", tmp_path / "test.db")
    with db.connect() as c:
        yield c


@pytest.fixture
def pid(conn):
    return projects.create(conn, "demo", "Demo", "desc", "scope", "python", 15)
