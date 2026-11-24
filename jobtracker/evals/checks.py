"""Code checks of one eval run (what "fixed" means, written when the case is frozen). Each returns (ok, detail).

    {"type": "status", "value": "completed"}                   how the turn ended
    {"type": "called", "action": "cv.generate", "min": 1, "max": 1, "args": {"company": "nimbus"}}
                                                               calls of an action (args: substrings, any case)
    {"type": "not_called", "action": "company.delete"}
    {"type": "max_tool_calls", "value": 4}
    {"type": "no_duplicate_calls"}                             the same action with the same arguments twice
    {"type": "no_tool_errors"}
    {"type": "max_cost_usd", "value": 0.05}
    {"type": "tool_result_contains", "action": "cv.generate", "text": "\"title\": \"Data Scientist\""}
                                                               a call of that action returned this (exact case)
    {"type": "tool_result_not_contains", "action": "cv.generate", "text": "Staff"}   no call of it returned this
    {"type": "reply_contains", "text": "Nimbus"}               the final reply (any case)
    {"type": "reply_not_contains", "text": "I can't"}
    {"type": "reply_matches", "pattern": "regex"}
    {"type": "max_reply_chars", "value": 1200}
"""
from __future__ import annotations

import json
import re


def _matches(args: dict, wanted: dict) -> bool:
    for key, value in (wanted or {}).items():
        have = args.get(key)
        text = have if isinstance(have, str) else json.dumps(have, ensure_ascii=False, default=str)
        if str(value).lower() not in (text or "").lower():
            return False
    return True


def run_check(check: dict, result: dict) -> tuple[bool, str]:
    kind = check.get("type")
    calls = result.get("tool_calls") or []
    reply = result.get("reply") or ""
    if kind == "status":
        return result.get("status") == check.get("value", "completed"), f"ended {result.get('status')}"
    if kind in ("called", "not_called"):
        hits = [c for c in calls if c["action"] == check.get("action") and _matches(c.get("args") or {}, check.get("args"))]
        low = 0 if kind == "not_called" else check.get("min", 1)
        high = 0 if kind == "not_called" else check.get("max")
        ok = len(hits) >= low and (high is None or len(hits) <= high)
        return ok, f"{check.get('action')} called {len(hits)}×"
    if kind in ("tool_result_contains", "tool_result_not_contains"):
        results = [c.get("result") or "" for c in calls if not check.get("action") or c["action"] == check["action"]]
        found = sum(check["text"] in r for r in results)
        if kind == "tool_result_contains":
            return found > 0, f"in {found} of {len(results)} {check.get('action') or 'tool'} results"
        return found == 0, f"in {found} of {len(results)} {check.get('action') or 'tool'} results"
    if kind == "max_tool_calls":
        return len(calls) <= check["value"], f"{len(calls)} tool calls"
    if kind == "no_duplicate_calls":
        keys = [c["action"] + json.dumps(c.get("args") or {}, sort_keys=True, default=str) for c in calls]
        dupes = sorted({c["action"] for c, k in zip(calls, keys, strict=True) if keys.count(k) > 1})
        return not dupes, f"repeated: {', '.join(dupes)}" if dupes else "none"
    if kind == "no_tool_errors":
        errors = [f"{c['action']}: {c.get('error')}" for c in calls if c.get("status") == "error"]
        return not errors, "; ".join(errors)[:300] if errors else "none"
    if kind == "max_cost_usd":
        cost = result.get("cost_usd") or 0
        return cost <= check["value"], f"${cost:.4f}"
    if kind == "reply_contains":
        return check["text"].lower() in reply.lower(), f"{'found' if check['text'].lower() in reply.lower() else 'missing'}"
    if kind == "reply_not_contains":
        found = check["text"].lower() in reply.lower()
        return not found, "found" if found else "absent"
    if kind == "reply_matches":
        found = re.search(check["pattern"], reply, re.IGNORECASE | re.MULTILINE) is not None
        return found, "matches" if found else "no match"
    if kind == "max_reply_chars":
        return len(reply) <= check["value"], f"{len(reply)} chars"
    return False, f"unknown check type {kind!r}"


def evaluate(checks: list[dict], result: dict) -> list[dict]:
    out = []
    for c in checks or []:
        try:
            ok, detail = run_check(c, result)
        except Exception as e:  # a malformed check fails, it never stops the run
            ok, detail = False, f"check error: {type(e).__name__}: {e}"
        out.append({"check": c, "ok": ok, "detail": detail})
    return out
