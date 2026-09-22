"""Same vibetrack tools, reachable over the internet, for AIs that only speak remote MCP
(ChatGPT Developer Mode, Gemini/Antigravity with a serverUrl, Cursor's remote connectors, ...).

Deploy this — not server.py — to a host like Render. server.py (stdio) is for tools running on
your own machine, like Claude Code, Codex CLI or Antigravity CLI; this file is for tools that
can't launch a local process and only take a URL.

*** READ THIS BEFORE DEPLOYING ***
1. SECURITY: this exposes read/write access to every project in your database to anyone who has
   the URL. Set VIBETRACK_TOKEN (a long random string) and keep it secret. Requests without the
   matching token get 401, before they reach any tool.
2. STORAGE: most free hosts (Render's free tier included) wipe local disk on every deploy and
   restart. VIBETRACK_DB defaults to a local SQLite file, which would then lose your data. Either
   pay for a persistent disk, or point VIBETRACK_DB at a database that survives restarts.

Run locally:   VIBETRACK_TOKEN=devsecret uvicorn vibetrack.remote_server:app --port 8080
Then:          https://your-host/mcp?token=devsecret  (or header Authorization: Bearer devsecret)
"""
import os
import sys

from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse
from starlette.routing import Mount, Route
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.transport_security import TransportSecuritySettings

from . import handoff, tasks
from .db import connect, project_id
from .server import mcp

TOKEN = os.environ.get("VIBETRACK_TOKEN")
# The MCP SDK has its own Host/Origin check, separate from our token check below, meant to stop a
# malicious webpage's browser from being tricked into calling a server on your local machine
# ("DNS rebinding"). It only matters for that scenario; our token check already gates every
# request here, so by default we turn it off rather than hard-code a hostname that would break on
# Render's next redeploy (or your next custom domain). Set VIBETRACK_ALLOWED_HOSTS
# (comma-separated hostnames, e.g. "vibetrack-ssz4.onrender.com") for defense-in-depth instead.
_hosts = os.environ.get("VIBETRACK_ALLOWED_HOSTS", "")
mcp.settings.transport_security = TransportSecuritySettings(
    enable_dns_rebinding_protection=bool(_hosts),
    allowed_hosts=[h.strip() for h in _hosts.split(",") if h.strip()],
    allowed_origins=[h.strip() for h in _hosts.split(",") if h.strip()],
)
if not TOKEN:
    sys.exit("VIBETRACK_TOKEN is not set. Pick a long random string and set it as an environment "
             "variable before starting this server — it's the password that protects your data.")


# ---------- Plain JSON/text routes ----------
# For tools that can only fetch a URL and read the response (a plain "visit this page" browsing
# capability, like ChatGPT's free-tier web search, or your own script/shortcut) rather than speak
# the MCP protocol above. Same data, same token gate, just plain GET/POST instead of JSON-RPC.
# `project` defaults to your only project when you have exactly one, so the URL can stay short.

def _resolve_project(conn, request: Request) -> int:
    slug = request.query_params.get("project")
    if slug:
        return project_id(conn, slug)
    rows = conn.execute("SELECT slug FROM projects").fetchall()
    if len(rows) == 1:
        return project_id(conn, rows[0]["slug"])
    names = ", ".join(r["slug"] for r in rows) or "(none yet)"
    raise ValueError(f"pass ?project=<slug> — you have {len(rows)} projects: {names}")


async def plain_task(request: Request) -> JSONResponse:
    """GET: the next task to do, in plain JSON. POST: a JSON body updates a task — same shape as
    the `update_task` tool: {"task_id": 3, "status": "done", "note": "...", "hours_spent": 1.5}."""
    try:
        with connect() as conn:
            pid = _resolve_project(conn, request)
            if request.method == "GET":
                return JSONResponse(tasks.next_task(conn, pid) or {"message": "No open tasks."})
            body = await request.json()
            task_id = body.pop("task_id", None)
            if not task_id:
                return JSONResponse({"error": "task_id is required in the POST body"}, status_code=400)
            body.setdefault("actor", "chatgpt")
            result = tasks.update_task(conn, task_id, **body)
            return JSONResponse({"message": "updated", "task": result})
    except ValueError as exc:
        return JSONResponse({"error": str(exc)}, status_code=400)


async def plain_context(request: Request) -> PlainTextResponse:
    """GET: the full handoff briefing as plain text, readable by a tool that can only browse a URL."""
    try:
        with connect() as conn:
            return PlainTextResponse(handoff.build(conn, _resolve_project(conn, request)))
    except ValueError as exc:
        return PlainTextResponse(str(exc), status_code=400)


class RequireToken:
    """Reject any request whose token doesn't match, before it reaches an MCP tool.

    Accepts the token as `?token=...` (easiest to paste into a client's "server URL" field) or as
    `Authorization: Bearer ...` (what a client that supports custom headers will send).
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            return await self.app(scope, receive, send)
        req = Request(scope, receive)
        auth = req.headers.get("authorization", "")
        given = auth.removeprefix("Bearer ").strip() or req.query_params.get("token", "")
        if given != TOKEN:
            res = JSONResponse({"error": "missing or wrong token"}, status_code=401)
            return await res(scope, receive, send)
        await self.app(scope, receive, send)


# stateless_http: each request stands alone, so a host that restarts your process between
# requests (common on free tiers) doesn't break an in-progress session.
mcp.settings.stateless_http = True
# The plain routes are matched first; anything else (i.e. /mcp) falls through to the MCP app.
# Mounting mcp's app doesn't carry its lifespan along automatically — without this, the MCP
# session manager never starts and every /mcp request fails with "Task group is not initialized".
# Reusing its lifespan directly on the combined app is what actually starts it.
_mcp_app = mcp.streamable_http_app()
_combined = Starlette(
    routes=[
        Route("/task", plain_task, methods=["GET", "POST"]),
        Route("/context", plain_context, methods=["GET"]),
        Mount("/", app=_mcp_app),
    ],
    lifespan=lambda app: mcp.session_manager.run(),
)
app = RequireToken(_combined)


if __name__ == "__main__":
    # Local testing only. In production, the host (e.g. Render) runs uvicorn for you.
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8080)))
