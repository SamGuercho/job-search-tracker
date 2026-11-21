"""HTTP layer: a thin wrapper over the action registry + static files."""
from __future__ import annotations

from pathlib import Path

from fastapi import Body, FastAPI, File, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError

from . import (
    __version__,
    actions,  # noqa: F401  (registers the actions)
    auth,
    config,
    logs,
    registry,
    users,
)
from .services import llm, pdf
from .services.companies import NotFound
from .services.cv_import import SUPPORTED
from .services.llm import LLMUnavailable

logs.setup()
app = FastAPI(title="Job Search Tracker", version=__version__)
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
    """For container / cloud health checks. Public, reveals no data."""
    return {"status": "ok", "version": __version__, "pdf_engine": pdf.available_engine(),
            "ai": llm.available(), "login": auth.enabled()}


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
    except LLMUnavailable as e:
        raise HTTPException(503, str(e)) from e
    except ValidationError as e:
        raise HTTPException(422, e.errors(include_url=False, include_context=False)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e


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
    return FileResponse(target, filename=target.name)


@app.get("/")
def index():
    return FileResponse(config.WEB_DIR / "index.html")


app.mount("/", StaticFiles(directory=config.WEB_DIR), name="web")
