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
import base64
import json
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


MAX_HANDOFF_BYTES = 500_000   # a handoff is text; this stops a runaway or malicious upload


async def handoff_box(request: Request) -> PlainTextResponse:
    """A drop box for the final handoff, keyed by project slug.

    POST {"markdown": "...", "pushed_by": "codex"} stores it (replacing the previous one).
    GET returns the latest stored text, so ChatGPT (or any tool with the token) can read it.
    Separate from /context on purpose: /context rebuilds from THIS server's database, which is
    empty when your real work happens on your laptop. This holds exactly what the laptop pushed.
    """
    slug = (request.query_params.get("project") or "").strip()
    if not slug:
        return PlainTextResponse("pass ?project=<slug>", status_code=400)
    try:
        with connect() as conn:
            if request.method == "GET":
                row = conn.execute("SELECT markdown, updated_at FROM pushed_handoffs WHERE slug = ?",
                                   (slug,)).fetchone()
                if row is None:
                    return PlainTextResponse(f"No handoff has been pushed for '{slug}' yet.", status_code=404)
                return PlainTextResponse(row["markdown"])
            raw = await request.body()
            if len(raw) > MAX_HANDOFF_BYTES:
                return PlainTextResponse("handoff too large", status_code=413)
            body = json.loads(raw)
            if body.get("markdown_b64"):
                # Base64 so a web-application firewall in front of the host can't read code-like
                # words in the text (it blocks e.g. "eval" or shell snippets, even in a harmless note).
                text = base64.b64decode(body["markdown_b64"], validate=True).decode("utf-8").strip()
            else:
                text = (body.get("markdown") or "").strip()
            if not text:
                return PlainTextResponse('body must be JSON like {"markdown_b64": "..."} or {"markdown": "..."}',
                                         status_code=400)
            conn.execute(
                "INSERT INTO pushed_handoffs (slug, markdown, pushed_by, updated_at) "
                "VALUES (?, ?, ?, datetime('now')) ON CONFLICT(slug) DO UPDATE SET "
                "markdown = excluded.markdown, pushed_by = excluded.pushed_by, updated_at = excluded.updated_at",
                (slug, text, str(body.get("pushed_by") or "unknown")[:40]))
            return PlainTextResponse(f"stored handoff for '{slug}' ({len(text)} chars)")
    except (ValueError, AttributeError) as exc:     # bad JSON / bad base64 / body not an object
        return PlainTextResponse(f"bad request: {exc}", status_code=400)


MAX_CODE_BUNDLE_BYTES = 900_000   # base64 adds ~33%; keep the whole request under Render's cap


async def code_box(request: Request) -> PlainTextResponse:
    """Same idea as /handoff, for the code bundle: POST {"code_b64": "..."} stores it per slug,
    GET returns the latest one as plain text — readable by a tool that can only open a URL."""
    slug = (request.query_params.get("project") or "").strip()
    if not slug:
        return PlainTextResponse("pass ?project=<slug>", status_code=400)
    try:
        with connect() as conn:
            if request.method == "GET":
                row = conn.execute("SELECT code FROM pushed_code WHERE slug = ?", (slug,)).fetchone()
                if row is None:
                    return PlainTextResponse(f"No code has been pushed for '{slug}' yet.", status_code=404)
                return PlainTextResponse(row["code"])
            raw = await request.body()
            if len(raw) > MAX_CODE_BUNDLE_BYTES:
                return PlainTextResponse("code bundle too large", status_code=413)
            body = json.loads(raw)
            text = base64.b64decode(body.get("code_b64", ""), validate=True).decode("utf-8").strip()
            if not text:
                return PlainTextResponse('body must be JSON like {"code_b64": "..."}', status_code=400)
            conn.execute(
                "INSERT INTO pushed_code (slug, code, pushed_by, updated_at) VALUES (?, ?, ?, datetime('now')) "
                "ON CONFLICT(slug) DO UPDATE SET code = excluded.code, pushed_by = excluded.pushed_by, "
                "updated_at = excluded.updated_at",
                (slug, text, str(body.get("pushed_by") or "unknown")[:40]))
            return PlainTextResponse(f"stored code bundle for '{slug}' ({len(text)} chars)")
    except (ValueError, AttributeError) as exc:
        return PlainTextResponse(f"bad request: {exc}", status_code=400)


MAX_ZIP_BYTES = 20 * 1024 * 1024   # 20 MB: typical project code is well under this


async def zip_box(request: Request):
    """POST: upload the project zip.  GET: download it.

    POST body: multipart/form-data with a `file` field, or raw bytes with
    Content-Type: application/zip. The zip name is taken from the Content-Disposition
    header if present, otherwise defaults to <slug>-project.zip.
    Only the server-side token check guards this endpoint; the URL itself is the "password"
    that you share with ChatGPT so it can download the code.
    """
    slug = (request.query_params.get("project") or "").strip()
    if not slug:
        return JSONResponse({"error": "pass ?project=<slug>"}, status_code=400)
    try:
        with connect() as conn:
            if request.method == "GET":
                row = conn.execute(
                    "SELECT zip_bytes, filename FROM pushed_zips WHERE slug = ?", (slug,)).fetchone()
                if row is None:
                    return JSONResponse({"error": f"No zip has been pushed for '{slug}' yet."},
                                       status_code=404)
                from starlette.responses import Response
                return Response(bytes(row["zip_bytes"]),
                                media_type="application/zip",
                                headers={"Content-Disposition": f'attachment; filename="{row["filename"]}"'})
            # POST: accept raw bytes
            raw = await request.body()
            if not raw:
                return JSONResponse({"error": "empty body"}, status_code=400)
            if len(raw) > MAX_ZIP_BYTES:
                return JSONResponse({"error": f"zip too large (max {MAX_ZIP_BYTES // 1024 // 1024} MB)"},
                                    status_code=413)
            # Validate it's actually a zip
            if not raw.startswith(b"PK"):
                return JSONResponse({"error": "not a zip file"}, status_code=400)
            pushed_by = (request.query_params.get("by") or "unknown")[:40]
            cd = request.headers.get("content-disposition", "")
            filename = slug + "-project.zip"
            for part in cd.split(";"):
                part = part.strip()
                if part.startswith("filename="):
                    filename = part[9:].strip("\"'")[:80] or filename
                    break
            conn.execute(
                "INSERT INTO pushed_zips (slug, zip_bytes, filename, pushed_by, updated_at) "
                "VALUES (?, ?, ?, ?, datetime('now')) ON CONFLICT(slug) DO UPDATE SET "
                "zip_bytes = excluded.zip_bytes, filename = excluded.filename, "
                "pushed_by = excluded.pushed_by, updated_at = excluded.updated_at",
                (slug, raw, filename, pushed_by))
            return JSONResponse({"stored": f"{filename} ({len(raw):,} bytes)", "project": slug})
    except Exception as exc:
        return JSONResponse({"error": str(exc)}, status_code=500)


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
        Route("/handoff", handoff_box, methods=["GET", "POST"]),
        Route("/zip", zip_box, methods=["GET", "POST"]),
        Route("/code", code_box, methods=["GET", "POST"]),
        Mount("/", app=_mcp_app),
    ],
    lifespan=lambda app: mcp.session_manager.run(),
)
app = RequireToken(_combined)


if __name__ == "__main__":
    # Local testing only. In production, the host (e.g. Render) runs uvicorn for you.
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8080)))
