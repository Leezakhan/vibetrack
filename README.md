# VibeTrack

Persistent project memory for AI-assisted coding: nested tasks, an urgent-aware queue, a decision
tree, and a one-call handoff so any AI can continue where another stopped.

**Phase 1:** SQLite store + MCP tools + `PROJECT_RULES.md` + handoff.
**Phase 2:** live dashboard with charts (read-only, localhost).
Next: Phase 3 git tracker.

## Setup (Linux/macOS)
```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest -q                      # sanity check
```

## Connect it to an AI (local MCP, stdio)
Claude Code:
```bash
claude mcp add vibetrack -- /ABS/PATH/.venv/bin/python -m vibetrack.server
```
Claude Desktop (`claude_desktop_config.json`):
```json
{ "mcpServers": { "vibetrack": {
    "command": "/ABS/PATH/.venv/bin/python", "args": ["-m", "vibetrack.server"] } } }
```

## Use it on a new project
1. Give the AI two things: your project doc + `vibetrack/PROJECT_RULES.md`.
2. It creates the project, asks your timeline, and plans every task.
3. Each session: it calls `next_task`, works, then `update_task` with notes.
4. Switching AI or out of limit? Run `python -m vibetrack.handoff <slug> > HANDOFF.md`
   and paste it to the new AI (or let it call `get_handoff_context`).

Data lives in `~/.vibetrack/vibetrack.db` (set `VIBETRACK_DB` to change).

## Dashboard (Phase 2)
```bash
python -m vibetrack.dashboard          # then open http://127.0.0.1:8765
```
It refreshes itself every 5 seconds, so you can watch the AI work. Shows: schedule status in one
sentence, plan-vs-actual chart, what's in progress / blocked / next, hours by area, tasks by type,
who changed the tracker, the full task tree, decisions, and a button that copies the handoff.

Preview it without any AI, using a throwaway database:
```bash
export VIBETRACK_DB=/tmp/demo.db
python scripts/seed_demo.py && python -m vibetrack.dashboard
unset VIBETRACK_DB                     # afterwards, so real work goes to the real database
```
The dashboard is read-only, listens on localhost only, and shows AI-written text as plain text
(never as HTML). Same data as the MCP tools: `GET /api/projects/<slug>/overview|charts|tasks|decisions|handoff`.

## Use it with other AI tools (Codex, Antigravity/Gemini, Cursor, ...)
Every tool that supports **local (stdio) MCP servers** uses the same command as Claude Code:
`/ABS/PATH/.venv/bin/python -m vibetrack.server`. They all share one database, so a project started in
one AI can be continued in another (use the handoff). Run one AI per project at a time.

| Tool | How to add it |
|---|---|
| Codex CLI and the Codex VS Code extension | Add to `~/.codex/config.toml`: `[mcp_servers.vibetrack]` with `command = "/ABS/PATH/.venv/bin/python"` and `args = ["-m", "vibetrack.server"]`. Check with `codex mcp list`, or `/mcp` inside Codex. |
| Antigravity CLI (`agy`, replaced Gemini CLI for free/Pro/Ultra accounts in June 2026) | `agy mcp add vibetrack -- /ABS/PATH/.venv/bin/python -m vibetrack.server`, then `agy mcp list`. |
| Gemini CLI (still works with an API key or Vertex AI) | In `~/.gemini/settings.json`: `{"mcpServers": {"vibetrack": {"command": "/ABS/PATH/.venv/bin/python", "args": ["-m", "vibetrack.server"]}}}` |
| Cursor, Cline, other MCP editors | Add a stdio server with the same command in their MCP settings. |
| ChatGPT web, Gemini web, claude.ai | **Not supported.** They only connect to remote HTTPS servers. Use the dashboard's "Copy handoff" button to give them context (read-only). |

Tell each tool about the rules by running this once per project folder:
```bash
python -m vibetrack.init_project ~/path/to/project --slug my-project
```
It copies `PROJECT_RULES.md` and adds a short block to `CLAUDE.md`, `AGENTS.md` and `GEMINI.md`, which
those tools load automatically. Existing files are kept; re-running is safe. Every AI passes its own
name as `actor`, so the dashboard shows who did what (callers that forget appear as `unknown`).

## Remote access for ChatGPT and Gemini (Phase 2.3)
ChatGPT's website and Gemini's website can't run a program on your machine — they only connect to
a server with a public URL. `vibetrack/remote_server.py` is the same tools as `server.py`, served
over HTTPS instead of stdio, with a required secret token.

**This is not a dummy REST API you call with plain GET/POST** — ChatGPT and Gemini's connectors
speak the MCP protocol (JSON-RPC over one `/mcp` endpoint), so a hand-rolled `/data` style API,
or a GitHub Gist as a "tunnel", won't work as a connector. A Gist is also unsuited to this: it has
no live two-way connection, and posting to it needs your own GitHub token in every request.

### Deploy to Render
1. Push this repo to GitHub.
2. On Render: New → Blueprint → point it at the repo. `render.yaml` sets up the build and start
   commands and a free-tier web service.
3. Render generates `VIBETRACK_TOKEN` for you — copy it from the service's Environment tab.
4. **Read this before you rely on it:** Render's free tier does not keep a persistent disk. Every
   deploy and restart wipes local files, so `VIBETRACK_DB` (a SQLite file) would lose your tasks.
   Either add a paid persistent disk in Render and point `VIBETRACK_DB` at a path on it, or treat
   the remote server as read-mostly and keep doing real work through a local tool (Claude Code,
   Codex, Antigravity) against your local database.

### Connect it
Your URL is `https://<your-service>.onrender.com/mcp?token=<your-token>`.

- **ChatGPT:** Settings → Apps & Connectors → Advanced → enable Developer mode → Create connector
  → paste the URL above → No authentication (the token is already in the URL).
- **Gemini CLI / Antigravity:** add to `~/.gemini/settings.json` (or `agy mcp add --type http`):
  `{"mcpServers": {"vibetrack": {"httpUrl": "https://<your-service>.onrender.com/mcp", "headers": {"Authorization": "Bearer <your-token>"}}}}`

### Test it locally first
```bash
VIBETRACK_TOKEN=devsecret VIBETRACK_DB=/tmp/remote.db uvicorn vibetrack.remote_server:app --port 8080
curl "http://127.0.0.1:8080/mcp"                        # expect 401, no token
curl "http://127.0.0.1:8080/mcp?token=devsecret" -X POST -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"test","version":"0"}}}'
```
