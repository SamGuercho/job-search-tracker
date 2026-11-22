"""Server checks: is the data readable, and when was it last backed up.

- check_data(): at startup, every user's data files are read with the current models. A new version that can't
  read existing data reports it on /health, so the update script on the home server puts the previous version
  back (deploy/home-server/update.ps1) before anything is written in the new format.
- backup_status(): deploy/home-server/backup.ps1 writes DATA_DIR/last_backup.json after each run; /health and
  the Today page (owner only, on the server) say when it is missing, failed or too old.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import config, users
from .logs import log
from .storage import REGISTERED

BACKUP_FILE = "last_backup.json"
BACKUP_MAX_AGE_HOURS = 72          # a daily backup that missed more than two runs
_state: dict = {"problems": None}  # filled by check_data() at startup


def data_folders() -> list[Path]:
    """The owner's data folder and every other user's."""
    folders = [config.DATA_DIR]
    if config.USERS_DIR.exists():
        folders += sorted(f / "data" for f in config.USERS_DIR.iterdir() if (f / "data").is_dir())
    return folders


def _rel(path: Path) -> str:
    try:
        return path.relative_to(config.DATA_DIR).as_posix()
    except ValueError:
        return str(path)


def check_data() -> list[str]:
    """Read every existing data file with its model; returns the problems (file and error), never raises."""
    problems = []
    for folder in data_folders():
        for name, model in REGISTERED.items():
            path = folder / name
            if not path.exists():
                continue
            try:
                model.model_validate(json.loads(path.read_text(encoding="utf-8")))
            except Exception as e:  # noqa: BLE001  (any failure means this version can't use the file)
                problems.append(f"{_rel(path)}: {type(e).__name__}: {(str(e).splitlines() or [''])[0][:200]}")
    for p in problems:
        log.error("Data check failed: %s", p)
    _state["problems"] = problems
    return problems


def data_problems() -> list[str] | None:
    """The problems found at startup (None: not checked yet)."""
    return _state["problems"]


def backup_status(now: datetime | None = None) -> dict | None:
    """{"at", "ok", "age_hours", "stale", "error"} from the last backup run, or None if none ran."""
    path = config.DATA_DIR / BACKUP_FILE
    try:
        raw = json.loads(path.read_text(encoding="utf-8-sig"))  # PowerShell may write a BOM
        at = datetime.fromisoformat(str(raw["at"])[:19])
    except (OSError, ValueError, KeyError, TypeError):
        return None
    age = ((now or datetime.now()) - at).total_seconds() / 3600
    ok = bool(raw.get("ok"))
    return {"at": at.isoformat(timespec="minutes"), "ok": ok, "age_hours": round(age, 1),
            "stale": age > BACKUP_MAX_AGE_HOURS, "error": "" if ok else str(raw.get("error", ""))[:300]}


def warnings() -> list[str]:
    """What the owner should fix on the server (shown on Today). Nothing for other users, or when not on a server."""
    if not users.enabled() or users.current():
        return []
    out = []
    if data_problems():
        out.append("Some data files can't be read by this version: see the server logs (docker compose logs).")
    b = backup_status()
    if b is None:
        out.append("No backup yet. On the server: powershell -ExecutionPolicy Bypass -File .\\backup.ps1 -Install")
    elif not b["ok"]:
        out.append(f"The last backup failed ({b['at'][:10]}): {b['error'] or 'see backup.log on the server'}")
    elif b["stale"]:
        days = int(b["age_hours"] // 24)
        out.append(f"Last backup: {b['at'][:10]}, {days} days ago. Is the server's backup task running?")
    return out
