"""Flags in the telemetry database: what went wrong in a turn, and where it stands (see flags.py).

Written by the app's telemetry writer (detector suggestions, the user's thumbs down) and by the monitor (the owner's
flags and status changes). Both use SQLite's own locking (WAL), so they never overwrite each other.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime

from . import flags as F

FLAG_FIELDS = ("id", "turn_id", "span_id", "llm_call_id", "conversation_id", "user_id", "category", "note", "source",
               "status", "case_id", "fixed_in", "prompt_version", "created_at", "updated_at")


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _insert(conn: sqlite3.Connection, turn_id: str, category: str, source: str, status: str, note: str = "",
            span_id: str = "", llm_call_id: str = "", on_conflict: str = "DO NOTHING") -> None:
    now = _now()
    conn.execute(
        f"""INSERT INTO flags (turn_id, span_id, llm_call_id, conversation_id, user_id, category, note, source, status,
                               prompt_version, created_at, updated_at)
            VALUES (?, ?, ?, ?, (SELECT user_id FROM turns WHERE turn_id = ?), ?, ?, ?, ?,
                    (SELECT prompt_version FROM turns WHERE turn_id = ?), ?, ?)
            ON CONFLICT (turn_id, span_id, category, source) {on_conflict}""",
        (turn_id, span_id or "", llm_call_id or "", turn_id.rpartition(":")[0], turn_id, category, note, source, status,
         turn_id, now, now))


def add_suggestions(conn: sqlite3.Connection, suggestions: list[dict]) -> None:
    """Detector findings. Never re-suggests one the owner already dismissed or handled."""
    for s in suggestions:
        _insert(conn, s["turn_id"], s["category"], "detector", "suggested", s.get("note", ""),
                s.get("span_id", ""), s.get("llm_call_id", ""))


def apply_feedback(conn: sqlite3.Connection, payload: dict) -> None:
    """The user's 👍/👎 on a reply (event feedback.given). A thumbs down = one open flag per reason; changing or
    removing it replaces the user's flags of that turn that nobody has started working on."""
    turn_id = payload.get("turn_id") or ""
    if not turn_id:
        return
    conn.execute("DELETE FROM flags WHERE turn_id = ? AND source = 'user' AND status IN ('open', 'suggested')", (turn_id,))
    if payload.get("rating") != "down":
        return
    reasons = [r for r in payload.get("reasons") or [] if r in F.USER_REASONS] or ["other"]
    comment = (payload.get("comment") or "").strip()
    for r in reasons:
        label, category = F.USER_REASONS[r]
        note = f"👎 {label}" + (f": {comment}" if comment else "")
        _insert(conn, turn_id, category, "user", "open", note,
                on_conflict="DO UPDATE SET note = excluded.note, updated_at = excluded.updated_at")


def add(conn: sqlite3.Connection, turn_id: str, category: str, note: str = "", span_id: str = "",
        llm_call_id: str = "") -> dict:
    """The owner flags a turn or one of its spans (open right away). Same target and category again: new note."""
    if category not in F.CATEGORY_IDS:
        raise ValueError(f"Unknown category {category!r}")
    with conn:
        _insert(conn, turn_id, category, "owner", "open", note, span_id, llm_call_id,
                on_conflict="DO UPDATE SET note = excluded.note, status = 'open', updated_at = excluded.updated_at")
    row = conn.execute("SELECT * FROM flags WHERE turn_id = ? AND span_id = ? AND category = ? AND source = 'owner'",
                       (turn_id, span_id or "", category)).fetchone()
    return dict(row)


def update(conn: sqlite3.Connection, flag_id: int, **changes) -> dict | None:
    """Change a flag's status (and case_id / fixed_in / note / category)."""
    editable = ("status", "case_id", "fixed_in", "note", "category")
    allowed = {k: v for k, v in changes.items() if k in editable and v is not None}
    if allowed.get("status") and allowed["status"] not in F.STATUSES:
        raise ValueError(f"Unknown status {allowed['status']!r}")
    if allowed.get("category") and allowed["category"] not in F.CATEGORY_IDS:
        raise ValueError(f"Unknown category {allowed['category']!r}")
    if allowed:
        sets = ", ".join(f"{k} = ?" for k in allowed)
        with conn:
            conn.execute(f"UPDATE flags SET {sets}, updated_at = ? WHERE id = ?", [*allowed.values(), _now(), flag_id])
    row = conn.execute("SELECT * FROM flags WHERE id = ?", (flag_id,)).fetchone()
    return dict(row) if row else None


def listing(conn: sqlite3.Connection, statuses: list[str] | None = None, turn_id: str | None = None) -> list[dict]:
    where, args = [], []
    if statuses:
        where.append(f"status IN ({','.join('?' * len(statuses))})")
        args += statuses
    if turn_id:
        where.append("turn_id = ?")
        args.append(turn_id)
    sql = "SELECT * FROM flags" + (f" WHERE {' AND '.join(where)}" if where else "") + " ORDER BY id DESC"
    return [dict(r) for r in conn.execute(sql, args)]


def counts(conn: sqlite3.Connection) -> dict:
    return {r[0]: r[1] for r in conn.execute("SELECT status, count(*) FROM flags GROUP BY status")}


def feedback(conn: sqlite3.Connection, since_ns: int = 0) -> dict:
    """The latest 👍/👎 per turn (a later click replaces an earlier one)."""
    latest: dict[str, str] = {}
    for (payload,) in conn.execute("SELECT payload FROM events WHERE type = 'feedback.given' AND ts_ns >= ? ORDER BY seq",
                                   (since_ns,)):
        p = json.loads(payload or "{}")
        if p.get("turn_id"):
            latest[p["turn_id"]] = p.get("rating") or ""
    values = list(latest.values())
    return {"up": values.count("up"), "down": values.count("down")}
