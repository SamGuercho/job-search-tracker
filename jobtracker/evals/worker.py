"""Runs ONE eval case in its own process: `python -m jobtracker.evals.worker <case.json> <result.json>`.

The runner (runner.py) starts it with JOBTRACKER_DATA_DIR / _OUTPUT_DIR / _TELEMETRY_DB pointing at a throwaway
folder, so the case's tools change only the case's copy of the data, never the real one. Tools that reach outside
(web pages, notifications, transcription, Codex) answer what was recorded in the original turn, or an error.
"""
from __future__ import annotations

import json
import sys
import time
from contextlib import closing
from pathlib import Path

EXTERNAL = {"job.fetch_url", "company.find_logo", "prep.start", "recording.transcribe", "recording.reanalyze",
            "push.test", "push.subscribe", "push.unsubscribe", "job.remember_board"}


def _replayer(action: str, recorded: list[dict]):
    def fake(**kwargs):
        same = [r for r in recorded if r.get("action") == action]
        hit = next((r for r in same if (r.get("args") or {}) == kwargs), same[0] if same else None)
        if hit is None:
            raise RuntimeError(f"{action} is not available in an eval run (nothing recorded for it in this case)")
        if hit.get("error"):
            raise RuntimeError(hit["error"])
        return hit.get("result")
    return fake


def run(case: dict) -> dict:
    from jobtracker import actions, config, registry  # noqa: F401  (actions: registers them)
    from jobtracker.evals import checks, fixture
    from jobtracker.services import assistant
    from jobtracker.telemetry import metrics, store

    snap = case.get("snapshot") or {}
    fixture.write(snap.get("fixture") or {}, config.DATA_DIR)
    originals = {name: registry.ACTIONS[name].fn for name in EXTERNAL if name in registry.ACTIONS}
    for name in originals:
        registry.ACTIONS[name].fn = _replayer(name, snap.get("recorded") or [])

    started = time.time()
    out: dict = {"case_id": case.get("id"), "prompt_version": assistant.prompt_version(), "error": ""}
    try:
        chat = assistant.replay(snap.get("history") or [], snap["message"])
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {e}"
        chat = None
    finally:
        for name, fn in originals.items():
            registry.ACTIONS[name].fn = fn
    store.flush()
    with closing(store.connect(config.TELEMETRY_DB)) as conn:
        last = conn.execute("SELECT turn_id FROM events WHERE type = 'turn.started' ORDER BY seq DESC LIMIT 1").fetchone()
        events = store.turn_events(conn, last[0]) if last else []
    summary = metrics.turn_summary(events) if events else {}
    spans = metrics.spans(events)
    tools = sorted((s for s in spans.values() if s.kind == "tool"), key=lambda s: s.start_ns)
    replies = [m.text for m in (chat.messages if chat else []) if m.role == "assistant"]
    out.update({
        "status": summary.get("status") or ("error" if out["error"] else "?"),
        "reply": replies[-1] if replies else "",
        "replies": replies,
        "tool_calls": [{"action": s.start.get("action"), "args": s.start.get("args") or {}, "status": s.status,
                        "error": s.end.get("error") or "", "result_chars": s.end.get("result_chars"),
                        "result": s.end.get("result") or "",
                        "llm_call_id": s.llm_call_id} for s in tools],
        "llm_calls": summary.get("llm_calls"), "cost_usd": summary.get("cost_usd"),
        "wall_ms": summary.get("wall_ms") or (time.time() - started) * 1000,
        "model": f"{summary.get('provider')}/{summary.get('model')}" if summary.get("model") else None,
    })
    out["checks"] = checks.evaluate(case.get("checks") or [], out)
    out["rubric"] = [{"question": q, "verdict": None, "reason": ""} for q in case.get("rubric") or []]
    return out


def main(argv: list[str]) -> int:
    case = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    result = run(case)
    Path(argv[1]).write_text(json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
