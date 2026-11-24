"""Claude (Anthropic API): the fallback when OpenAI fails on quota, billing or access.

Used by `llm.structured` and by the chat assistant. Billed per token by Anthropic
(a Claude subscription doesn't cover it): set a spending limit in the Anthropic console.
"""
from __future__ import annotations

import json
import os
from typing import TypeVar

from pydantic import BaseModel

from .. import config
from . import usage

S = TypeVar("S", bound=BaseModel)

# If Claude declines a request on safety grounds, Anthropic re-runs it on a fallback model itself.
FALLBACK_BETA = "server-side-fallback-2026-07-01"
MAX_TOKENS = 16_000


class ClaudeRefused(RuntimeError):
    pass


def api_key() -> str:
    key = os.environ.get("ANTHROPIC_API_KEY", "").strip().strip("\"'“”‘’").strip()
    return key.removeprefix("ANTHROPIC_API_KEY=").strip()


def available() -> bool:
    return bool(api_key())


def model() -> str:
    return config.ANTHROPIC_MODEL


def client():
    import anthropic
    return anthropic.Anthropic(api_key=api_key())


def structured(instructions: str, text: str, schema: type[S]) -> S:
    """Same contract as llm.structured: a filled instance of `schema` (structured output)."""
    response = client().beta.messages.parse(
        model=model(), max_tokens=MAX_TOKENS, system=instructions,
        messages=[{"role": "user", "content": text}], output_format=schema,
        betas=[FALLBACK_BETA], fallbacks="default")
    usage.record_anthropic(response)
    if response.stop_reason == "refusal":
        raise ClaudeRefused("Claude declined this request.")
    if response.parsed_output is None:
        raise ClaudeRefused(f"Claude returned no structured result (stop reason: {response.stop_reason}).")
    return response.parsed_output


STREAM_MAX_TOKENS = 64_000  # streaming: no HTTP timeout to fear, room for long answers
_NO_ADAPTIVE = ("claude-3", "claude-haiku-4-5", "claude-sonnet-4-5", "claude-opus-4-5", "claude-opus-4-1")


def _thinking() -> dict | None:
    """Adaptive thinking with readable summaries (shown live and recorded); older models don't take it."""
    return None if model().startswith(_NO_ADAPTIVE) else {"type": "adaptive", "display": "summarized"}


def chat(system: str, tools: list[dict], messages: list, sink=None):
    """One streamed round-trip of the assistant loop (the caller runs the tools and loops).

    `sink` (optional) is told as it happens: delta(kind, key, text) for each text/thinking piece,
    block_done(kind, key, full_text) when a block ends, tool_call(id, name, arguments_json) per tool call."""
    extra = {"thinking": thinking} if (thinking := _thinking()) else {}
    with client().beta.messages.stream(
            model=model(), max_tokens=STREAM_MAX_TOKENS, system=system, tools=tools, messages=messages,
            output_config={"effort": config.ANTHROPIC_EFFORT},
            cache_control={"type": "ephemeral"},  # the tool list and history repeat on every step
            betas=[FALLBACK_BETA], fallbacks="default", **extra) as stream:
        for event in stream:
            if sink is None:
                continue
            if event.type == "content_block_delta":
                delta = event.delta
                if delta.type == "text_delta":
                    sink.delta("text", event.index, delta.text)
                elif delta.type == "thinking_delta":
                    sink.delta("thinking", event.index, delta.thinking)
            elif event.type == "content_block_stop":
                block = event.content_block
                if block.type == "text":
                    sink.block_done("text", event.index, block.text)
                elif block.type == "thinking":
                    sink.block_done("thinking", event.index, block.thinking)
                elif block.type == "tool_use":
                    sink.tool_call(block.id, block.name, json.dumps(block.input))
        response = stream.get_final_message()
    usage.record_anthropic(response)
    return response
