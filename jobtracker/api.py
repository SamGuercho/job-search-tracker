"""HTTP layer: a thin wrapper over the action registry + static files."""
from __future__ import annotations

import asyncio
import contextvars
import json
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import quote

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, RedirectResponse, Response, StreamingResponse
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
from .services import assistant, calendar, linkedin, llm, logos, mail, pdf, recordings, reminders, usage
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
    mail.start()  # reads connected mailboxes for news about applications (JOBTRACKER_MAIL=0 turns it off)
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
    return {"status": "error" if problems else "ok", "version": __version__, **maintenance.version(),
            "pdf_engine": pdf.available_engine(),
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
    except (LLMUnavailable, PrepUnavailable, recordings.RecordingUnavailable, mail.MailError) as e:
        raise HTTPException(503, str(e)) from e
    except PermissionError as e:
        raise HTTPException(403, str(e)) from e
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


# ------------------------------------------------------------------ chat, live
# The same turn as the chat.send / chat.resolve actions, streamed to the browser as server-sent events while it runs:
# text and thinking deltas, tools starting and ending, then the whole chat (or an error) at the end.

def _for_ui(event: dict) -> dict | None:
    """What the chat page needs from a telemetry event (small: no tool results, no stored text)."""
    kind = event.get("type", "")
    if kind == "live.delta":
        return {"type": "delta", "kind": event["kind"], "text": event["text"]}
    if kind == "live.block_done":
        return {"type": "block_done", "kind": event["kind"]}
    if kind in ("tool.started", "tool.ended"):
        p = event.get("payload") or {}
        return {"type": kind, "span_id": event["span_id"], "status": event.get("status", ""),
                **({"action": p.get("action"), "args": p.get("args")} if kind == "tool.started" else {})}
    if kind == "llm_call.started":
        return {"type": "step"}
    return None


def _error_status(e: Exception) -> int:
    for types, status in ((NotFound, 404), ((LLMUnavailable,), 503), ((PermissionError,), 403), ((ValueError,), 400)):
        if isinstance(e, types):
            return status
    return 500


@app.post("/api/chat/stream")
async def chat_stream(payload: dict = Body(default={})):
    """{message, chat_id?} sends a message; {chat_id, event_id, approve} answers an approval."""
    resolving = bool(payload.get("event_id"))
    if not (payload.get("message") or "").strip() and not (resolving and payload.get("chat_id")):
        raise HTTPException(422, "Send a message, or chat_id + event_id + approve")
    loop = asyncio.get_running_loop()
    queue: asyncio.Queue = asyncio.Queue()

    def listener(event: dict) -> None:
        if (item := _for_ui(event)) is not None:
            loop.call_soon_threadsafe(queue.put_nowait, item)

    def turn():
        llm.reset_notices()
        with usage.feature("chat.resolve" if resolving else "chat.send"):  # same billing as the actions
            if resolving:
                chat = assistant.resolve(payload["chat_id"], payload["event_id"], bool(payload.get("approve")), listener)
            else:
                chat = assistant.send(payload["message"], payload.get("chat_id"), listener)
        return registry.to_jsonable(chat), llm.pop_notice()

    context = contextvars.copy_context()  # the request's user (set by the auth middleware) runs the turn

    async def work():
        try:
            chat, notice = await loop.run_in_executor(None, context.run, turn)
            queue.put_nowait({"type": "chat", "chat": chat, **({"notice": notice} if notice else {})})
        except Exception as e:  # the browser shows it like a failed action
            if _error_status(e) == 500:
                logs.log.exception("Chat stream failed")
            queue.put_nowait({"type": "error", "status": _error_status(e), "detail": str(e)})
        finally:
            queue.put_nowait(None)

    task = asyncio.create_task(work())

    async def events():
        while (item := await queue.get()) is not None:
            yield f"data: {json.dumps(item, default=str, ensure_ascii=False)}\n\n"
        await task

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


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


def _base_url(request: Request) -> str:
    """This app's address as the browser sees it (https behind `tailscale serve`)."""
    host = request.headers.get("x-forwarded-host") or request.headers.get("host") or request.url.netloc
    proto = request.headers.get("x-forwarded-proto") or request.url.scheme
    if host.split(":")[0].endswith(".ts.net"):  # tailscale serve: always https
        proto = "https"
    return f"{proto}://{host}/"


@app.get("/mail/connect")
def mail_connect(request: Request, provider: str = "gmail"):
    """Start the sign-in for read-only access: Google for Gmail, Microsoft for Outlook (Profile → Email → Connect)."""
    try:
        return RedirectResponse(mail.connect_url(_base_url(request), provider), status_code=303)
    except mail.MailError as e:
        return RedirectResponse(f"/#/profile?mail_error={quote(str(e))}", status_code=303)


@app.get("/mail/callback")
def mail_callback(request: Request, code: str = "", state: str = "", error: str = ""):
    """Google or Microsoft sends the user back here after the sign-in (the state says which)."""
    if error or not code:
        reason = ("You didn't allow access to your email." if error in ("access_denied", "consent_required")
                  else f"The sign-in said: {error or 'no code'}")
        return RedirectResponse(f"/#/profile?mail_error={quote(reason)}", status_code=303)
    try:
        mail.finish_connect(code, state, _base_url(request))
    except mail.MailError as e:
        return RedirectResponse(f"/#/profile?mail_error={quote(str(e))}", status_code=303)
    return RedirectResponse("/#/profile?mail=connected", status_code=303)


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


@app.post("/api/linkedin/import")
async def import_linkedin(file: UploadFile = File(...)):
    """Upload LinkedIn's data export (zip or Connections.csv): read in memory, only the needed fields are kept."""
    try:
        name = Path(file.filename or "export.zip").name
        return {"ok": True, "result": await run_in_threadpool(linkedin.import_export, name, file.file)}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


def _output_file(path: str) -> Path:
    """A file of the current user's output folder (404 for anything else)."""
    root = users.output_dir()
    target = (root / path).resolve()
    if not target.is_relative_to(root.resolve()) or not target.is_file():
        raise HTTPException(404, "File not found")
    return target


@app.get("/api/file-pages/{path:path}")
def file_pages(path: str):
    """How many pages a generated PDF has (the iPhone file viewer shows each as an image)."""
    from .services import pdf_pages
    target = _output_file(path)
    if target.suffix.lower() != ".pdf":
        raise HTTPException(400, "Not a PDF")
    try:
        return {"pages": pdf_pages.count(target)}
    except Exception as e:  # noqa: BLE001  (a damaged PDF)
        raise HTTPException(422, f"This PDF can't be read: {e}") from e


@app.get("/files/{path:path}")
def get_file(path: str, page: int = 0, width: int = 1200):
    """Download a generated document from the output folder. A PDF with `page` (from 1): that page as a PNG."""
    target = _output_file(path)
    if page and target.suffix.lower() == ".pdf":
        from .services import pdf_pages
        try:
            png = pdf_pages.render(target, page, width)
        except ValueError as e:
            raise HTTPException(404, str(e)) from e
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, max-age=300"})
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
