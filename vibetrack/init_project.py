"""Wire a project folder for every AI tool: copy the rules and add the pointer files tools auto-load.

Usage:  python -m vibetrack.init_project /path/to/project [--slug my-project]

Claude Code reads CLAUDE.md; Codex reads AGENTS.md; Antigravity/Gemini CLI read GEMINI.md and
AGENTS.md. Each gets a short marked block telling the AI to follow PROJECT_RULES.md. Your own
files are never overwritten: the block is appended once and refreshed in place on re-runs.
"""
import argparse
import re
import shutil
import sys
from pathlib import Path
from typing import Dict, Optional

from .projects import SLUG_RE

RULES = Path(__file__).with_name("PROJECT_RULES.md")
POINTER_FILES = ("CLAUDE.md", "AGENTS.md", "GEMINI.md")
START, END = "<!-- vibetrack:start -->", "<!-- vibetrack:end -->"


def _block(slug: Optional[str]) -> str:
    lines = [START, "## VibeTrack project tracking",
             "Read `PROJECT_RULES.md` in this folder and follow it on every task.",
             "This project is tracked with the `vibetrack` MCP tools. At the start of a session call "
             "`get_project_overview`, then `next_task`."]
    if slug:
        lines.append(f"Project slug: `{slug}`.")
    lines.append("Pass `actor` = your own tool name (for example `claude`, `codex`, `gemini`) on every call.")
    return "\n".join(lines + [END]) + "\n"


def _write_pointer(path: Path, block: str) -> str:
    """Create the file, or add/refresh our marked block without touching anything else."""
    if not path.exists():
        path.write_text(block)
        return "created"
    text = path.read_text()
    if START in text:
        new = re.sub(re.escape(START) + r".*?" + re.escape(END) + r"\n?", lambda _: block, text, flags=re.S)
        if new == text:
            return "up to date"
        path.write_text(new)
        return "updated"
    path.write_text(text.rstrip("\n") + "\n\n" + block)
    return "block appended"


def init(target: Path, slug: Optional[str] = None) -> Dict[str, str]:
    """Set up `target`; returns {file name: what happened}."""
    if not target.is_dir():
        raise ValueError(f"{target} is not a folder")
    if slug and not SLUG_RE.match(slug):
        raise ValueError("slug must be 2-40 chars: lowercase letters, digits, hyphens")

    done: Dict[str, str] = {}
    dst = target / RULES.name
    if not dst.exists():
        shutil.copyfile(RULES, dst)
        done[dst.name] = "copied"
    elif dst.read_text() == RULES.read_text():
        done[dst.name] = "up to date"
    else:
        done[dst.name] = "kept (your edited version differs from the built-in one)"
    block = _block(slug)
    for name in POINTER_FILES:
        done[name] = _write_pointer(target / name, block)
    return done


def main() -> None:
    ap = argparse.ArgumentParser(description="Set up a project folder for VibeTrack and all AI tools")
    ap.add_argument("folder", type=Path)
    ap.add_argument("--slug", help="the project's VibeTrack slug (so the AI does not have to ask)")
    args = ap.parse_args()
    try:
        for name, result in init(args.folder.expanduser().resolve(), args.slug).items():
            print(f"{name}: {result}")
    except ValueError as exc:
        sys.exit(f"error: {exc}")


if __name__ == "__main__":
    main()
