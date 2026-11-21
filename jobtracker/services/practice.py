"""Practice log and training profile: what the user practised, how they scored, and what to review.

The assistant records each graded answer (practice.record). The profile built from that log tells the user
(and the next prep) which exercises are done, which topics are strong or weak, and what to review first.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from pydantic import BaseModel

from ..logs import log
from ..models import PracticeAttempt, PracticeBook
from ..storage import UserDocument
from . import companies, llm, prep

store = UserDocument("practice.json", PracticeBook)
GOOD = 7    # a score below this (out of 10) means "to review"
WINDOW = 3  # answers averaged for "level now" / "level at the start" and the moving average

# Broad capabilities progress is tracked on, with the words that point to each (matched in the exercise's
# capability, topic, kind and title; the longest match wins). New ones can appear (from Codex or the model).
CAPABILITIES: dict[str, list[str]] = {
    "SQL": ["sql", "query", "queries", "join", "window function", "cte", "aggregation", "group by"],
    "Python": ["python", "pandas", "numpy"],
    "Algorithms & coding": ["algorithm", "data structure", "coding", "leetcode", "complexity", "live coding"],
    "Statistics": ["statistic", "probability", "p-value", "hypothesis", "regression", "distribution",
                   "confidence interval", "bayes"],
    "A/B testing": ["a/b", "ab test", "experiment", "split test", "sample size"],
    "Product analytics": ["metric", "kpi", "funnel", "retention", "product sense", "dashboard", "case study",
                          "analytics case", "root cause"],
    "Data modeling": ["data model", "schema design", "dimensional", "star schema", "data modelling"],
    "Data engineering": ["pipeline", "etl", "elt", "warehouse", "spark", "airflow", "streaming", "dbt"],
    "Machine learning": ["machine learning", "model training", "classification", "feature engineering", "llm",
                         "nlp", "deep learning", "ml "],
    "System design": ["system design", "architecture", "scalab", "distributed"],
    "Behavioral": ["behavioral", "behavioural", "leadership", "conflict", "star method", "teamwork", "motivation"],
}


def known_capabilities() -> list[str]:
    """The default capabilities plus any the user already has (from answers or targets)."""
    book = store.load()
    seen = [a.capability for a in book.attempts if a.capability] + list(book.targets)
    return list(dict.fromkeys([*CAPABILITIES, *seen]))


def canonical(name: str, known: list[str] | None = None) -> str:
    """A known capability with the same name (any case), else the name as given."""
    name = " ".join(name.split())
    return next((k for k in (known or known_capabilities()) if k.lower() == name.lower()), name)


def guess(*texts: str) -> str:
    """The capability the words point to ("" if none): the longest keyword match wins."""
    text = " " + " ".join(t.lower() for t in texts if t) + " "
    best, length = "", 0
    for capability, words in CAPABILITIES.items():
        for word in [capability.lower(), *words]:
            if word in text and len(word) > length:
                best, length = capability, len(word)
    return best


class _Sorted(BaseModel):
    capabilities: list[str]  # one per exercise, same order


def _ask_model(items: list[str], known: list[str]) -> list[str]:
    """Cheap model: one broad capability per exercise description ("" when unavailable)."""
    if not items or not llm.available():
        return [""] * len(items)
    instructions = ("For each practice exercise below (one per line), give the broad capability it trains, "
                    f"reusing exactly one of these names when one fits: {', '.join(known)}. Only if none fits, "
                    "write a short new name (1 to 3 words). Return one capability per exercise, in order.")
    try:
        out = llm.structured(instructions, "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items)), _Sorted).capabilities
    except llm.LLMUnavailable as e:
        log.warning("Could not sort practice answers into capabilities: %s", e)
        return [""] * len(items)
    return [canonical(c, known) for c in out[:len(items)]] + [""] * (len(items) - len(out))


def assign(capability: str, topic: str, kind: str, title: str, known: list[str] | None = None) -> str:
    """The capability for one exercise: given (normalised), else guessed from its words, else the cheap model,
    else "Other"."""
    known = known or known_capabilities()
    if capability.strip():
        return canonical(capability, known)
    return guess(topic, kind, title) or (_ask_model([f"{title} ({kind}; {topic})"], known)[0] or "Other")


def classify() -> dict:
    """Give a capability to the answers that have none (logged before capabilities existed). Returns counts."""
    known = known_capabilities()
    attempts = store.load().attempts
    todo = [a for a in attempts if not a.capability]
    if not todo:
        return {"sorted": 0, "by_model": 0}
    guesses = {a.id: guess(a.topic, a.kind, a.exercise) for a in todo}
    unknown = [a for a in todo if not guesses[a.id]]
    answers = _ask_model([f"{a.exercise} ({a.kind}; {a.topic})" for a in unknown], known)  # same length
    for a, c in zip(unknown, answers, strict=True):
        guesses[a.id] = c or "Other"
    with store.transaction() as book:
        for a in book.attempts:
            if not a.capability and a.id in guesses:
                a.capability = guesses[a.id]
    return {"sorted": len(todo), "by_model": len(unknown)}


def set_target(capability: str, target: int | None) -> dict:
    """Set the score /10 to reach on a capability (None or 0 removes it)."""
    name = canonical(capability)
    if not name:
        raise ValueError("Give a capability name")
    with store.transaction() as book:
        if target:
            book.targets[name] = max(1, min(int(target), 10))
        else:
            book.targets.pop(name, None)
        return dict(book.targets)


def _find_exercise(company_ref: str, exercise: str):
    """An exercise of the application's latest prep, by number ("2") or title (exact, then partial match)."""
    p = prep.get(company_ref)
    exercises = p.result.exercises if p and p.result else []
    key = exercise.strip().lower().lstrip("#")
    if key.isdigit() and 1 <= int(key) <= len(exercises):
        return exercises[int(key) - 1]
    return next((e for e in exercises if e.title.lower() == key), None) or \
        next((e for e in exercises if key in e.title.lower() or e.title.lower() in key), None)


