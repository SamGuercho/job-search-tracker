"""Telemetry storage: SQLite (stdlib), one row per event, written live by a single background thread.

Only the app writes (this module's writer thread); the monitor app reads. Events are queued in memory and
inserted every few hundred milliseconds, so the chat never waits on the disk, and a crash mid-turn still leaves
everything up to the crash. When a turn ends, its summary row in `turns` is recomputed (fast dashboards).
It stays out of synced folders (see config.TELEMETRY_DB). Backups (compressed, weekly, the last few): see backup.py.
"""
from __future__ import annotations

import json
import queue
import sqlite3
import threading
import time
from pathlib import Path

from .. import config
from ..logs import log
from . import backup, detect, metrics, review
from .schema import Event

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL UNIQUE,
    ts TEXT NOT NULL,
    ts_ns INTEGER NOT NULL,
    schema_version INTEGER NOT NULL,
    app_version TEXT,
    user_id TEXT,
    conversation_id TEXT,
    turn_index INTEGER,
    turn_id TEXT,
    span_id TEXT,
    parent_span_id TEXT,
    llm_call_id TEXT,
    type TEXT NOT NULL,
    status TEXT,
    duration_ms REAL,
    payload TEXT NOT NULL DEFAULT '{}'
);
CREATE INDEX IF NOT EXISTS events_turn ON events (turn_id, seq);
CREATE INDEX IF NOT EXISTS events_conversation ON events (conversation_id, turn_index);
CREATE INDEX IF NOT EXISTS events_user_time ON events (user_id, ts_ns);
CREATE INDEX IF NOT EXISTS events_type_time ON events (type, ts_ns);

-- One row per finished turn, recomputed from its events when it ends.
CREATE TABLE IF NOT EXISTS turns (
    turn_id TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    turn_index INTEGER NOT NULL,
    user_id TEXT,
    trigger TEXT,
    title TEXT,
    status TEXT,
    provider TEXT,
    model TEXT,
    started_ns INTEGER,
    ended_ns INTEGER,
    wall_ms REAL,
    ttft_ms REAL,
    llm_calls INTEGER,
    llm_ms REAL,
    tool_calls INTEGER,
    tool_errors INTEGER,
    tool_wall_ms REAL,
    tool_sum_ms REAL,
    input_tokens INTEGER,
    cached_tokens INTEGER,
    output_tokens INTEGER,
    cost_usd REAL,
    consistent INTEGER
);
CREATE INDEX IF NOT EXISTS turns_time ON turns (started_ns);
CREATE INDEX IF NOT EXISTS turns_conversation ON turns (conversation_id, turn_index);

-- Evaluations of a turn (LLM as a judge, smaller models, rules…): empty until evaluators exist.
CREATE TABLE IF NOT EXISTS turn_scores (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    evaluator TEXT NOT NULL,
    metric TEXT NOT NULL,
    value REAL,
    label TEXT,
    details TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS turn_scores_turn ON turn_scores (turn_id);

-- What went wrong (see flags.py): a turn, or one of its spans; from the user, the owner or a detector.
CREATE TABLE IF NOT EXISTS flags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    turn_id TEXT NOT NULL,
    span_id TEXT NOT NULL DEFAULT '',
    llm_call_id TEXT NOT NULL DEFAULT '',
    conversation_id TEXT,
    user_id TEXT,
    category TEXT NOT NULL,
    note TEXT NOT NULL DEFAULT '',
    source TEXT NOT NULL,
    status TEXT NOT NULL,
    case_id TEXT,
    fixed_in TEXT,
    prompt_version TEXT,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    UNIQUE (turn_id, span_id, category, source)
);
CREATE INDEX IF NOT EXISTS flags_status ON flags (status, id);

