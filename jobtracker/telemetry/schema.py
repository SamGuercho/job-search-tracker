"""Telemetry event: a fixed envelope (who, which conversation/turn, which span, when) + a type and its payload.

Spans are a `<kind>.started` and a `<kind>.ended` event sharing `span_id`; instant events (`llm.text`,
`tool_call.requested`…) carry the span they happened in. The span tree of a turn:

    turn ─┬─ history
          ├─ llm_call   (instant events inside: llm.thinking, llm.text, tool_call.requested)
          ├─ tool, tool (one per tool call the LLM call asked for, linked by llm_call_id;
          │              they overlap when they ran in parallel)
          └─ llm_call …

Shared by the app (writes) and the monitor (reads): changing a field means bumping SCHEMA_VERSION.
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel

SCHEMA_VERSION = 1

# Event types (the payload of each is documented where it is emitted: services/assistant.py)
CONVERSATION_STARTED = "conversation.started"
TURN = "turn"                       # turn.started / turn.ended
HISTORY = "history"                 # history.started / history.ended
LLM_CALL = "llm_call"               # llm_call.started / llm_call.ended
LLM_FIRST_TOKEN = "llm_call.first_token"
LLM_THINKING = "llm.thinking"
LLM_TEXT = "llm.text"
TOOL_CALL_REQUESTED = "tool_call.requested"
TOOL = "tool"                       # tool.started / tool.ended
APPROVAL_REQUESTED = "approval.requested"
APPROVAL_RESOLVED = "approval.resolved"
TITLE_SET = "title.set"
PROVIDER_FALLBACK = "provider.fallback"
COMPACTION = "compaction"
ERROR = "error"

SPAN_KINDS = (TURN, HISTORY, LLM_CALL, TOOL)


class Event(BaseModel):
    event_id: str
    seq: int | None = None            # global order, set by the database
    ts: str                           # UTC ISO 8601 with microseconds
    ts_ns: int                        # same instant, nanoseconds since epoch (for durations)
    schema_version: int = SCHEMA_VERSION
    app_version: str = ""
    user_id: str = ""                 # Tailscale login (or "" locally)
    conversation_id: str = ""
    turn_index: int | None = None
    turn_id: str = ""                 # "<conversation_id>:<turn_index>"
    span_id: str = ""
    parent_span_id: str = ""
    llm_call_id: str = ""             # set on everything an LLM call produced (and the tools it asked for)
    type: str
    status: str = ""
    duration_ms: float | None = None  # on *.ended events
    payload: dict[str, Any] = {}


def turn_id(conversation_id: str, turn_index: int) -> str:
    return f"{conversation_id}:{turn_index}"
