"""Practice log and training profile: what the user practised, how they scored, and what to review.

The assistant records each graded answer (practice.record). The profile built from that log tells the user
(and the next prep) which exercises are done, which topics are strong or weak, and what to review first.
"""
from __future__ import annotations

from collections import defaultdict
from statistics import mean

from ..models import PracticeAttempt, PracticeBook
from ..storage import UserDocument
from . import companies, prep

store = UserDocument("practice.json", PracticeBook)
GOOD = 7  # a score below this (out of 10) means "to review"


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
           topic: str = "", kind: str = "") -> PracticeAttempt:
    """Log one graded answer. `exercise` is the prep exercise's number or title; an exercise that isn't in the
    prep (made up during practice) is logged too, with the topic/kind given."""
    c = companies.resolve(company)
    found = _find_exercise(c.id, exercise)
    attempt = PracticeAttempt(
        company_id=c.id, company=c.name, exercise=found.title if found else exercise.strip(),
        kind=found.kind if found else kind, topic=found.topic if found else topic,
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
    return {
        "totals": {"attempts": len(attempts), "exercises": len(exercises),
                   "average": round(mean(a.score for a in attempts), 1) if attempts else None},
        "to_review": to_review,
        "weak_topics": weak_topics,
        "topics": sorted(topics, key=lambda t: t["average"]),
        "exercises": sorted(exercises, key=lambda e: e["last_at"], reverse=True),
    }


def summary_for_prep(limit: int = 40) -> str:
    """The profile as text for a new prep's brief: what not to repeat, what to insist on."""
    p = profile()
    if not p["totals"]["attempts"]:
        return ""
    done = [f"- {e['exercise']} ({e['company']}, {e['topic'] or e['kind']}): best {e['best_score']}/10"
            for e in p["exercises"][:limit]]
    topics = [f"- {t['topic']}: average {t['average']}/10 over {t['attempts']} answers" for t in p["topics"]]
    review = [f"- {e['exercise']}: last {e['last_score']}/10, missed: {'; '.join(e['missed']) or 'n/a'}"
              for e in p["to_review"][:15]]
    return "\n".join(["Exercises already practised (do not repeat them):", *done,
                      "", "Scores by topic (weakest first):", *topics,
                      "", "To review (low scores, what was missed):", *(review or ["- nothing"])])