-- Eval cases built from flags (frozen snapshot + checks + rubric): filled by the review process (phase 2).
CREATE TABLE IF NOT EXISTS cases (
    id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    flag_ids TEXT NOT NULL DEFAULT '[]',
    snapshot TEXT NOT NULL DEFAULT '{}',
    checks TEXT NOT NULL DEFAULT '[]',
    rubric TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""
# Columns added after a table was first created (old databases get them on the next start)
MIGRATIONS = {"turns": {"prompt_version": "TEXT"}}

COLUMNS = ("event_id", "ts", "ts_ns", "schema_version", "app_version", "user_id", "conversation_id", "turn_index",
           "turn_id", "span_id", "parent_span_id", "llm_call_id", "type", "status", "duration_ms", "payload")
TURN_COLUMNS = ("turn_id", "conversation_id", "turn_index", "user_id", "trigger", "title", "status", "provider", "model",
                "started_ns", "ended_ns", "wall_ms", "ttft_ms", "llm_calls", "llm_ms", "tool_calls", "tool_errors",
                "tool_wall_ms", "tool_sum_ms", "input_tokens", "cached_tokens", "output_tokens", "cost_usd", "consistent",
                "prompt_version")
BATCH_SECONDS = 0.2


def connect(path: Path | None = None, readonly: bool = False) -> sqlite3.Connection:
    path = Path(path or config.TELEMETRY_DB)
    if readonly:
        ensure_schema(path)  # the monitor may start before the first event
        conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=30, check_same_thread=False)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(path, timeout=30, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.executescript(SCHEMA)
        _migrate(conn)
    conn.row_factory = sqlite3.Row
    return conn


def _migrate(conn: sqlite3.Connection) -> None:
    for table, columns in MIGRATIONS.items():
        have = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        for name, kind in columns.items():
            if name not in have:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {kind}")
    conn.commit()


def ensure_schema(path: Path) -> None:
    if not path.exists():
        connect(path).close()


def row_to_event(row: sqlite3.Row) -> dict:
    event = dict(row)
    event["payload"] = json.loads(event.get("payload") or "{}")
    return event


def turn_events(conn: sqlite3.Connection, turn_id: str) -> list[dict]:
    return [row_to_event(r) for r in conn.execute("SELECT * FROM events WHERE turn_id = ? ORDER BY seq", (turn_id,))]


# --------------------------------------------------------------- the writer

_queue: queue.Queue = queue.Queue()
_thread: threading.Thread | None = None
_start_lock = threading.Lock()


def put(event: Event) -> None:
    global _thread
    if _thread is None or not _thread.is_alive():
        with _start_lock:
            if _thread is None or not _thread.is_alive():
                _thread = threading.Thread(target=_run, name="telemetry-writer", daemon=True)
                _thread.start()
    _queue.put(event)


def flush(timeout: float = 10) -> bool:
    """Wait until every queued event is written (tests, shutdown). False on timeout."""
    end = time.monotonic() + timeout
    while _queue.unfinished_tasks:
        if time.monotonic() > end:
            return False
        time.sleep(0.01)
    return True


def _run() -> None:
    conn: sqlite3.Connection | None = None
    path: Path | None = None
    while True:
        batch = [_queue.get()]
        time.sleep(BATCH_SECONDS)  # gather what arrives meanwhile: one transaction per batch
        while True:
            try:
                batch.append(_queue.get_nowait())
            except queue.Empty:
                break
        try:
            if conn is None or path != config.TELEMETRY_DB:  # tests change the path
                if conn is not None:
                    conn.close()
                path, conn = config.TELEMETRY_DB, connect(config.TELEMETRY_DB)
            write(conn, batch)
            backup.maybe_start()
        except Exception as e:  # telemetry must never break the app: the batch is lost, the app goes on
            log.warning("Telemetry: could not write %d events: %s", len(batch), e)
            conn = None
        finally:
            for _ in batch:
                _queue.task_done()


def write(conn: sqlite3.Connection, events: list[Event]) -> None:
    with conn:
        conn.executemany(
            f"INSERT OR IGNORE INTO events ({', '.join(COLUMNS)}) VALUES ({', '.join('?' * len(COLUMNS))})",
            [(e.event_id, e.ts, e.ts_ns, e.schema_version, e.app_version, e.user_id, e.conversation_id, e.turn_index,
              e.turn_id, e.span_id, e.parent_span_id, e.llm_call_id, e.type, e.status, e.duration_ms,
              json.dumps(e.payload, default=str, ensure_ascii=False)) for e in events])
    for turn_id in {e.turn_id for e in events if e.type == "turn.ended" and e.turn_id}:
        try:  # the events are saved even if a summary can't be computed
            with conn:
                upsert_turn(conn, turn_id)
            with conn:  # what probably went wrong: suggested flags for the owner to confirm
                review.add_suggestions(conn, detect.suggest(turn_events(conn, turn_id)))
        except Exception as e:
            log.warning("Telemetry: no summary for turn %s: %s", turn_id, e)
    for e in events:
        if e.type == "feedback.given":
            try:
                with conn:
                    review.apply_feedback(conn, e.payload)
            except Exception as ex:
                log.warning("Telemetry: feedback not applied: %s", ex)


def upsert_turn(conn: sqlite3.Connection, turn_id: str) -> None:
    summary = metrics.turn_summary(turn_events(conn, turn_id))
    if not summary:
        return
    row = {k: summary.get(k) for k in TURN_COLUMNS}
    row["consistent"] = int(bool(summary.get("consistent")))
    conn.execute(f"INSERT OR REPLACE INTO turns ({', '.join(TURN_COLUMNS)}) VALUES ({', '.join('?' * len(TURN_COLUMNS))})",
                 [row[k] for k in TURN_COLUMNS])

