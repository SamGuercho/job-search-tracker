"""Queries over the telemetry database, shaped for the monitor's pages (flags: see jobtracker/telemetry/review.py)."""
from __future__ import annotations

import json
import sqlite3
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from jobtracker.telemetry import flags as F
from jobtracker.telemetry import metrics, review
from jobtracker.telemetry.store import row_to_event, turn_events

DAY_NS = 86_400 * 10**9


def percentile(values: list[float], q: float) -> float | None:
    values = sorted(v for v in values if v is not None)
    if not values:
        return None
    k = (len(values) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(values) - 1)
    return values[lo] + (values[hi] - values[lo]) * (k - lo)


def dist(values: list[float]) -> dict:
    values = [v for v in values if v is not None]
    return {"n": len(values), "p50": percentile(values, 0.5), "p95": percentile(values, 0.95),
            "max": max(values) if values else None}


def _since(days: int) -> int:
    return time.time_ns() - days * DAY_NS


def _where(days: int, user: str | None, column: str = "started_ns") -> tuple[str, list]:
    sql, args = f"{column} >= ?", [_since(days)]
    if user is not None:
        sql += " AND user_id = ?"
        args.append(user)
    return sql, args


def users(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT DISTINCT user_id FROM events ORDER BY user_id")]


def overview(conn: sqlite3.Connection, days: int = 30, user: str | None = None) -> dict:
    where, args = _where(days, user)
    turns = [dict(r) for r in conn.execute(f"SELECT * FROM turns WHERE {where} ORDER BY started_ns", args)]
    ev_where, ev_args = _where(days, user, "ts_ns")

    def events(*types):
        marks = ",".join("?" * len(types))
        return [row_to_event(r) for r in conn.execute(
            f"SELECT * FROM events WHERE type IN ({marks}) AND {ev_where}", [*types, *ev_args])]

    calls = events("llm_call.ended")
    tools = events("tool.ended")
    tool_starts = {e["span_id"]: e["payload"].get("action") for e in events("tool.started")}
    approvals = events("approval.resolved")

    # turns per day and user (local days)
    start = datetime.now().date() - timedelta(days=days - 1)
    per_day = {(start + timedelta(days=i)).isoformat(): defaultdict(int) for i in range(days)}
    for t in turns:
        day = datetime.fromtimestamp(t["started_ns"] / 1e9).date().isoformat()
        if day in per_day:
            per_day[day][t["user_id"] or "(local)"] += 1

    by_action: dict[str, list] = defaultdict(list)
    for e in tools:
        by_action[tool_starts.get(e["span_id"]) or "?"].append(e)
    by_model: dict[str, dict] = defaultdict(lambda: {"calls": 0, "input_tokens": 0, "cached_tokens": 0,
                                                     "output_tokens": 0, "cost_usd": 0.0, "durations": []})
    starts = {e["span_id"]: e["payload"] for e in events("llm_call.started")}
    for e in calls:
        s = starts.get(e["span_id"], {})
        m = by_model[f"{s.get('provider', '?')}/{s.get('model', '?')}"]
        u = e["payload"].get("usage") or {}
        m["calls"] += 1
        m["durations"].append(e["duration_ms"])
        for k in ("input_tokens", "cached_tokens", "output_tokens"):
            m[k] += u.get(k) or 0
        m["cost_usd"] += e["payload"].get("cost_usd") or 0
    by_user: dict[str, dict] = defaultdict(lambda: {"turns": 0, "cost_usd": 0.0, "output_tokens": 0, "wall": []})
    for t in turns:
        u = by_user[t["user_id"] or "(local)"]
        u["turns"] += 1
        u["cost_usd"] += t["cost_usd"] or 0
        u["output_tokens"] += t["output_tokens"] or 0
        u["wall"].append(t["wall_ms"])
    statuses: dict[str, int] = defaultdict(int)
    for t in turns:
        statuses[t["status"] or "?"] += 1

    return {
        "days": days, "user": user, "users": users(conn),
        "totals": {
            "turns": len(turns), "conversations": len({t["conversation_id"] for t in turns}),
            "llm_calls": len(calls), "tool_calls": len(tools),
            "tool_errors": sum(1 for e in tools if e["status"] == "error"),
            "cost_usd": round(sum(t["cost_usd"] or 0 for t in turns), 4),
            "input_tokens": sum((t["input_tokens"] or 0) + (t["cached_tokens"] or 0) for t in turns),
            "output_tokens": sum(t["output_tokens"] or 0 for t in turns),
            "errors": len(events("error")), "fallbacks": len(events("provider.fallback")),
            "inconsistent_turns": sum(1 for t in turns if not t["consistent"]),
            "approved": sum(1 for e in approvals if e["payload"].get("approved")),
            "rejected": sum(1 for e in approvals if not e["payload"].get("approved")),
        },
        "review": {"flags": review.counts(conn), "feedback": review.feedback(conn, _since(days))},
        "statuses": dict(statuses),
        "latency": {
            "turn_wall_ms": dist([t["wall_ms"] for t in turns]),
            "ttft_ms": dist([t["ttft_ms"] for t in turns]),
            "llm_call_ms": dist([e["duration_ms"] for e in calls]),
            "tool_ms": dist([e["duration_ms"] for e in tools]),
            "tools_wall_per_turn_ms": dist([t["tool_wall_ms"] for t in turns if t["tool_calls"]]),
            "tools_sum_per_turn_ms": dist([t["tool_sum_ms"] for t in turns if t["tool_calls"]]),
        },
        "per_day": [{"date": d, "by_user": dict(v)} for d, v in per_day.items()],
        "by_user": sorted(({"user": k, "turns": v["turns"], "cost_usd": round(v["cost_usd"], 4),
                            "output_tokens": v["output_tokens"], "wall": dist(v["wall"])} for k, v in by_user.items()),
                          key=lambda r: -r["turns"]),
        "by_model": sorted(({"model": k, **{n: v[n] for n in ("calls", "input_tokens", "cached_tokens", "output_tokens")},
                             "cost_usd": round(v["cost_usd"], 4), "latency": dist(v["durations"])}
                            for k, v in by_model.items()), key=lambda r: -r["calls"]),
        "by_tool": sorted(({"action": a, "calls": len(es), "errors": sum(1 for e in es if e["status"] == "error"),
                            "pending": sum(1 for e in es if e["status"] == "pending_approval"),
                            "latency": dist([e["duration_ms"] for e in es]),
                            "result_tokens": dist([e["payload"].get("result_tokens") for e in es])}
                           for a, es in by_action.items()), key=lambda r: -r["calls"]),
    }


def conversations(conn: sqlite3.Connection, days: int = 30, user: str | None = None) -> list[dict]:
    where, args = _where(days, user)
    rows = conn.execute(f"""
        SELECT conversation_id, user_id, COUNT(*) AS turns, SUM(cost_usd) AS cost_usd,
               MIN(started_ns) AS first_ns, MAX(started_ns) AS last_ns,
               SUM(CASE WHEN status NOT IN ('completed', 'waiting_approval') THEN 1 ELSE 0 END) AS problems,
               SUM(tool_calls) AS tool_calls
        FROM turns WHERE {where} GROUP BY conversation_id ORDER BY last_ns DESC""", args)
    out = [dict(r) for r in rows]
    known = {c["conversation_id"] for c in out}
    ev_where, ev_args = _where(days, user, "ts_ns")
    for r in conn.execute(  # conversations whose first turn is still running (or crashed before its end)
            f"SELECT conversation_id, user_id, MIN(ts_ns), MAX(ts_ns) FROM events WHERE type = 'turn.started' "
            f"AND {ev_where} GROUP BY conversation_id", ev_args):
        if r[0] not in known:
            out.insert(0, {"conversation_id": r[0], "user_id": r[1], "turns": 0, "cost_usd": 0, "first_ns": r[2],
                           "last_ns": r[3], "problems": 0, "tool_calls": 0, "running": True})
    for c in out:
        c["title"] = _title(conn, c["conversation_id"])
    return out


def _title(conn: sqlite3.Connection, conversation_id: str) -> str:
    row = conn.execute("SELECT title FROM turns WHERE conversation_id = ? AND title IS NOT NULL "
                       "ORDER BY turn_index DESC LIMIT 1", (conversation_id,)).fetchone()
    if row:
        return row[0]
    row = conn.execute("SELECT payload FROM events WHERE conversation_id = ? AND type = 'conversation.started'",
                       (conversation_id,)).fetchone()
    return (json.loads(row[0]).get("title") if row else None) or conversation_id


def conversation(conn: sqlite3.Connection, conversation_id: str) -> dict:
    turns = [dict(r) for r in conn.execute("SELECT * FROM turns WHERE conversation_id = ? ORDER BY turn_index",
                                           (conversation_id,))]
    done = {t["turn_id"] for t in turns}
    running = [r[0] for r in conn.execute(  # turns that started but haven't ended (yet, or crashed)
        "SELECT DISTINCT turn_id FROM events WHERE conversation_id = ? AND type = 'turn.started'", (conversation_id,))
        if r[0] not in done]
    for turn_id in running:
        summary = metrics.turn_summary(turn_events(conn, turn_id))
        if summary:
            turns.append({k: v for k, v in summary.items() if k not in ("checks", "steps")})
    turns.sort(key=lambda t: t["turn_index"])
    first = conn.execute("SELECT user_id, payload FROM events WHERE conversation_id = ? AND type = 'turn.started' "
                         "ORDER BY seq", (conversation_id,)).fetchall()
    texts = {}
    for t in turns:
        row = conn.execute("SELECT payload FROM events WHERE turn_id = ? AND type = 'turn.started'",
                           (t["turn_id"],)).fetchone()
        p = json.loads(row[0]) if row else {}
        texts[t["turn_id"]] = p.get("text") or (f"{'Approved' if p.get('approve') else 'Rejected'} {p.get('action')}"
                                                if t.get("trigger") == "approval" else "")
    open_flags = dict(conn.execute("SELECT turn_id, count(*) FROM flags WHERE conversation_id = ? AND status IN "
                                   "('suggested', 'open', 'in_case') GROUP BY turn_id", (conversation_id,)).fetchall())
    for t in turns:
        t["user_text"] = texts.get(t["turn_id"], "")
        t["flags"] = open_flags.get(t["turn_id"], 0)
    return {"conversation_id": conversation_id, "title": _title(conn, conversation_id),
            "user_id": first[0][0] if first else "", "turns": turns}


def turn(conn: sqlite3.Connection, turn_id: str) -> dict:
    events = turn_events(conn, turn_id)
    if not events:
        return {}
    summary = metrics.turn_summary(events)
    all_spans = metrics.spans(events)
    origin = min(s.start_ns for s in all_spans.values()) if all_spans else events[0]["ts_ns"]
    spans = sorted((s.to_dict(origin) for s in all_spans.values()), key=lambda s: s["start_ms"])
    for e in events:
        e["at_ms"] = (e["ts_ns"] - origin) / 1e6
    scores = [dict(r) | {"details": json.loads(r["details"] or "{}")}
              for r in conn.execute("SELECT * FROM turn_scores WHERE turn_id = ? ORDER BY id", (turn_id,))]
    conv, _, index = turn_id.rpartition(":")
    neighbours = [r[0] for r in conn.execute(
        "SELECT DISTINCT turn_index FROM events WHERE conversation_id = ? AND turn_index IS NOT NULL ORDER BY 1", (conv,))]
    return {"turn_id": turn_id, "conversation_id": conv, "turn_index": int(index), "turn_indexes": neighbours,
            "title": _title(conn, conv), "origin_ns": origin,
            "origin": datetime.fromtimestamp(origin / 1e9, UTC).isoformat(),
            "summary": summary, "spans": spans, "events": events, "scores": scores,
            "flags": review.listing(conn, turn_id=turn_id),
            "running": bool(summary) and summary.get("ended_ns") is None}


# ------------------------------------------------------------------ review queue

def _turn_text(conn: sqlite3.Connection, turn_id: str) -> str:
    row = conn.execute("SELECT payload FROM events WHERE turn_id = ? AND type = 'turn.started'", (turn_id,)).fetchone()
    p = json.loads(row[0]) if row else {}
    if p.get("text") or not p.get("action"):
        return p.get("text") or ""
    return f"{'Approved' if p.get('approve') else 'Rejected'} {p.get('action')}"


def _span_label(conn: sqlite3.Connection, span_id: str) -> str:
    if not span_id:
        return ""
    row = conn.execute("SELECT type, payload FROM events WHERE span_id = ? AND type LIKE '%.started'", (span_id,)).fetchone()
    if not row:
        return ""
    p = json.loads(row[1] or "{}")
    if row[0] == "tool.started":
        return f"tool {p.get('action')}"
    if row[0] == "llm_call.started":
        return f"LLM call step {p.get('step')} ({p.get('model')})"
    return row[0].rsplit(".", 1)[0]


def flag_list(conn: sqlite3.Connection, statuses: list[str] | None = None) -> list[dict]:
    """Flags with what's needed to read them without opening the turn: conversation, user message, span."""
    out = review.listing(conn, statuses)
    titles: dict[str, str] = {}
    labels = {c[0]: (c[1], c[2]) for c in F.CATEGORIES}
    for f in out:
        conv = f["conversation_id"] or f["turn_id"].rpartition(":")[0]
        if conv not in titles:
            titles[conv] = _title(conn, conv)
        f["title"] = titles[conv]
        f["user_text"] = _turn_text(conn, f["turn_id"])
        f["span_label"] = _span_label(conn, f["span_id"])
        f["group"], f["label"] = labels.get(f["category"], ("Other", f["category"]))
    return out


def review_queue(conn: sqlite3.Connection, full: bool = True) -> dict:
    """For the fixing process (Claude Code): the flags still to work on, each with its turn's whole trace."""
    items = flag_list(conn, list(F.QUEUE))
    traces = {}
    if full:
        for turn_id in {f["turn_id"] for f in items}:
            traces[turn_id] = turn(conn, turn_id)
    return {"catalog": F.catalog(), "flags": items, "turns": traces}
