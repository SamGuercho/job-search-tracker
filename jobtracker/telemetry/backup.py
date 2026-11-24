"""Telemetry backups: a compressed copy of the database every week (by default), only the last few kept.

Off unless JOBTRACKER_TELEMETRY_BACKUP_DIR is set. On the home server it is `data/telemetry-backups`, which the
nightly backup.ps1 mirrors to OneDrive (so the cloud holds the same few files, never more).

How: a consistent copy of the live database (VACUUM INTO, from its own read-only connection, so the app keeps
writing meanwhile), compressed with xz (telemetry is mostly text: typically 10x smaller), written under a temporary
name and renamed when complete. It runs in its own low-priority thread: the chat never waits for it.
Restore: decompress (`xz -d`, 7-Zip) and put the file back as telemetry.db (see the home-server README).
"""
from __future__ import annotations

import lzma
import os
import shutil
import sqlite3
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .. import config
from ..logs import log

PREFIX, SUFFIX = "telemetry-", ".db.xz"
CHECK_EVERY = 3600  # seconds between "is a backup due?" checks (one stat of a small folder)

_lock = threading.Lock()
_thread: threading.Thread | None = None
_next_check = 0.0


def backups(folder: Path) -> list[Path]:
    """The backups in `folder`, oldest first (their names start with the date)."""
    return sorted(folder.glob(f"{PREFIX}*{SUFFIX}")) if folder.is_dir() else []


def due(folder: Path, now: float | None = None) -> bool:
    existing = backups(folder)
    if not existing:
        return True
    return (now or time.time()) - existing[-1].stat().st_mtime >= config.TELEMETRY_BACKUP_EVERY_DAYS * 86400


def make(db: Path | None = None, folder: Path | None = None) -> Path:
    """Write one compressed backup now, then keep only the newest TELEMETRY_BACKUP_KEEP. Returns its path."""
    db = Path(db or config.TELEMETRY_DB)
    folder = Path(folder or config.TELEMETRY_BACKUP_DIR)
    folder.mkdir(parents=True, exist_ok=True)
    target = folder / f"{PREFIX}{datetime.now().strftime('%Y-%m-%d_%H%M%S')}{SUFFIX}"
    copy = db.with_name(db.name + ".backup-tmp")  # next to the live file (fast local disk), not in the synced folder
    partial = target.with_name(target.name + ".tmp")
    try:
        copy.unlink(missing_ok=True)
        conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
        try:
            conn.execute("VACUUM INTO ?", (str(copy),))
        finally:
            conn.close()
        with copy.open("rb") as src, lzma.open(partial, "wb", preset=6) as dst:
            shutil.copyfileobj(src, dst, 1024 * 1024)
        os.replace(partial, target)
    finally:
        copy.unlink(missing_ok=True)
        partial.unlink(missing_ok=True)
    prune(folder)
    log.info("Telemetry: backup %s (%.1f MB, database %.1f MB)", target.name, target.stat().st_size / 1e6,
             db.stat().st_size / 1e6)
    return target


def prune(folder: Path, keep: int | None = None) -> None:
    keep = max(1, keep or config.TELEMETRY_BACKUP_KEEP)
    for old in backups(folder)[:-keep]:
        old.unlink(missing_ok=True)


def maybe_start() -> None:
    """Called by the telemetry writer after each batch: cheap; starts a backup in the background when one is due."""
    global _thread, _next_check
    folder = config.TELEMETRY_BACKUP_DIR
    if not folder or time.time() < _next_check:
        return
    _next_check = time.time() + CHECK_EVERY
    with _lock:
        if _thread and _thread.is_alive():
            return
        try:
            if not due(folder):
                return
        except OSError as e:
            log.warning("Telemetry: backup folder %s unreadable: %s", folder, e)
            return
        _thread = threading.Thread(target=_run, name="telemetry-backup", daemon=True)
        _thread.start()


def _run() -> None:
    if sys.platform.startswith("linux"):  # on Linux, nice lowers this thread only (elsewhere: the whole app)
        try:
            os.nice(10)
        except OSError:
            pass
    try:
        make()
    except Exception as e:  # never breaks the app: tried again at the next check
        log.warning("Telemetry: backup failed: %s", e)


def wait(timeout: float = 60) -> None:
    """For tests: until the running backup (if any) is done."""
    if _thread:
        _thread.join(timeout)
