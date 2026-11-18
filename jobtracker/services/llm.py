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


def available() -> bool:
    return bool(os.environ.get("OPENAI_API_KEY"))


def structured(instructions: str, text: str, schema: type[S]) -> S:
    if not available():
        raise LLMUnavailable("OPENAI_API_KEY is not set: add it to the .env file and restart the server.")
    from openai import OpenAI, OpenAIError

    try:
        response = OpenAI().responses.parse(
            model=config.OPENAI_MODEL, instructions=instructions, input=text, text_format=schema)
    except OpenAIError as e:
        raise LLMUnavailable(f"OpenAI error: {e}") from e
    if response.output_parsed is None:
        raise LLMUnavailable("The model returned no structured result (refusal or truncated output).")
    return response.output_parsed
