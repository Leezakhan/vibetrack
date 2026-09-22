"""vibetrack.init_project: rules + pointer files for every AI tool, never clobbering user files."""
import pytest

from vibetrack import init_project as ip


def test_creates_rules_and_pointers(tmp_path):
    result = ip.init(tmp_path, "my-app")
    assert result["PROJECT_RULES.md"] == "copied"
    assert (tmp_path / "PROJECT_RULES.md").read_text() == ip.RULES.read_text()
    for name in ip.POINTER_FILES:
        text = (tmp_path / name).read_text()
        assert "PROJECT_RULES.md" in text and "`my-app`" in text and "actor" in text


def test_existing_files_are_kept_and_rerun_is_idempotent(tmp_path):
    (tmp_path / "CLAUDE.md").write_text("# My own notes\nUse tabs.\n")
    (tmp_path / "PROJECT_RULES.md").write_text("my edited rules")
    first = ip.init(tmp_path)
    assert first["CLAUDE.md"] == "block appended" and first["PROJECT_RULES.md"].startswith("kept")
    text = (tmp_path / "CLAUDE.md").read_text()
    assert text.startswith("# My own notes\nUse tabs.") and ip.START in text
    assert (tmp_path / "PROJECT_RULES.md").read_text() == "my edited rules"
    second = ip.init(tmp_path)
    assert second["CLAUDE.md"] == "up to date" and (tmp_path / "CLAUDE.md").read_text() == text


def test_slug_change_refreshes_block_in_place(tmp_path):
    (tmp_path / "AGENTS.md").write_text("intro\n")
    ip.init(tmp_path, "one")
    assert ip.init(tmp_path, "two")["AGENTS.md"] == "updated"
    text = (tmp_path / "AGENTS.md").read_text()
    assert "`two`" in text and "`one`" not in text and text.count(ip.START) == 1 and text.startswith("intro")


def test_bad_input(tmp_path):
    with pytest.raises(ValueError):
        ip.init(tmp_path / "missing")
    with pytest.raises(ValueError):
        ip.init(tmp_path, "Bad Slug!")
