"""Detectors: when a turn ends, find what probably went wrong and propose it as a "suggested" flag.

The owner confirms (→ open) or dismisses each suggestion in the monitor's Review page. Rules only, no AI call:
  - a tool failed: invalid arguments / unknown id → wrong_argument, unknown tool → wrong_tool, else tool_failed
  - a tool returned an error in its result (it ran, but didn't do what was planned) → tool_failed
  - the same tool with the same arguments more than once in a turn → redundant_call (on the repeats)
  - the turn stopped early: max steps, cut off, refused, error → incomplete_task
  - the user rejected an action the assistant proposed → wrong_intent, on the turn that proposed it
  - a turn above the cost or time threshold → too_costly / too_slow
"""
from __future__ import annotations

import json
import re

from .. import config
from . import metrics

ARGUMENT_ERRORS = re.compile(r"validation error|ValidationError|Field required|unexpected keyword|missing \d+ required|"
                             r"NotFound|not found|Invalid|TypeError|ValueError", re.IGNORECASE)
STOPPED = {"max_steps": "stopped: too many steps", "max_tokens": "reply cut off (too long)",
           "incomplete": "the model didn't finish", "refusal": "the model refused", "error": "the turn failed"}


def suggest(events: list[dict]) -> list[dict]:
    """Suggested flags for one finished turn: {turn_id, span_id, llm_call_id, category, note}."""
    if not events:
        return []
    turn_id = events[0]["turn_id"]
    spans = metrics.spans(events)
    turn = next((s for s in spans.values() if s.kind == "turn"), None)
    out: list[dict] = []

    def add(category: str, note: str, span=None, target: str | None = None):
        out.append({"turn_id": target or turn_id, "span_id": span.span_id if span else "",
                    "llm_call_id": span.llm_call_id if span else "", "category": category, "note": note})

    seen: dict[str, str] = {}
    for s in sorted((s for s in spans.values() if s.kind == "tool"), key=lambda s: s.start_ns):
        action = s.start.get("action") or "?"
        if s.status == "error":
            error = str(s.end.get("error") or "")
            category = ("wrong_tool" if error.startswith("Unknown tool")
                        else "wrong_argument" if ARGUMENT_ERRORS.search(error) else "tool_failed")
            add(category, f"{action} failed: {error[:300]}", s)
        elif s.status == "done" and (problem := _result_error(s.end.get("result"))):
            add("tool_failed", f"{action} returned an error: {problem[:300]}", s)
        if action == "chat.set_title":
            continue
        key = action + json.dumps(s.start.get("args") or {}, sort_keys=True, default=str)
        if key in seen:
            add("redundant_call", f"{action} called again with the same arguments (first: span {seen[key]})", s)
        else:
            seen[key] = s.span_id

    if turn is not None:
        if turn.status in STOPPED:
            add("incomplete_task", STOPPED[turn.status])
        summary = metrics.turn_summary(events)
        cost, wall = summary.get("cost_usd") or 0, summary.get("wall_ms") or 0
        if config.FLAG_COST_USD and cost > config.FLAG_COST_USD:
            add("too_costly", f"${cost:.3f} for this turn (threshold ${config.FLAG_COST_USD:.2f})")
        if config.FLAG_SLOW_S and wall > config.FLAG_SLOW_S * 1000:
            add("too_slow", f"{wall / 1000:.0f} s for this turn (threshold {config.FLAG_SLOW_S:.0f} s)")

    for e in events:
        if e["type"] == "approval.resolved" and not e["payload"].get("approved"):
            target = e["payload"].get("requested_turn_id") or turn_id
            add("wrong_intent", f"the user rejected {e['payload'].get('action')}", target=target)
    return out


def _result_error(result) -> str:
    """The error a tool reported inside a successful result ({"result": {"error": …}} or {"ok": false})."""
    try:
        data = json.loads(result) if isinstance(result, str) else result
    except ValueError:
        return ""
    inner = data.get("result") if isinstance(data, dict) else None
    if isinstance(inner, dict):
        if inner.get("error"):
            return str(inner["error"])
        if inner.get("ok") is False:
            return str(inner.get("reason") or inner.get("message") or "ok: false")
    return ""
