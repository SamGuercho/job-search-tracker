"""LLM access, isolated here so the provider can be swapped (OpenAI today).

`structured(instructions, text, schema)` returns an instance of the pydantic
`schema`, filled by the model with schema-constrained (structured) output.
"""
from __future__ import annotations

import os
from typing import TypeVar

from pydantic import BaseModel

from .. import config

S = TypeVar("S", bound=BaseModel)


class LLMUnavailable(RuntimeError):
    pass


def api_key() -> str:
    """The key as configured, cleaned of what copy-pasting adds: spaces, quotes, Windows line endings
    (Notepad saves "\r\n" and Docker keeps the "\r"), and an accidental "OPENAI_API_KEY=" prefix."""
    key = os.environ.get("OPENAI_API_KEY", "").strip().strip("\"'\u201c\u201d\u2018\u2019").strip()
    return key.removeprefix("OPENAI_API_KEY=").strip()


def available() -> bool:
    return bool(api_key())


def client():
    """OpenAI client using the cleaned key."""
    from openai import OpenAI
    return OpenAI(api_key=api_key())


def structured(instructions: str, text: str, schema: type[S]) -> S:
    if not available():
        raise LLMUnavailable("OPENAI_API_KEY is not set: add it to the .env file and restart the server.")
    from openai import OpenAIError

    try:
        response = client().responses.parse(
            model=config.OPENAI_MODEL, instructions=instructions, input=text, text_format=schema)
    except OpenAIError as e:
        raise LLMUnavailable(f"OpenAI error: {e}") from e
    if response.output_parsed is None:
        raise LLMUnavailable("The model returned no structured result (refusal or truncated output).")
    return response.output_parsed
