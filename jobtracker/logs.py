"""Logging: readable text locally, one JSON object per line in the cloud (LOG_FORMAT=json)."""
from __future__ import annotations

import json
import logging
import time

from fastapi import Request

from . import config

log = logging.getLogger("jobtracker")


class _Json(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        entry = {"severity": record.levelname, "message": record.getMessage(), "logger": record.name,
                 "time": self.formatTime(record, "%Y-%m-%dT%H:%M:%S")}
        entry.update(getattr(record, "fields", {}))
        if record.exc_info:
            entry["exception"] = self.formatException(record.exc_info)
        return json.dumps(entry, ensure_ascii=False)


def setup() -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(_Json() if config.LOG_FORMAT == "json"
                         else logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger("jobtracker")
    root.handlers[:] = [handler]
    root.setLevel(config.LOG_LEVEL.upper())
    root.propagate = False


async def access_log(request: Request, call_next):
    """One line per request: method, path, status, duration (static UI files are skipped)."""
    start = time.perf_counter()
    response = await call_next(request)
    path = request.url.path
    if path.startswith(("/api/", "/files/", "/login", "/health")) or path == "/":
        ms = round((time.perf_counter() - start) * 1000)
        log.info("%s %s %s %dms", request.method, path, response.status_code, ms,
                 extra={"fields": {"method": request.method, "path": path,
                                   "status": response.status_code, "duration_ms": ms}})
    return response
