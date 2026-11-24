"""Chat telemetry: what happened in each conversation turn (LLM calls, tools, approvals, timings, tokens).

    with telemetry.turn(chat.id, index, "user_message"):
        with telemetry.span("llm_call", llm_call_id=..., provider=..., model=...) as s:
            telemetry.emit("llm.text", text=...)
            s.set(usage=...)

Events are stored live in SQLite (store.py) and read by the monitor app (monitor/).
"""
from .emit import Span, current_span, current_turn_id, emit, live, new_id, set_turn, span, turn  # noqa: F401
