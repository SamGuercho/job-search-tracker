"""Claude (Anthropic API): the fallback when OpenAI fails on quota, billing or access.

Used by `llm.structured` and by the chat assistant. Billed per token by Anthropic
(a Claude subscription doesn't cover it): set a spending limit in the Anthropic console.
"""
from __future__ import annotations

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


def chat(system: str, tools: list[dict], messages: list):
    """One round-trip of the assistant loop (the caller runs the tools and loops)."""
    response = client().beta.messages.create(
        model=model(), max_tokens=MAX_TOKENS, system=system, tools=tools, messages=messages,
        output_config={"effort": config.ANTHROPIC_EFFORT},
        cache_control={"type": "ephemeral"},  # the tool list and history repeat on every step
        betas=[FALLBACK_BETA], fallbacks="default")
    usage.record_anthropic(response)
    return response
