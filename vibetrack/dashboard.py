"""Read-only dashboard: a small JSON API plus one static page. Starlette and uvicorn come with `mcp`.

Run:  python -m vibetrack.dashboard [--port 8765]     then open http://127.0.0.1:8765

Safety: binds to localhost only, answers GET/HEAD only (it cannot change your data), and rejects
foreign Host headers (blocks DNS-rebinding, so other websites cannot read it).
"""
import argparse
from pathlib import Path
from typing import Any, Callable

import uvicorn
from starlette.applications import Starlette
from starlette.middleware import Middleware
from starlette.middleware.trustedhost import TrustedHostMiddleware
from starlette.requests import Request
from starlette.responses import FileResponse, JSONResponse
from starlette.routing import Route

from . import decisions, handoff, projects, stats, tasks
from .db import connect, project_id

WEB = Path(__file__).with_name("web")
# Our own scripts and styles only. The page never uses innerHTML (task text comes from an AI).
CSP = ("default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; "
       "base-uri 'none'; form-action 'none'")


def _static(name: str, media_type: str, path: str) -> Route:
    def endpoint(request: Request) -> FileResponse:
        return FileResponse(WEB / name, media_type=media_type,
                            headers={"Content-Security-Policy": CSP, "Cache-Control": "no-cache"})
    return Route(path, endpoint)


def _project_api(build: Callable[[Any, int], Any]) -> Callable[[Request], JSONResponse]:
    """Wrap a (conn, project_id) -> data function as a JSON endpoint; unknown slug -> 404."""
    def endpoint(request: Request) -> JSONResponse:
        try:
            with connect() as conn:
                return JSONResponse(build(conn, project_id(conn, request.path_params["slug"])))
        except ValueError as exc:
            return JSONResponse({"error": str(exc)}, status_code=404)
    return endpoint


def _project_list(request: Request) -> JSONResponse:
    with connect() as conn:
        return JSONResponse(projects.list_all(conn))


def _route(name: str, build: Callable[[Any, int], Any]) -> Route:
    return Route(f"/api/projects/{{slug}}/{name}", _project_api(build))


app = Starlette(
    routes=[
        _static("index.html", "text/html", "/"),
        _static("app.js", "text/javascript", "/app.js"),
        _static("style.css", "text/css", "/style.css"),
        Route("/api/projects", _project_list),
        _route("overview", projects.overview),
        _route("charts", stats.charts),
        _route("tasks", tasks.tree),
        _route("decisions", decisions.tree),
        _route("handoff", lambda conn, pid: {"markdown": handoff.build(conn, pid)}),
    ],
    middleware=[Middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])],
)


def main() -> None:
    ap = argparse.ArgumentParser(description="VibeTrack dashboard (read-only, localhost)")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    print(f"VibeTrack dashboard: http://127.0.0.1:{args.port}  (Ctrl+C to stop)")
    uvicorn.run(app, host="127.0.0.1", port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
