"""Bundle a project's source files into one plain-text document an AI can read directly — no zip,
no manual attach. Paste the output, or push it alongside the handoff (see remote_server.py /code).

CLI:  python -m vibetrack.codebundle [path] [--out CODE.md] [--max-bytes N]
Default path is the current directory, so run this from inside the project you're bundling.
"""
import argparse
import re
import sys
from pathlib import Path
from typing import List, Tuple

# Extensions worth an AI reading. Binary/media/lockfiles are never useful as pasted text.
INCLUDE_EXTENSIONS = {
    ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".scss", ".json", ".md", ".txt",
    ".toml", ".yaml", ".yml", ".sql", ".sh", ".env.example", ".ini", ".cfg",
}
# Matched against any path component — catches the folder anywhere in the tree, not just at root.
EXCLUDE_DIR_NAMES = {
    ".git", ".venv", "venv", "env", "node_modules", "__pycache__", ".pytest_cache",
    "dist", "build", ".next", ".cache", "vibetrack.egg-info", ".mypy_cache",
}
# Filenames that are secrets or local machine state by convention, whatever their extension.
EXCLUDE_FILENAME_PATTERNS = (
    re.compile(r"^\.env(\..*)?$"), re.compile(r".*\.(db|sqlite3?|log|pem|key|p12|pfx)$"),
    re.compile(r"^(id_rsa|id_ed25519)(\.pub)?$"),
)
DEFAULT_MAX_BYTES = 400_000  # keeps a single push well under the remote server's request-size cap

# Catches "API_KEY = '...'" style assignments and known key prefixes, so a secret pasted into a
# chat isn't leaked even if it slipped past the .env exclusion above (e.g. hardcoded in a .py file).
_SECRET_LINE = re.compile(
    r"""(?ix)
    ( (?:api[_-]?key|secret|token|password|access[_-]?key) \s*[:=]\s* ['"] ) [^'"]{6,} (['"])
    | (AIza[0-9A-Za-z_-]{35})
    | (sk-[A-Za-z0-9]{20,})
    """
)


def _redact(text: str) -> Tuple[str, int]:
    count = 0
    def repl(m: "re.Match") -> str:
        nonlocal count
        count += 1
        return (m.group(1) + "***REDACTED***" + m.group(2)) if m.group(1) else "***REDACTED***"
    return _SECRET_LINE.sub(repl, text), count


def _should_skip_dir(path: Path) -> bool:
    return any(part in EXCLUDE_DIR_NAMES for part in path.parts)


def _should_skip_file(path: Path) -> bool:
    if any(pat.match(path.name) for pat in EXCLUDE_FILENAME_PATTERNS):
        return True
    return path.suffix not in INCLUDE_EXTENSIONS and path.name not in INCLUDE_EXTENSIONS


def build(root: Path, max_bytes: int = DEFAULT_MAX_BYTES) -> str:
    """Walk `root` and return one Markdown document: a file tree, then each file's contents in a
    fenced block, stopping once `max_bytes` of content is reached. Secret-looking values inside
    files are redacted, but this is a safety net, not a substitute for excluding real secret
    files — check the "files skipped" list before trusting nothing sensitive is inside."""
    root = root.resolve()
    if not root.is_dir():
        raise ValueError(f"{root} is not a folder")

    files = sorted(p for p in root.rglob("*") if p.is_file() and not _should_skip_dir(p.relative_to(root).parent))
    included: List[Path] = [p for p in files if not _should_skip_file(p.relative_to(root))]

    parts = [f"# Code bundle: {root.name}", "", "## Files", ""]
    parts += [f"- `{p.relative_to(root)}`" for p in included] or ["_(nothing matched)_"]
    parts += ["", "## Contents", ""]

    used = sum(len(l) + 1 for l in parts)
    redacted_total = 0
    stopped_early = []
    for p in included:
        rel = p.relative_to(root)
        try:
            text = p.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue  # binary or unreadable; skip rather than emit garbage
        text, n = _redact(text)
        redacted_total += n
        lang = p.suffix.lstrip(".") or "text"
        block = f"### `{rel}`\n\n```{lang}\n{text}\n```\n\n"
        if used + len(block) > max_bytes:
            stopped_early.append(str(rel))
            continue
        parts.append(block)
        used += len(block)

    if stopped_early:
        parts.append(f"_(stopped at the {max_bytes}-byte limit; not included: "
                     f"{', '.join(stopped_early[:20])}{'...' if len(stopped_early) > 20 else ''})_")
    if redacted_total:
        parts.append(f"_({redacted_total} value(s) that looked like secrets were redacted.)_")
    return "\n".join(parts)


def main() -> None:
    ap = argparse.ArgumentParser(description="Bundle a project's source into one text document")
    ap.add_argument("path", nargs="?", default=".", type=Path)
    ap.add_argument("--out", help="write to this file instead of printing")
    ap.add_argument("--max-bytes", type=int, default=DEFAULT_MAX_BYTES)
    args = ap.parse_args()
    try:
        text = build(args.path, args.max_bytes)
    except ValueError as exc:
        sys.exit(f"error: {exc}")
    if args.out:
        Path(args.out).write_text(text)
        print(f"Wrote {args.out} ({len(text)} chars)")
    else:
        print(text)


if __name__ == "__main__":
    main()
