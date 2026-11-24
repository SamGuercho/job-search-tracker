"""Latency and usage math over one turn's events (used by the writer for the `turns` table and by the monitor).

Parallel work is never added up as if it were sequential: the wall time of a group of spans is the length of the
UNION of their intervals; the sum is reported next to it as "work", and parallelism = sum / union.
`consistency()` checks the aggregates against the raw start/end timestamps.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from .schema import SPAN_KINDS

TOLERANCE_MS = 5.0  # clock reads are not atomic with the work they measure


@dataclass
class Span:
    span_id: str
    kind: str
    parent_span_id: str = ""
    llm_call_id: str = ""
    start_ns: int = 0
    end_ns: int | None = None
    status: str = ""
    start: dict = field(default_factory=dict)   # payload of *.started
    end: dict = field(default_factory=dict)     # payload of *.ended
    events: list[dict] = field(default_factory=list)  # instant events inside this span

    @property
    def duration_ms(self) -> float | None:
        return None if self.end_ns is None else (self.end_ns - self.start_ns) / 1e6

    def to_dict(self, origin_ns: int = 0) -> dict:
        return {"span_id": self.span_id, "kind": self.kind, "parent_span_id": self.parent_span_id,
                "llm_call_id": self.llm_call_id, "status": self.status,
                "start_ms": (self.start_ns - origin_ns) / 1e6,
                "end_ms": None if self.end_ns is None else (self.end_ns - origin_ns) / 1e6,
                "duration_ms": self.duration_ms, "start": self.start, "end": self.end, "events": self.events}


def spans(events: list[dict]) -> dict[str, Span]:
    """Pair *.started / *.ended events into spans; attach instant events to the span they happened in."""
    out: dict[str, Span] = {}
    for e in events:
        kind, _, phase = e["type"].rpartition(".")
        if phase == "started" and kind in SPAN_KINDS:  # not conversation.started: an instant event
            out[e["span_id"]] = Span(e["span_id"], kind, e.get("parent_span_id") or "", e.get("llm_call_id") or "",
                                     e["ts_ns"], start=e.get("payload") or {})
        elif phase == "ended" and kind in SPAN_KINDS and e["span_id"] in out:
            s = out[e["span_id"]]
            s.end_ns, s.status, s.end = e["ts_ns"], e.get("status") or "", e.get("payload") or {}
        elif e["span_id"] in out:
            out[e["span_id"]].events.append(e)
    return out


def union_ms(intervals: list[tuple[int, int]]) -> float:
    """Length of the union of [start, end] intervals (ns in, ms out): overlapping parallel work counts once."""
    total, cur_start, cur_end = 0, None, None
    for start, end in sorted(intervals):
        if cur_end is None or start > cur_end:
            if cur_end is not None:
                total += cur_end - cur_start
            cur_start, cur_end = start, end
        else:
            cur_end = max(cur_end, end)
    if cur_end is not None:
        total += cur_end - cur_start
    return total / 1e6


def _closed(ss) -> list[tuple[int, int]]:
    return [(s.start_ns, s.end_ns) for s in ss if s.end_ns is not None]


def steps(all_spans: dict[str, Span]) -> list[dict]:
    """One step per LLM call: the call itself, then the tools it asked for (wall = union, work = sum)."""
    out = []
    calls = sorted((s for s in all_spans.values() if s.kind == "llm_call"), key=lambda s: s.start_ns)
    for i, call in enumerate(calls):
        # tools run after the call ended (siblings in the turn), linked to it by llm_call_id
        tools = [s for s in all_spans.values() if s.kind == "tool" and call.llm_call_id
                 and s.llm_call_id == call.llm_call_id]
        tool_sum = sum(s.duration_ms or 0 for s in tools)
        tool_wall = union_ms(_closed(tools))
        out.append({"step": i, "span_id": call.span_id, "llm_call_id": call.llm_call_id, "llm_ms": call.duration_ms,
                    "tools": len(tools), "tool_wall_ms": tool_wall, "tool_sum_ms": tool_sum,
                    "parallelism": round(tool_sum / tool_wall, 2) if tool_wall else None,
                    "wall_ms": union_ms(_closed([call] + tools))})
    return out


def consistency(all_spans: dict[str, Span], turn: Span | None) -> list[dict]:
    """Checks that the aggregates agree with the raw timestamps. Each: {check, ok, detail}."""
    checks = []
    open_spans = [s for s in all_spans.values() if s.end_ns is None]
    checks.append({"check": "every span ended", "ok": not open_spans or (turn is not None and turn.end_ns is None),
                   "detail": f"{len(open_spans)} still open" if open_spans else "all closed"})
    outside = []
    for s in all_spans.values():
        parent = all_spans.get(s.parent_span_id)
        if parent and s.end_ns is not None and parent.end_ns is not None:
            if (parent.start_ns - s.start_ns) / 1e6 > TOLERANCE_MS or (s.end_ns - parent.end_ns) / 1e6 > TOLERANCE_MS:
                outside.append(f"{s.kind} {s.span_id}")
    checks.append({"check": "children inside their parent", "ok": not outside,
                   "detail": ", ".join(outside) if outside else "yes"})
    if turn is not None and turn.end_ns is not None:
        children = [s for s in all_spans.values() if s is not turn]
        covered = union_ms(_closed(children))
        step_wall = sum(st["wall_ms"] for st in steps(all_spans))
        wall = turn.duration_ms or 0
        checks.append({"check": "steps fit in the turn", "ok": step_wall <= wall + TOLERANCE_MS,
                       "detail": f"steps {step_wall:.0f} ms ≤ turn {wall:.0f} ms"})
        checks.append({"check": "unaccounted time", "ok": covered <= wall + TOLERANCE_MS,
                       "detail": f"{max(wall - covered, 0):.0f} ms outside any span (saving, app overhead)"})
    requested = sum(1 for s in all_spans.values() for e in s.events if e["type"] == "tool_call.requested")
    ran = sum(1 for s in all_spans.values() if s.kind == "tool" and s.llm_call_id)  # approved later: no request
    checks.append({"check": "every requested tool has a span", "ok": requested == ran,
                   "detail": f"{requested} requested, {ran} ran"})
    return checks


def turn_summary(events: list[dict]) -> dict:
    """Totals of one turn (the `turns` row). Empty dict if the turn has no turn.started yet."""
    all_spans = spans(events)
    turn = next((s for s in all_spans.values() if s.kind == "turn"), None)
    if turn is None:
        return {}
    first = events[0]
    calls = [s for s in all_spans.values() if s.kind == "llm_call"]
    tools = [s for s in all_spans.values() if s.kind == "tool"]
    usages = [s.end.get("usage") or {} for s in calls]
    first_tokens = [e["ts_ns"] for e in events if e["type"] == "llm_call.first_token"]
    st = steps(all_spans)
    checks = consistency(all_spans, turn)
    last_call = max(calls, key=lambda s: s.start_ns, default=None)
    return {
        "turn_id": first["turn_id"], "conversation_id": first["conversation_id"], "turn_index": first["turn_index"],
        "user_id": first.get("user_id", ""), "trigger": turn.start.get("trigger"),
        "title": turn.end.get("title") or turn.start.get("title"), "status": turn.status or "running",
        "provider": last_call.start.get("provider") if last_call else None,
        "model": last_call.start.get("model") if last_call else None,
        "started_ns": turn.start_ns, "ended_ns": turn.end_ns, "wall_ms": turn.duration_ms,
        "ttft_ms": (min(first_tokens) - turn.start_ns) / 1e6 if first_tokens else None,
        "llm_calls": len(calls), "llm_ms": sum(s.duration_ms or 0 for s in calls),
        "tool_calls": len(tools), "tool_errors": sum(1 for s in tools if s.status == "error"),
        "tool_wall_ms": sum(x["tool_wall_ms"] for x in st), "tool_sum_ms": sum(x["tool_sum_ms"] for x in st),
        "input_tokens": sum(u.get("input_tokens") or 0 for u in usages),
        "cached_tokens": sum(u.get("cached_tokens") or 0 for u in usages),
        "output_tokens": sum(u.get("output_tokens") or 0 for u in usages),
        "cost_usd": round(sum(s.end.get("cost_usd") or 0 for s in calls), 6),
        "consistent": all(c["ok"] for c in checks), "checks": checks, "steps": st,
    }
