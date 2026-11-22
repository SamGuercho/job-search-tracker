"""HTTP layer: a thin wrapper over the action registry + static files."""
from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from mcp.server.streamable_http_manager import StreamableHTTPASGIApp
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool  # keeps the request's user (context variables)
from starlette.routing import Route

from . import (
    __version__,
    actions,  # noqa: F401  (registers the actions)
    auth,
    config,
    logs,
    maintenance,
    mcp_server,
    registry,
    users,
)
from .services import calendar, llm, logos, pdf, recordings, reminders
from .services.companies import NotFound
from .services.cv_import import SUPPORTED
from .services.llm import LLMUnavailable
from .services.prep import PrepUnavailable

logs.setup()
_mcp = {"app": None}  # the /mcp handler; a session manager runs only once, so each app start gets a new one


@asynccontextmanager
async def lifespan(_app):
    manager = mcp_server.http_manager()
    maintenance.check_data()  # a version that can't read the data says so on /health (the update script rolls back)
    reminders.start()  # interview / follow-up notifications (JOBTRACKER_REMINDERS=0 turns them off)
    async with manager.run():
        _mcp["app"] = StreamableHTTPASGIApp(manager)
        yield
        _mcp["app"] = None


class _McpEndpoint:
    """Raw ASGI app (a class, so Starlette doesn't wrap it as a request/response handler)."""

    async def __call__(self, scope, receive, send):
        if _mcp["app"] is None:
            raise RuntimeError("MCP endpoint used before the app started")
        await _mcp["app"](scope, receive, send)


app = FastAPI(title="Ezri (job search tracker)", version=__version__, lifespan=lifespan)
app.middleware("http")(auth.middleware)   # registered first = runs innermost, after logging
app.middleware("http")(logs.access_log)


@app.middleware("http")
async def no_stale_ui(request, call_next):
    """Browsers revalidate the UI files, so an app update is visible without a hard refresh."""
    response = await call_next(request)
    if not request.url.path.startswith(("/api/", "/files/")):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.get("/health")
def health():
    """For container / cloud health checks. Public, reveals no data (only how many files fail and the backup date).
    "status" is "error" when this version can't read existing data: the home server's update script then goes
    back to the previous version."""
    problems = maintenance.data_problems() or []
    backup = maintenance.backup_status()
    return {"status": "error" if problems else "ok", "version": __version__, "pdf_engine": pdf.available_engine(),
            "ai": llm.available(), "login": auth.enabled(), "data_problems": len(problems),
            "backup": {k: backup[k] for k in ("at", "ok", "age_hours", "stale")} if backup else None}


@app.get("/login")
def login_form():
    return auth.login_page()


@app.post("/login")
async def login(request: Request):
    return await auth.login(request)


@app.get("/logout")
def logout():
    return auth.logout()


@app.get("/api/actions")
def list_actions():
    """Machine-readable list of every action with its JSON schema (for agents / MCP)."""
    return registry.manifest()


@app.post("/api/actions/{name}")
def run_action(name: str, payload: dict = Body(default={})):
    if name not in registry.ACTIONS:
        raise HTTPException(404, f"Unknown action {name!r}")
    llm.reset_notices()
    try:
        result = registry.run(name, payload)
        notice = llm.pop_notice()  # e.g. "switched to Claude": the browser shows it as a toast
        return {"ok": True, "result": result, **({"notice": notice} if notice else {})}
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except (LLMUnavailable, PrepUnavailable, recordings.RecordingUnavailable) as e:
        raise HTTPException(503, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.get("/api/calendar/step.ics")
def calendar_step(request: Request, company: str, step: str):
    """One interview step as a calendar event: phones open it in their calendar app."""
    try:
        name, text = calendar.step_ics(company, step, str(request.base_url))
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    safe = "".join(ch if ch.isalnum() or ch in " -_." else "_" for ch in name)
    return Response(text, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": f'inline; filename="{safe}"'})


@app.get("/api/calendar/interviews.ics")
def calendar_feed(request: Request):
    """All upcoming interviews, as a calendar to subscribe to (updates by itself)."""
    return Response(calendar.feed_ics(str(request.base_url)), media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="Job interviews.ics"'})


@app.get("/share")
def share_target(title: str = "", text: str = "", url: str = ""):
    """Android share sheet -> "Ezri". A job's text (you selected it on the page) opens the "Add this job?"
    confirmation; a link alone pre-fills the assistant (it never sends anything by itself)."""
    import base64
    import json
    import re
    from urllib.parse import quote

    found = url or next(iter(re.findall(r"https?://\S+", f"{text} {title}")), "")
    words = re.sub(r"https?://\S+", "", text).strip()
    if len(words) > 200:  # the job itself was shared
        data = json.dumps({"u": found, "t": title, "x": words[:15_000]}, ensure_ascii=False).encode()
        return RedirectResponse("/#/clip?d=" + base64.urlsafe_b64encode(data).decode().rstrip("="), status_code=303)
    content = found or text or title
    message = f"Add this job and make my CV: {content}" if content else ""
    return RedirectResponse(f"/#/chat?q={quote(message)}" if message else "/#/", status_code=303)


@app.post("/api/companies/logo")
async def upload_logo(company: str, file: UploadFile = File(...)):
    """Use your own image as a company's logo (PNG, JPEG, WebP, ICO or SVG)."""
    try:
        return {"ok": True, "result": logos.upload(company, Path(file.filename or "logo").name, await file.read())}
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


@app.post("/api/recordings/upload")
async def upload_recording(company: str, step: str = "", file: UploadFile = File(...)):
    """Upload an interview recording for an application: saved in its folder, measured, with cost estimates.
    Nothing is spent until recording.transcribe."""
    try:  # the upload is already spooled to a temporary file: copied from there, never loaded whole
        rec = await run_in_threadpool(recordings.upload, company, Path(file.filename or "recording").name, file.file, step)
    except NotFound as e:
        raise HTTPException(404, str(e)) from e
    except recordings.RecordingUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"ok": True, "result": recordings.view(rec)}


@app.post("/api/cv/import")
async def import_cv_file(file: UploadFile = File(...)):
    """Upload a CV file; it is kept in data/uploads/ and parsed with cv.import.parse."""
    name = Path(file.filename or "cv").name
    if Path(name).suffix.lower() not in SUPPORTED:
        raise HTTPException(400, f"Unsupported file: use {', '.join(sorted(SUPPORTED))}")
    uploads = users.uploads_dir()
    uploads.mkdir(parents=True, exist_ok=True)
    target = uploads / name
    target.write_bytes(await file.read())
    return run_action("cv.import.parse", {"file_path": str(target)})


@app.get("/files/{path:path}")
def get_file(path: str):
    """Download a generated document from the output folder."""
    root = users.output_dir()
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(404, "File not found")
    if target.suffix.lower() in {".md", ".txt"}:  # readable in the browser instead of downloaded
        return FileResponse(target, media_type="text/plain; charset=utf-8", filename=target.name,
                            content_disposition_type="inline")
    if target.suffix.lower() == ".svg":  # a logo from the web: shown as an image, never allowed to run anything
        return FileResponse(target, media_type="image/svg+xml", headers={
            "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; img-src data:; sandbox",
            "X-Content-Type-Options": "nosniff"})
    return FileResponse(target, filename=target.name)


# MCP over HTTP for remote AI clients (Codex, Claude…): same tools as mcp_server.py, behind the same
# auth middleware; each call runs as the request's Tailscale user (see mcp_server._http_user).
app.router.routes.append(Route("/mcp", _McpEndpoint()))


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.WEB_DIR), name="web")
