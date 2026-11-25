"""The monitor: a separate web app over the chat telemetry (reads it; writes only flags and eval cases).

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
from pydantic import BaseModel

from jobtracker import __version__, auth, config, users
from jobtracker.evals import fixture
from jobtracker.telemetry import flags, review

from . import queries

WEB = Path(__file__).parent / "web"
app = FastAPI(title="Ezri monitor", version=__version__)
_db: dict = {"path": None, "conn": None}
_rw: dict = {"path": None, "conn": None}
_lock = threading.Lock()
_write_lock = threading.Lock()
# What installing the monitor as an app needs: browsers fetch the manifest without cookies (nothing private here)
PUBLIC = ("/health", "/manifest.webmanifest", "/static/icons/", "/static/logo-64.png")


def _connection(slot: dict, readonly: bool):
    path = str(config.TELEMETRY_DB)
    if slot["path"] != path:
        from jobtracker.telemetry import store
        if slot["conn"] is not None:
            slot["conn"].close()
        if readonly:
            store.connect(config.TELEMETRY_DB).close()  # creates the tables an older database doesn't have yet
        slot["conn"], slot["path"] = store.connect(config.TELEMETRY_DB, readonly=readonly), path
    return slot["conn"]


def query(fn, *args):
    """Run a query on the shared read-only connection (queries are short)."""
    with _lock:
        return fn(_connection(_db, True), *args)


def write(fn, *args, **kwargs):
    """Flags and eval cases only (the app writes everything else)."""
    with _write_lock:
        return fn(_connection(_rw, False), *args, **kwargs)


def _denied(request: Request, message: str, status: int = 403):
    if request.url.path.startswith("/api/"):
        return JSONResponse({"detail": message}, status_code=status)
    return HTMLResponse(f"<!doctype html><meta charset=utf-8><title>Monitor · no access</title>"
                        f"<body style='font:16px system-ui;max-width:32em;margin:15vh auto;padding:0 16px'>"
                        f"<h1>No access</h1><p>{html.escape(message)}</p>", status_code=status)


@app.middleware("http")
async def owner_only(request: Request, call_next):
    path = request.url.path
    if any(path == p or (p.endswith("/") and path.startswith(p)) for p in PUBLIC):
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


@app.get("/api/mail")
def mail(days: int = 30, user: str | None = None):
    """The email check: what it looked at, found, how people judged it, and what it missed."""
    return query(queries.mail, max(1, min(days, 365)), _user(user))


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


# ------------------------------------------------------------------ flags and the review queue

class NewFlag(BaseModel):
    turn_id: str
    category: str
    note: str = ""
    span_id: str = ""
    llm_call_id: str = ""


class FlagChange(BaseModel):
    status: str | None = None
    note: str | None = None
    category: str | None = None
    case_id: str | None = None
    fixed_in: str | None = None


@app.get("/api/flags/catalog")
def flag_catalog():
    return flags.catalog()


@app.get("/api/flags")
def flag_list(status: str = "suggested,open,in_case"):
    statuses = [s for s in status.split(",") if s] if status != "*" else None
    return query(queries.flag_list, statuses)


@app.post("/api/flags")
def flag_add(body: NewFlag):
    if not query(queries.turn, body.turn_id):
        raise HTTPException(404, "No telemetry for this turn")
    try:
        return write(review.add, body.turn_id, body.category, body.note.strip(), body.span_id, body.llm_call_id)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e


@app.patch("/api/flags/{flag_id}")
def flag_change(flag_id: int, body: FlagChange):
    try:
        out = write(review.update, flag_id, **body.model_dump())
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    if out is None:
        raise HTTPException(404, "No such flag")
    return out


class CaseChange(BaseModel):
    status: str


@app.get("/api/cases")
def case_list(status: str = "*"):
    return query(review.list_cases, None if status == "*" else [s for s in status.split(",") if s])


@app.get("/api/cases/{case_id}")
def case_get(case_id: str):
    out = query(review.get_case, case_id)
    if out is None:
        raise HTTPException(404, "No such case")
    return out


@app.post("/api/cases")
def case_save(case: dict):
    try:
        return write(review.save_case, case)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e


@app.patch("/api/cases/{case_id}")
def case_status(case_id: str, body: CaseChange):
    try:
        out = write(review.set_case_status, case_id, body.status)
    except ValueError as e:
        raise HTTPException(422, str(e)) from e
    except PermissionError as e:
        raise HTTPException(409, str(e)) from e
    if out is None:
        raise HTTPException(404, "No such case")
    return out


@app.get("/api/review/data")
def review_data(user: str = ""):
    """A person's tracker files (applications, CV, profile…), the starting point of an eval case."""
    return fixture.export(user)


@app.get("/api/review/queue")
def review_queue(full: bool = True):
    """What still needs fixing, with each flagged turn's whole trace (for the fixing process)."""
    return query(queries.review_queue, full)


@app.get("/")
def index():
    return FileResponse(WEB / "index.html")


@app.get("/manifest.webmanifest")
def manifest():
    return FileResponse(WEB / "manifest.webmanifest", media_type="application/manifest+json")


@app.get("/sw.js")
def service_worker():
    """At the root, so it controls the whole monitor (a worker's scope is its own folder)."""
    return FileResponse(WEB / "sw.js", media_type="text/javascript")


app.mount("/static", StaticFiles(directory=WEB), name="static")
