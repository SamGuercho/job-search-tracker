"""LLM access, isolated here so the provider can be swapped (OpenAI today, Claude as fallback).

`structured(instructions, text, schema)` returns an instance of the pydantic
`schema`, filled by the model with schema-constrained (structured) output.

OpenAI is used first. When it fails because of quota, billing, access or rate limits
and ANTHROPIC_API_KEY is set, the call is retried on Claude and a notice is recorded
(`pop_notice()`), which the API sends to the browser so the user sees the switch.
Without an OpenAI key but with an Anthropic key, Claude is simply used.
"""
from __future__ import annotations

import os
from contextvars import ContextVar
from typing import TypeVar

from pydantic import BaseModel

from .. import config
from ..logs import log
from . import claude, usage

S = TypeVar("S", bound=BaseModel)

NO_KEY = "No AI key set: add OPENAI_API_KEY (or ANTHROPIC_API_KEY) to the .env file and restart the server."


class LLMUnavailable(RuntimeError):
    pass


def api_key() -> str:
    """The key as configured, cleaned of what copy-pasting adds: spaces, quotes, Windows line endings
    (Notepad saves "\r\n" and Docker keeps the "\r"), and an accidental "OPENAI_API_KEY=" prefix."""
    key = os.environ.get("OPENAI_API_KEY", "").strip().strip("\"'“”‘’").strip()
    return key.removeprefix("OPENAI_API_KEY=").strip()


def openai_available() -> bool:
    return bool(api_key())


def available() -> bool:
    return openai_available() or claude.available()


def client():
    """OpenAI client using the cleaned key."""
    from openai import OpenAI
    return OpenAI(api_key=api_key())


class StreamFailed(RuntimeError):
    """The stream reported an error or ended without a final response."""


def reasoning_summary(model: str) -> dict:
    """Reasoning models can send a summary of their reasoning (shown live and recorded)."""
    return {"reasoning": {"summary": "auto"}} if model.startswith(("gpt-5", "o1", "o3", "o4")) else {}


def stream_response(sink=None, **kwargs):
    """A streamed Responses API call; returns the final response (like responses.create would).

    `sink` (optional, same contract as claude.chat): delta(kind, key, text), block_done(kind, key, text),
    tool_call(call_id, name, arguments_json). OpenAI errors raised when the call starts propagate (fallback)."""
    final = None
    for event in client().responses.create(stream=True, **kwargs):
        kind = event.type
        if kind == "response.output_text.delta":
            sink and sink.delta("text", ("text", event.output_index, event.content_index), event.delta)
        elif kind == "response.output_text.done":
            sink and sink.block_done("text", ("text", event.output_index, event.content_index), event.text)
        elif kind == "response.reasoning_summary_text.delta":
            sink and sink.delta("thinking", ("reasoning", event.output_index, event.summary_index), event.delta)
        elif kind == "response.reasoning_summary_text.done":
            sink and sink.block_done("thinking", ("reasoning", event.output_index, event.summary_index), event.text)
        elif kind == "response.output_item.done" and getattr(event.item, "type", "") == "function_call":
            sink and sink.tool_call(event.item.call_id, event.item.name, event.item.arguments)
        elif kind in ("response.completed", "response.incomplete", "response.failed"):
            final = event.response
        elif kind == "error":
            raise StreamFailed(f"OpenAI stream error: {getattr(event, 'message', event)}")
    if final is None:
        raise StreamFailed("The OpenAI stream ended without a response.")
    return final


def fallback_reason(error: Exception) -> str | None:
    """Why this OpenAI error should switch to Claude, or None if it shouldn't (bad request, outage…).
    Rate limits arrive here only after the OpenAI SDK's own retries."""
    import openai

    if isinstance(error, openai.RateLimitError):
        return ("OpenAI quota or billing limit reached" if getattr(error, "code", None) == "insufficient_quota"
                else "OpenAI rate limit reached")
    if isinstance(error, openai.AuthenticationError):
        return "OpenAI rejected the API key"
    if isinstance(error, openai.PermissionDeniedError):
        return "OpenAI denied access"
    if isinstance(error, openai.APIStatusError) and error.status_code == 402:
        return "OpenAI billing problem"
    return None


def switch_notice(reason: str, scope: str = "") -> str:
    return f"⚠ {reason}: switched to Claude ({claude.model()}){scope}."


# Notices for the current request (one context per request, so users never see each other's).
_notices: ContextVar[tuple[str, ...]] = ContextVar("llm_notices", default=())


def _note(message: str) -> None:
    log.warning(message)
    if message not in _notices.get():
        _notices.set(_notices.get() + (message,))


def reset_notices() -> None:
    _notices.set(())


def pop_notice() -> str | None:
    notices, _ = _notices.get(), _notices.set(())
    return " ".join(notices) or None


def structured(instructions: str, text: str, schema: type[S]) -> S:
    if not available():
        raise LLMUnavailable(NO_KEY)
    if openai_available():
        from openai import OpenAIError
        try:
            return _openai_structured(instructions, text, schema)
        except OpenAIError as e:
            reason = fallback_reason(e)
            if not (reason and claude.available()):
                raise LLMUnavailable(f"OpenAI error: {e}") from e
            _note(switch_notice(reason))
    return _claude_structured(instructions, text, schema)


def _openai_structured(instructions: str, text: str, schema: type[S]) -> S:
    response = client().responses.parse(
        model=config.OPENAI_MODEL, instructions=instructions, input=text, text_format=schema)
    usage.record_openai(response, config.OPENAI_MODEL)
    if response.output_parsed is None:
        raise LLMUnavailable("The model returned no structured result (refusal or truncated output).")
    return response.output_parsed


def _claude_structured(instructions: str, text: str, schema: type[S]) -> S:
    import anthropic
    try:
        return claude.structured(instructions, text, schema)
    except claude.ClaudeRefused as e:
        raise LLMUnavailable(str(e)) from e
    except anthropic.AnthropicError as e:
        raise LLMUnavailable(f"Claude error: {e}") from e
