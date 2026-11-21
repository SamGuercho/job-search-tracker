"""List prices used to turn measured usage into costs (USD). Provider bills are the reference.

Token prices are per 1M tokens; audio prices per minute. Checked on the providers' pricing pages on 2026-11-21:
OpenAI https://developers.openai.com/api/docs/pricing , Anthropic https://claude.com/pricing .
Override or add models without a code change: JOBTRACKER_PRICES=/path/to/prices.json with the same shape,
e.g. {"openai/gpt-5.4": {"input": 2.5, "cached_input": 0.25, "output": 15}}.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

CHECKED_ON = "2026-11-21"

PRICES: dict[str, dict[str, float]] = {
    # OpenAI text (Responses API, standard tier)
    "openai/gpt-5.4-mini": {"input": 0.75, "cached_input": 0.075, "output": 4.50},
    # OpenAI transcription (estimated per minute of audio, as listed by OpenAI)
    "openai/gpt-4o-transcribe-diarize": {"per_minute": 0.006},
    "openai/gpt-transcribe": {"per_minute": 0.0045},
    "openai/gpt-4o-transcribe": {"per_minute": 0.006},
    "openai/gpt-4o-mini-transcribe": {"per_minute": 0.003},
    # Anthropic (cache writes are 1.25x input for the 5-minute cache)
    "anthropic/claude-sonnet-5-5": {"input": 2.00, "cached_input": 0.10, "cache_write": 2.50, "output": 10.00},
    "anthropic/claude-sonnet-5": {"input": 2.00, "cached_input": 0.20, "cache_write": 2.50, "output": 10.00},
    "anthropic/claude-opus-5-5": {"input": 4.00, "cached_input": 0.20, "cache_write": 5.00, "output": 20.00},
    # (Haiku cache prices: the usual 0.1x read / 1.25x write ratios)
    "anthropic/claude-haiku-5-5": {"input": 0.10, "cached_input": 0.01, "cache_write": 0.125, "output": 0.50},
}


def _load_overrides() -> None:
    path = os.environ.get("JOBTRACKER_PRICES", "").strip()
    if path and Path(path).expanduser().exists():
        PRICES.update(json.loads(Path(path).expanduser().read_text(encoding="utf-8")))


_load_overrides()


def price(provider: str, model: str) -> dict[str, float] | None:
    """The price entry for a model; dated variants ("gpt-5.4-mini-2026-03-17") use their base model's."""
    key = f"{provider}/{model}"
    if key in PRICES:
        return PRICES[key]
    return next((p for k, p in sorted(PRICES.items(), key=lambda kv: -len(kv[0])) if key.startswith(k + "-")), None)


def token_cost(provider: str, model: str, input_tokens: int = 0, output_tokens: int = 0, cached_tokens: int = 0,
               cache_write_tokens: int = 0) -> float | None:
    """Cost of a text call. `input_tokens` excludes cached and cache-write tokens. None if the model is unknown."""
    p = price(provider, model)
    if not p or "input" not in p:
        return None
    return (input_tokens * p["input"] + cached_tokens * p.get("cached_input", p["input"])
            + cache_write_tokens * p.get("cache_write", p["input"]) + output_tokens * p["output"]) / 1_000_000


def audio_cost(provider: str, model: str, seconds: float) -> float | None:
    p = price(provider, model)
    return round(seconds / 60 * p["per_minute"], 6) if p and "per_minute" in p else None
