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

from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from mcp.server.transport_security import TransportSecuritySettings

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
app = RequireToken(mcp.streamable_http_app())


if __name__ == "__main__":
    # Local testing only. In production, the host (e.g. Render) runs uvicorn for you.
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=int(os.environ.get("PORT", 8080)))
