"""Writing guides: the rules the app's AI follows for one kind of text (cv.md: titles, summaries, achievements).

One source of truth, included in every prompt that writes that kind of text (services/summary.py,
services/wording.py) and summed up in the assistant's instructions. Part of the assistant's prompt_version, so a
change to a guide shows in the monitor like any prompt change.
"""
from __future__ import annotations

from functools import cache
from pathlib import Path

HERE = Path(__file__).parent


@cache
def read(name: str) -> str:
    return (HERE / f"{name}.md").read_text(encoding="utf-8").strip()


def all_text() -> str:
    """Every guide, for the prompt version hash."""
    return "\n\n".join(read(p.stem) for p in sorted(HERE.glob("*.md")))
