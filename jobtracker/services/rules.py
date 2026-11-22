"""The coaching rules behind Today (and the follow-up notification).

- To apply: every application not applied yet, oldest first. Open positions fill fast: the age turns amber
  after APPLY_AMBER days and red after APPLY_RED; after APPLY_STALE days, "still interested?". A referral
  multiplies the chances: with a referee, ask them (then remind them after REFERRAL_REMIND days); without one,
  look for one on LinkedIn before applying.
- Silence depends on the stage (usual hiring timing):
    Applied, no reply: follow up after APPLIED_FOLLOW_UP days when there's someone to write to (a contact or a
    referee); "probably no answer" APPLIED_NO_ANSWER days after applying (most replies come within 2 weeks).
    After an interview: follow up after INTERVIEW_FOLLOW_UP days (5 working days) without news; "probably no
    answer" after INTERVIEW_NO_ANSWER days without news since your follow-up.
  Nothing is ever closed automatically: the user closes it as "No answer" in one tap (reopening is one tap too),
  or snoozes the nudge for SNOOZE_DAYS. An application with a step scheduled ahead is not waiting for news.
- Interview readiness (0-100): the share of the prep's exercises practised times their average last score, so
  practising everything at 8/10 gives 80. "Train first" is the interview with the most readiness missing per day
  left.
- Debrief: an interview 1 to 3 days ago with no notes and no recording yet: "how did it go?".
"""
from __future__ import annotations

from datetime import date, timedelta
from statistics import mean

from ..models import Company

APPLY_AMBER, APPLY_RED, APPLY_STALE = 3, 7, 21
APPLIED_FOLLOW_UP, APPLIED_NO_ANSWER = 7, 30
INTERVIEW_FOLLOW_UP, INTERVIEW_NO_ANSWER = 7, 21
SNOOZE_DAYS = 14
REFERRAL_REMIND = 3            # days after asking the referee, while you haven't applied
READY = 80                     # readiness from which an interview no longer needs "train first"
GOOD_SCORE = 7
INTERVIEW_TYPES = {"hr", "technical", "interview"}
NO_ANSWER = "No answer"        # process.close reason (analytics counts it apart from rejections)
NOT_INTERESTED = "Not interested anymore"


def _since(iso: str | None, today: date) -> int | None:
    try:
        return (today - date.fromisoformat((iso or "")[:10])).days
    except ValueError:
        return None


def snoozed(c: Company, today: date | None = None) -> bool:
    return bool(c.snooze_until) and c.snooze_until >= (today or date.today()).isoformat()


def has_contact(c: Company) -> bool:
    """Someone to write a follow-up to."""
    return bool(c.contact.name or c.contact.email or c.contact.linkedin or c.referee.strip())


def applied_step(c: Company):
    """The step that means "applied" (to mark it done in one tap): the first applied-type step, else the first."""
    order = c.process.order()
    return next((n for n in order if n.type() == "applied"), order[0] if order else None)


def _applied_on(c: Company) -> str | None:
    done = [n for n in c.process.order() if n.state == "done" and n.type() == "applied" and n.date]
    return done[0].date if done else None


def _scheduled_ahead(c: Company, today: date) -> bool:
    return any(n.state == "planned" and (n.date or "")[:10] >= today.isoformat() for n in c.process.steps)


def _base(c: Company) -> dict:
    return {"company_id": c.id, "company": c.name, "position": c.position, "logo_file": c.logo_file,
            "logo_checked_at": c.logo_checked_at}


# --------------------------------------------------------------------- to apply

def to_apply(apps: list[Company], today: date | None = None) -> list[dict]:
    today = today or date.today()
    out = []
    for c in apps:
        if c.status != "Not applied":
            continue
        age = _since(c.created_at, today) or 0
        step = applied_step(c)
        asked = _since(c.referral_asked_at, today) if c.referral_asked_at else None
        referral = ("none" if not c.referee.strip() else "to_ask" if asked is None
                    else "remind" if asked >= REFERRAL_REMIND else "asked")
        out.append({**_base(c), "age": age, "referee": c.referee.strip(), "referral": referral, "asked_days": asked,
                    "level": "red" if age >= APPLY_RED else "amber" if age >= APPLY_AMBER else "new",
                    "stale": age >= APPLY_STALE and not snoozed(c, today),
                    "cv_ready": bool(c.documents), "applied_step_id": step.id if step else None,
                    "job_url": c.job_url})
    return sorted(out, key=lambda x: -x["age"])


