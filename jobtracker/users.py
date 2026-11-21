"""Who is using the app, and where their data lives.

Behind `tailscale serve` (JOBTRACKER_TAILSCALE_USERS=1, set by deploy/home-server/compose.yaml), Tailscale
decides who gets in: your own devices and the people you shared the machine with. `tailscale serve` adds a
`Tailscale-User-Login` header to every request (also for shared users) and strips any copy sent by the
client; the app listens on localhost only, so the header can't be forged. There is no list to maintain:

- every Tailscale login gets its own space, created on first visit: USERS_DIR/<login>/data and /companies
- the owner keeps DATA_DIR / OUTPUT_DIR (the data from before several users). The owner is the login equal
  to the email in that master CV, recorded in DATA_DIR/owner.txt on first visit so that changing the CV
  email later changes nothing. JOBTRACKER_OWNER overrides it (Tailscale login different from the CV email).
- HTTP requests without the header are refused (the password is not used: anyone knowing it would be the
  owner). The CLI and the stdio MCP server don't go through HTTP and act as the owner; the HTTP MCP endpoint (/mcp)
  uses the request's login like the rest of the app.

Every data and file path in the app goes through data_dir() / output_dir(), which read the current user
from a context variable set once per request: isolation is structural, not a check in each action.
"""
from __future__ import annotations

import json
import re
import threading
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import config

HEADER = "tailscale-user-login"
_current: ContextVar[str] = ContextVar("jobtracker_user", default="")  # "" = the owner
_lock = threading.Lock()


def enabled() -> bool:
    return config.TAILSCALE_USERS


def current() -> str:
    """Login of the current user ("" = the owner)."""
    return _current.get()


def _owner_file() -> Path:
    return config.DATA_DIR / "owner.txt"


def owner() -> str:
    """The owner's Tailscale login, or "" while unknown."""
    if config.OWNER:
        return config.OWNER
    path = _owner_file()
    return path.read_text(encoding="utf-8").strip().lower() if path.exists() else ""


def _cv_email() -> str:
    try:
        cv = json.loads((config.DATA_DIR / "master_cv.json").read_text(encoding="utf-8"))
        return str(cv.get("profile", {}).get("email", "")).strip().lower()
    except (OSError, ValueError, AttributeError):
        return ""


def resolve(login: str) -> str:
    """Map a Tailscale login to a user: "" for the owner, the login for anyone else."""
    login = login.strip().lower()
    with _lock:
        known = owner()
        if not known and login and login == _cv_email():  # first visit of the owner: remember them
            _owner_file().parent.mkdir(parents=True, exist_ok=True)
            _owner_file().write_text(login + "\n", encoding="utf-8")
            known = login
    return "" if login == known else login


@contextmanager
def acting_as(user: str):
    token = _current.set(user)
    try:
        yield
    finally:
        _current.reset(token)


def _folder(user: str) -> Path:
    name = re.sub(r"[^a-z0-9@._-]+", "_", user.lower()).strip("._") or "_"
    return config.USERS_DIR / name


def data_dir() -> Path:
    user = current()
    return _folder(user) / "data" if user else config.DATA_DIR


def output_dir() -> Path:
    user = current()
    return _folder(user) / "companies" if user else config.OUTPUT_DIR


def uploads_dir() -> Path:
    return data_dir() / "uploads"
