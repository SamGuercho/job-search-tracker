"""What can go wrong in a turn: the categories of a flag, shared by the app (thumbs down, detectors) and the monitor.

A flag points at a turn, and optionally at one span of it (an LLM call or a tool call). Where it comes from:
  user      a thumbs down in the chat (one flag per reason given)
  owner     set by hand in the monitor
  detector  found automatically when a turn ends (detect.py): "suggested" until the owner confirms or dismisses it
Status: suggested → open → in_case (part of an eval case) → fixed → archived; or dismissed.
"""
from __future__ import annotations

# (id, group, label, what it applies to)
CATEGORIES: list[tuple[str, str, str, tuple[str, ...]]] = [
    ("wrong_intent", "Understanding", "Misread what was asked", ("turn", "llm_call")),
    ("incomplete_task", "Understanding", "Didn't complete the task", ("turn", "llm_call")),
    ("ignored_instruction", "Understanding", "Ignored an instruction", ("turn", "llm_call")),
    ("unfaithful", "Correctness", "Unfaithful or made up", ("turn", "llm_call")),
    ("wrong_data_changed", "Correctness", "Changed the wrong data", ("turn", "tool")),
    ("wrong_argument", "Tools", "Wrong argument", ("tool",)),
    ("wrong_tool", "Tools", "Wrong tool", ("tool",)),
    ("missing_tool_call", "Tools", "A tool call was missing", ("turn", "llm_call")),
    ("redundant_call", "Tools", "Redundant call", ("tool",)),
    ("tool_failed", "Tools", "Didn't do as planned", ("tool",)),
    ("wrong_style", "Response", "Wrong style or tone", ("turn", "llm_call")),
    ("wrong_level", "Response", "Wrong level of language", ("turn", "llm_call")),
    ("out_of_character", "Response", "Out of character", ("turn", "llm_call")),
    ("wrong_length", "Response", "Too long or too short", ("turn", "llm_call")),
    ("out_of_domain", "Response", "Out of domain", ("turn", "llm_call")),
    ("too_costly", "Cost & speed", "Too costly", ("turn", "llm_call")),
    ("too_slow", "Cost & speed", "Too slow", ("turn", "llm_call", "tool")),
    ("other", "Other", "Other", ("turn", "llm_call", "tool")),
]
CATEGORY_IDS = {c[0] for c in CATEGORIES}

# Thumbs-down reasons offered in the chat (id: label shown to the user, category)
USER_REASONS: dict[str, tuple[str, str]] = {
    "misunderstood": ("Misunderstood me", "wrong_intent"),
    "unfinished": ("Didn't finish", "incomplete_task"),
    "wrong": ("Wrong or made up", "unfaithful"),
    "wrong_change": ("Changed the wrong thing", "wrong_data_changed"),
    "tone": ("Tone or style", "wrong_style"),
    "slow": ("Too slow", "too_slow"),
    "other": ("Something else", "other"),
}

SOURCES = ("user", "owner", "detector")
STATUSES = ("suggested", "open", "in_case", "fixed", "archived", "dismissed")
QUEUE = ("open", "in_case")   # what still needs work


def catalog() -> dict:
    """For the monitor and the chat: categories, user reasons, statuses."""
    return {"categories": [{"id": i, "group": g, "label": label, "levels": list(levels)}
                           for i, g, label, levels in CATEGORIES],
            "user_reasons": [{"id": k, "label": v[0], "category": v[1]} for k, v in USER_REASONS.items()],
            "statuses": list(STATUSES)}