def record(company: str, exercise: str, score: int, missed: list[str] | None = None, feedback: str = "",
           topic: str = "", kind: str = "", capability: str = "") -> PracticeAttempt:
    """Log one graded answer. `exercise` is the prep exercise's number or title; an exercise that isn't in the
    prep (made up during practice) is logged too, with the topic/kind given."""
    c = companies.resolve(company)
    found = _find_exercise(c.id, exercise)
    title, kind = (found.title, found.kind) if found else (exercise.strip(), kind)
    topic = found.topic if found else topic
    attempt = PracticeAttempt(
        company_id=c.id, company=c.name, exercise=title, kind=kind, topic=topic,
        capability=assign(found.capability if found and found.capability else capability, topic, kind, title),
        difficulty=found.difficulty if found else "", score=max(0, min(int(score), 10)),
        missed=[m for m in (missed or []) if m.strip()], feedback=feedback.strip())
    with store.transaction() as book:
        book.attempts.append(attempt)
    return attempt


def history(company: str | None = None) -> list[PracticeAttempt]:
    """Attempts, newest first; only one application's if `company` is given."""
    attempts = store.load().attempts
    if company:
        company_id = companies.resolve(company).id
        attempts = [a for a in attempts if a.company_id == company_id]
    return sorted(reversed(attempts), key=lambda a: a.at, reverse=True)  # same second: last logged first