# ---------------------------------------------------------------- silence rules

def nudges(apps: list[Company], today: date | None = None) -> dict:
    """{"follow_ups": [...], "no_answer": [...]}, longest silence first."""
    today = today or date.today()
    follow_ups, no_answer = [], []
    for c in apps:
        if c.status not in ("Applied", "In process") or snoozed(c, today) or _scheduled_ahead(c, today):
            continue
        quiet = c.days_since_update() or 0
        item = {**_base(c), "stage": c.process.stage(), "days": quiet, "last_update_by": c.last_update_by,
                "contact": c.contact.name or c.referee}
        if c.status == "Applied":
            since_applied = _since(_applied_on(c) or c.last_update, today) or 0
            if since_applied >= APPLIED_NO_ANSWER and quiet >= APPLIED_FOLLOW_UP:
                no_answer.append({**item, "days": since_applied, "why": f"No reply {since_applied} days after applying"})
            elif quiet >= APPLIED_FOLLOW_UP and has_contact(c):
                follow_ups.append({**item, "why": f"Applied, no reply for {quiet} days"
                                   + (f": a short note to {item['contact']}" if item["contact"] else "")})
        else:
            if c.last_update_by == "me" and quiet >= INTERVIEW_NO_ANSWER:
                no_answer.append({**item, "why": f"No news {quiet} days after your follow-up"})
            elif quiet >= INTERVIEW_FOLLOW_UP:
                follow_ups.append({**item, "why": f"No news for {quiet} days since {item['stage']}"})
    key = lambda x: -x["days"]  # noqa: E731
    return {"follow_ups": sorted(follow_ups, key=key), "no_answer": sorted(no_answer, key=key)}


# -------------------------------------------------------------------- readiness

def readiness(company_id: str, attempts: list, exercises: list, needs_work: list[str] | None = None) -> dict:
    """How ready you are for this application's interview, from its prep's exercises and your answers."""
    last: dict[str, int] = {}
    for a in sorted((a for a in attempts if a.company_id == company_id), key=lambda a: a.at):
        last[a.exercise] = a.score
    if exercises:
        titles = [e.title for e in exercises]
        practised = [t for t in titles if t in last]
        avg = mean(last[t] for t in practised) if practised else None
        score = round(100 * len(practised) / len(titles) * (avg or 0) / 10)
        total = len(titles)
        caps = {e.capability for e in exercises if e.capability}
        weak = [c for c in (needs_work or []) if c in caps]
        weak += [e.capability or e.topic for e in exercises
                 if e.title in last and last[e.title] < GOOD_SCORE and (e.capability or e.topic) not in weak]
    else:  # no prep: what was practised for this company, up to 5 answers
        practised = list(last)
        avg = mean(last.values()) if last else None
        score = round(100 * min(len(practised), 5) / 5 * (avg or 0) / 10)
        total, weak = None, []
    return {"score": score, "practised": len(practised), "total": total,
            "average": round(avg, 1) if avg is not None else None, "weak": weak[:2],
            "level": "good" if score >= READY else "mid" if score >= 40 else "low"}


def train_first(interviews: list[dict]) -> dict | None:
    """The interview to train for first: the most readiness missing per day left (only those not ready yet)."""
    todo = [i for i in interviews if i["readiness"]["score"] < READY]
    if not todo:
        return None
    best = max(todo, key=lambda i: ((100 - i["readiness"]["score"]) / (max(i["days"], 0) + 1), -i["days"]))
    return {k: best[k] for k in ("company_id", "company", "step", "days", "readiness", "prep")}


# ----------------------------------------------------------------------- debrief

def debriefs(apps: list[Company], recorded_steps: set[str], today: date | None = None) -> list[dict]:
    """Interviews 1 to 3 days ago with no notes and no recording: "how did it go?"."""
    today = today or date.today()
    out = []
    for c in apps:
        for n in c.process.steps:
            ago = _since(n.date, today)
            if (n.type() in INTERVIEW_TYPES and ago is not None and 1 <= ago <= 3
                    and not n.notes.strip() and n.id not in recorded_steps):
                out.append({**_base(c), "step": n.name, "step_id": n.id, "date": n.date[:10], "days_ago": ago,
                            "state": n.state})
    return sorted(out, key=lambda x: x["days_ago"])


def snooze_until(days: int = SNOOZE_DAYS, today: date | None = None) -> str:
    return ((today or date.today()) + timedelta(days=days)).isoformat()
