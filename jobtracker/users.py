"""Who is using the app, and where their data lives.

Identity comes from Tailscale: `tailscale serve` adds a `Tailscale-User-Login` header to every request it
forwards (also for friends a machine is shared with) and strips any copy sent by the client. The app
listens on localhost only, so the header can't be forged. It is trusted only when JOBTRACKER_OWNER is set.

- the owner (JOBTRACKER_OWNER) uses DATA_DIR and OUTPUT_DIR, as with a single user
- a friend (JOBTRACKER_FRIENDS) uses USERS_DIR/<login>/data and USERS_DIR/<login>/companies
- anyone else on the tailnet is refused
- requests without the header (the server itself, CLI, MCP, tests) are the owner, behind the optional password

Every data and file path in the app goes through data_dir() / output_dir(), which read the current user
from a context variable set once per request: isolation is structural, not a check in each action.
"""
from __future__ import annotations

import re
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from . import config

HEADER = "tailscale-user-login"
_current: ContextVar[str] = ContextVar("jobtracker_user", default="")  # "" = the owner


class NotInvited(Exception):
    pass


def enabled() -> bool:
    return bool(config.OWNER)


def current() -> str:
    """Login of the current user ("" = the owner)."""
    return _current.get()


def resolve(login: str | None) -> str:
    """Map a Tailscale login to a user: "" for the owner, the login for a friend; NotInvited otherwise."""
    login = (login or "").strip().lower()
    if not enabled() or not login or login == config.OWNER:
        return ""
    if login in config.FRIENDS:
        return login
    raise NotInvited(login)


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
