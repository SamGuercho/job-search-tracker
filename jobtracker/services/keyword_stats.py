"""Which keywords matter most for this user: how often the jobs they applied to asked for them.

Each application counts once per keyword: the CV terms that cover its job's requirements (kept by cv_score as
`job_match`) and the keywords its CVs were made with. A keyword many applications ask for is strong. The CV builder
uses it to decide when a project's achievement is worth keeping (cv_builder: projects vs professional experience).
"""
from __future__ import annotations

from statistics import median

from . import companies, master_cv


def stats() -> list[dict]:
    """[{keyword, applications, required}] most asked first. `applications` = how many applications' jobs asked for
    it; `required` = how many of them required it; `in_cv` = written in the master CV; gaps (requirements nothing in
    the CV covers) are listed too, with in_cv false."""
    counts: dict[str, dict] = {}

    def add(term: str, required: bool, seen: set, in_cv: bool) -> None:
        key = term.strip().lower()
        if not key or key in seen:  # once per application (required mentions come first)
            return
        seen.add(key)
        row = counts.setdefault(key, {"keyword": term.strip(), "applications": 0, "required": 0, "in_cv": in_cv})
        row["applications"] += 1
        row["required"] += int(required)
        row["in_cv"] = row["in_cv"] or in_cv

    for c in companies.list_companies():
        seen: set[str] = set()
        reqs = c.job_match.requirements if c.job_match else []
        for r in sorted(reqs, key=lambda r: not r.required):  # a required mention counts as required
            for k in r.keywords:
                add(k, r.required, seen, True)
            if not r.keywords:
                add(r.term, r.required, seen, False)
        for k in c.keywords:
            add(k, False, seen, True)
    return sorted(counts.values(), key=lambda r: (-r["applications"], -r["required"], r["keyword"].lower()))


def importance() -> dict[str, int]:
    """keyword (lower case) -> number of applications that asked for it."""
    return {r["keyword"].lower(): r["applications"] for r in stats()}


def strong_keywords(history: dict[str, int] | None = None) -> set[str]:
    """Keywords (lower case) asked for by at least two applications and at least as often as the median keyword:
    the user's strong ones. Empty while there is no history."""
    history = importance() if history is None else history
    if not history:
        return set()
    bar = max(2, median(history.values()))
    return {k for k, n in history.items() if n >= bar}


def report(limit: int = 30) -> dict:
    """For the Master CV page and the assistant: the most asked keywords, which ones are strong, which the CV lacks."""
    rows = stats()
    strong = strong_keywords({r["keyword"].lower(): r["applications"] for r in rows})
    vocabulary = {k.lower() for k in master_cv.keyword_vocabulary()}
    for r in rows:
        r["strong"] = r["keyword"].lower() in strong
        r["in_cv"] = r["in_cv"] or r["keyword"].lower() in vocabulary
    applications = sum(1 for c in companies.list_companies() if c.keywords or c.job_match)
    return {"applications": applications, "keywords": rows[:limit],
            "missing": [r for r in rows if not r["in_cv"]][:limit]}
