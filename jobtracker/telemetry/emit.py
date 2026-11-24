"""Emitting events. The current turn, span and LLM call live in context variables (like users.current()), so
code deep in the loop just calls `emit(...)` / `span(...)`; tool threads get a copy of the context.

Telemetry never raises into the chat: a failure here is logged and ignored.
A turn can also have a live listener (the chat UI's stream): it gets every event, plus `live()` deltas that are
not stored (single tokens).
"""
from __future__ import annotations

import time
import uuid
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime

from .. import __version__, config, users
from ..logs import log
from . import store
from .schema import TURN, Event, turn_id

Listener = Callable[[dict], None]


@dataclass
class _Turn:
    conversation_id: str
    turn_index: int
    user_id: str


@dataclass
class Span:
    span_id: str
    kind: str
    status: str = "ok"
    end: dict = field(default_factory=dict)  # payload of the *.ended event

    def set(self, **payload) -> None:
        self.end.update(payload)


_turn: ContextVar[_Turn | None] = ContextVar("telemetry_turn", default=None)
_span: ContextVar[str] = ContextVar("telemetry_span", default="")
_parent: ContextVar[str] = ContextVar("telemetry_parent", default="")
_llm: ContextVar[str] = ContextVar("telemetry_llm_call", default="")
_listener: ContextVar[Listener | None] = ContextVar("telemetry_listener", default=None)
_turn_span: ContextVar[Span | None] = ContextVar("telemetry_turn_span", default=None)


def new_id() -> str:
    return uuid.uuid4().hex[:16]


def _now() -> tuple[int, str]:
    ns = time.time_ns()
    return ns, datetime.fromtimestamp(ns / 1e9, UTC).isoformat(timespec="microseconds")


def _send(event: Event) -> None:
    try:
        if config.TELEMETRY:
            store.put(event)
        listener = _listener.get()
        if listener:
            listener(event.model_dump())
    except Exception as e:  # never break the chat
        log.warning("Telemetry: %s dropped: %s", event.type, e)


def _build(type_: str, span_id: str, parent: str, status: str = "", duration_ms: float | None = None,
           conversation_id: str | None = None, payload: dict | None = None) -> Event:
    t = _turn.get()
    ns, ts = _now()
    conv = conversation_id if conversation_id is not None else (t.conversation_id if t else "")
    index = t.turn_index if t and conv == t.conversation_id else None
    return Event(event_id=uuid.uuid4().hex, ts=ts, ts_ns=ns, app_version=__version__,
                 user_id=t.user_id if t else _user(), conversation_id=conv, turn_index=index,
                 turn_id=turn_id(conv, index) if index is not None else "", span_id=span_id, parent_span_id=parent,
                 llm_call_id=_llm.get(), type=type_, status=status, duration_ms=duration_ms, payload=payload or {})


def _user() -> str:
    try:
        return users.login()
    except Exception:
        return users.current()


def emit(type_: str, status: str = "", conversation_id: str | None = None, **payload) -> None:
    """An instant event, in the current span."""
    try:
        _send(_build(type_, _span.get(), _parent.get(), status, conversation_id=conversation_id, payload=payload))
    except Exception as e:
        log.warning("Telemetry: %s not recorded: %s", type_, e)


def live(name: str, /, **data) -> None:
    """For the live listener only (not stored): token deltas."""
    listener = _listener.get()
    if listener:
        try:
            listener({"type": "live." + name, "llm_call_id": _llm.get(), **data})
        except Exception as e:
            log.warning("Telemetry: live %s dropped: %s", name, e)


@contextmanager
def span(kind: str, llm_call_id: str | None = None, **payload) -> Iterator[Span]:
    """`<kind>.started` now, `<kind>.ended` on exit (status "error" + an error event if it raises)."""
    s = Span(new_id(), kind)
    parent = _span.get()
    tokens = [_span.set(s.span_id), _parent.set(parent)]
    if llm_call_id is not None:
        tokens.append(_llm.set(llm_call_id))
    start = time.time_ns()
    _send_safe(f"{kind}.started", s.span_id, parent, payload=payload)
    try:
        yield s
    except BaseException as e:
        s.status = "error"
        emit("error", where=kind, error_type=type(e).__name__, message=str(e)[:2000])
        raise
    finally:
        _send_safe(f"{kind}.ended", s.span_id, parent, status=s.status,
                   duration_ms=(time.time_ns() - start) / 1e6, payload=s.end)
        for token in reversed(tokens):
            token.var.reset(token)


def _send_safe(type_: str, span_id: str, parent: str, **kw) -> None:
    try:
        _send(_build(type_, span_id, parent, **kw))
    except Exception as e:
        log.warning("Telemetry: %s not recorded: %s", type_, e)


@contextmanager
def turn(conversation_id: str, turn_index: int, trigger: str, listener: Listener | None = None,
         **payload) -> Iterator[Span]:
    """Everything inside belongs to this turn. A listener (the chat UI stream) gets every event live."""
    tokens = [_turn.set(_Turn(conversation_id, turn_index, _user())), _span.set(""), _parent.set(""), _llm.set("")]
    if listener is not None:
        tokens.append(_listener.set(listener))
    try:
        with span(TURN, trigger=trigger, **payload) as s:
            token = _turn_span.set(s)
            try:
                yield s
            finally:
                _turn_span.reset(token)
    finally:
        for token in reversed(tokens):
            token.var.reset(token)


def current_span() -> str:
    return _span.get()


def current_turn_id() -> str:
    t = _turn.get()
    return turn_id(t.conversation_id, t.turn_index) if t else ""


def set_turn(status: str | None = None, **payload) -> None:
    """Status and payload of the current turn's turn.ended event."""
    s = _turn_span.get()
    if s is not None:
        if status:
            s.status = status
        s.set(**payload)
