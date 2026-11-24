"""The monitor: a separate web app over the chat telemetry (read only).

Run it next to the app: `python -m jobtracker monitor [--port 8100]`. Owner only: behind `tailscale serve`
(JOBTRACKER_TAILSCALE_USERS=1) the Tailscale login must be the owner's; with a password, the app's login cookie
(sign in on the app first; cookies are shared between ports of the same host); locally, open.
"""
from __future__ import annotations

import html
import threading
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from jobtracker import __version__, auth, config, users

from . import queries

WEB = Path(__file__).parent / "web"
app = FastAPI(title="Ezri monitor", version=__version__)
_db: dict = {"path": None, "conn": None}
_lock = threading.Lock()


def query(fn, *args):
    """Run a query on the shared read-only connection (the app is the only writer; queries are short)."""
    with _lock:
        path = str(config.TELEMETRY_DB)
        if _db["path"] != path:
            from jobtracker.telemetry import store
            if _db["conn"] is not None:
                _db["conn"].close()
            _db["conn"], _db["path"] = store.connect(config.TELEMETRY_DB, readonly=True), path
        return fn(_db["conn"], *args)


def _denied(request: Request, message: str, status: int = 403):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": message}, status_code=status)
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Monitor · no access</title>"
                        f"<body style='font:16px system-ui;max-width:32em;margin:15vh auto;padding:0 16px'>"
                        f"<h1>No access</h1><p>{html.escape(message)}</p>", status_code=status)


@app.middleware("http")
async def owner_only(request: Request, call_next):
    if request.url.path == "/health":
        return await call_next(request)
    if users.enabled():
        login = request.headers.get(users.HEADER, "").strip()
        if not login:
            return _denied(request, "Open the monitor through its Tailscale address.")
        if users.resolve(login) != "":
            return _denied(request, "The monitor is for the owner of this server only.")
    elif auth.enabled() and not auth.valid(request.cookies.get(auth.COOKIE)):
        return _denied(request, "Sign in on the app first (same address, the app's port), then reload.", 401)
    response = await call_next(request)
    if not request.url.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health")
def health():
    return {"status": "ok", "version": __version__}


def _user(user: str | None) -> str | None:
    return None if user in (None, "*") else user


@app.get("/api/overview")
def overview(days: int = 30, user: str | None = None):
    return query(queries.overview, max(1, min(days, 365)), _user(user))


@app.get("/api/conversations")
def conversations(days: int = 30, user: str | None = None):
    return query(queries.conversations, max(1, min(days, 365)), _user(user))


@app.get("/api/conversations/{conversation_id}")
def conversation(conversation_id: str):
    out = query(queries.conversation, conversation_id)
    if not out["turns"]:
        raise HTTPException(404, "No telemetry for this conversation")
    return out


@app.get("/api/turns/{turn_id}")
def turn(turn_id: str):
    out = query(queries.turn, turn_id)
    if not out:
        raise HTTPException(404, "No telemetry for this turn")
    return out


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


app.mount("/static", StaticFiles(directory=WEB), name="static")
