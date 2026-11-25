"""Text that reads as written by a person: no long dashes (— and –), the first sign of AI-written text.

Applied to every line written into a CV (cv_templates) and to CV text the app saves or proposes (summaries,
title variants, wordings), whoever wrote it: the user, an import, or a model.
"""
from __future__ import annotations

import re

_RANGE = re.compile(r"(\w)\s*[–—]\s*(?=\d|present\b|now\b|today\b|current\b)", re.IGNORECASE)  # 2019–2021, 5–10
_DASH = re.compile(r"\s*[–—]+\s*")


def plain_dashes(text: str) -> str:
    """"…enterprise software – from classical ML to LLMs" -> "…enterprise software, from classical ML to LLMs";
    ranges keep a plain hyphen ("2019 – 2021" -> "2019 - 2021", "5–10" -> "5-10")."""
    if not text or ("–" not in text and "—" not in text):
        return text
    out = _RANGE.sub(lambda m: m.group(1) + (" - " if " " in m.group(0) else "-"), text)
    out = _DASH.sub(", ", out)
    out = re.sub(r",\s*([,.;:!?)])", r"\1", out)       # "word, ." -> "word."
    out = re.sub(r"([(\[])\s*,\s*", r"\1", out)        # "(, x" -> "(x"
    return re.sub(r"^\s*,\s*|\s*,\s*$", "", out)        # no comma left at the start or the end