def profile() -> dict:
    """The training profile: totals, scores by topic, exercises done, and what to review (weakest first)."""
    attempts = sorted(store.load().attempts, key=lambda a: a.at)
    by_exercise: dict[tuple, list[PracticeAttempt]] = defaultdict(list)
    by_topic: dict[str, list[PracticeAttempt]] = defaultdict(list)
    for a in attempts:
        by_exercise[(a.company_id, a.exercise)].append(a)
        by_topic[(a.topic or a.kind or "other").strip()].append(a)

    exercises = []
    for (company_id, title), tries in by_exercise.items():
        last = tries[-1]
        exercises.append({
            "company_id": company_id, "company": last.company, "exercise": title, "topic": last.topic,
            "kind": last.kind, "difficulty": last.difficulty, "attempts": len(tries), "last_score": last.score,
            "best_score": max(t.score for t in tries), "last_at": last.at, "missed": last.missed})
    topics = []
    for topic, tries in by_topic.items():
        scores = [t.score for t in tries]
        topics.append({"topic": topic, "attempts": len(tries), "average": round(mean(scores), 1),
                       "last_score": scores[-1], "trend": scores[-1] - scores[0] if len(scores) > 1 else 0})
    to_review = sorted((e for e in exercises if e["last_score"] < GOOD),
                       key=lambda e: (e["last_score"], e["last_at"]))
    weak_topics = sorted((t for t in topics if t["average"] < GOOD), key=lambda t: t["average"])
    caps = capabilities(attempts)
    improving = [c for c in caps if c["change"] is not None and c["change"] > 0]
    return {
        "capabilities": caps,
        "most_improved": max(improving, key=lambda c: c["change"])["capability"] if improving else None,
        "needs_work": [c["capability"] for c in caps if c["now"] is not None and
                       (c["now"] < (c["target"] or GOOD))][:3],
        "totals": {"attempts": len(attempts), "exercises": len(exercises),
                   "average": round(mean(a.score for a in attempts), 1) if attempts else None},
        "to_review": to_review,
        "weak_topics": weak_topics,
        "topics": sorted(topics, key=lambda t: t["average"]),
        "exercises": sorted(exercises, key=lambda e: e["last_at"], reverse=True),
    }


def capabilities(attempts: list[PracticeAttempt] | None = None) -> list[dict]:
    """Progress per capability: every answer over time with a moving average, the level at the start and now
    (average of the first / last WINDOW answers), the change, the target and the gap to it. Furthest from
    its target (or weakest) first."""
    book = store.load()
    attempts = sorted(attempts if attempts is not None else book.attempts, key=lambda a: a.at)
    by_cap: dict[str, list[PracticeAttempt]] = defaultdict(list)
    for a in attempts:
        by_cap[a.capability or "Unsorted"].append(a)
    for name in book.targets:
        by_cap.setdefault(name, [])
    out = []
    for name, tries in by_cap.items():
        scores = [t.score for t in tries]
        now = round(mean(scores[-WINDOW:]), 1) if scores else None
        start = round(mean(scores[:WINDOW]), 1) if scores else None
        target = book.targets.get(name)
        out.append({
            "capability": name, "attempts": len(tries), "now": now, "start": start,
            "change": round(now - start, 1) if len(scores) > WINDOW else None,
            "best": max(scores) if scores else None, "last_at": tries[-1].at if tries else None,
            "target": target, "gap": round(target - now, 1) if target and now is not None else None,
            "points": [{"at": t.at, "score": t.score, "exercise": t.exercise,
                        "moving": round(mean(scores[max(0, i - WINDOW + 1):i + 1]), 2)}
                       for i, t in enumerate(tries)],
        })
    return sorted(out, key=lambda c: (-(c["gap"] if c["gap"] is not None else -10),
                                      c["now"] if c["now"] is not None else 99))


def summary_for_prep(limit: int = 40) -> str:
    """The profile as text for a new prep's brief: what not to repeat, what to insist on."""
    p = profile()
    if not p["totals"]["attempts"] and not p["capabilities"]:
        return ""
    levels = [f"- {c['capability']}: now {c['now'] if c['now'] is not None else 'not practised'}/10"
              + (f", target {c['target']}/10" if c["target"] else "") + f" ({c['attempts']} answers)"
              for c in p["capabilities"]]
    done = [f"- {e['exercise']} ({e['company']}, {e['topic'] or e['kind']}): best {e['best_score']}/10"
            for e in p["exercises"][:limit]]
    topics = [f"- {t['topic']}: average {t['average']}/10 over {t['attempts']} answers" for t in p["topics"]]
    review = [f"- {e['exercise']}: last {e['last_score']}/10, missed: {'; '.join(e['missed']) or 'n/a'}"
              for e in p["to_review"][:15]]
    return "\n".join(["Level per capability (furthest from target or weakest first; focus there):", *levels, "",
                      "Exercises already practised (do not repeat them):", *done,
                      "", "Scores by topic (weakest first):", *topics,
                      "", "To review (low scores, what was missed):", *(review or ["- nothing"])])
