"""JSON file persistence.

`JsonDocument` stores one pydantic model in one JSON file. Writes are atomic
(write to a temp file, then rename) and serialised with a lock, so the web
server and a CLI/agent can safely share the same data folder.

To move to a database later, replace this module: services only use
`load()` and `save()`.
"""
from __future__ import annotations

import json
import os
import shutil
import tempfile
import threading
from pathlib import Path
from typing import Generic, TypeVar

from pydantic import BaseModel

from . import config

M = TypeVar("M", bound=BaseModel)


class JsonDocument(Generic[M]):
    def __init__(self, path: Path, model: type[M], seed_name: str | None = None):
        self.path = path
        self.model = model
        self.seed_name = seed_name
        self._lock = threading.RLock()

    def _ensure_exists(self) -> None:
        if self.path.exists():
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        seed = config.SEED_DIR / self.seed_name if self.seed_name else None
        if seed and seed.exists():
            shutil.copyfile(seed, self.path)
        else:
            self.save(self.model())

    def load(self) -> M:
        with self._lock:
            self._ensure_exists()
            with self.path.open(encoding="utf-8") as f:
                return self.model.model_validate(json.load(f))

    def save(self, doc: M) -> None:
        with self._lock:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, tmp = tempfile.mkstemp(dir=self.path.parent, suffix=".tmp")
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(doc.model_dump(mode="json"), f, indent=2, ensure_ascii=False)
            os.replace(tmp, self.path)

    def transaction(self):
        """`with store.transaction() as doc:` loads, yields, then saves."""
        return _Transaction(self)


class _Transaction(Generic[M]):
    def __init__(self, store: JsonDocument[M]):
        self.store = store

    def __enter__(self) -> M:
        self.store._lock.acquire()
        self.doc = self.store.load()
        return self.doc

    def __exit__(self, exc_type, exc, tb):
        try:
            if exc_type is None:
                self.store.save(self.doc)
        finally:
            self.store._lock.release()
        return False
