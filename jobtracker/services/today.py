"""Today: what to do now, in one call (also what the app keeps for offline reading).

- next interviews (the coming 14 days) with their prep status and readiness, and the one to train for first;
- applications to apply to, follow-ups due and "probably no answer" (see rules.py for the rules);
- interviews of the last days to debrief;
- recordings waiting for you (pick your voice) and what came back recently (preps, debriefs);
- practice: what to review first and the capability furthest from its target;
- study sessions planned today and next, and checks suggested for new notions (services/learning.py);
- the week in numbers.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta

from .. import maintenance
from . import calendar, companies, learning, mail, practice, prep, profile, recordings, rules

RECENT_DAYS = 3


def _prep_state(company_id: str) -> dict:
    versions = prep.versions(company_id)
    done = [p for p in versions if p.status == "done"]
    last = versions[-1] if versions else None
    return {"status": last.status if last else "none", "ready": bool(done),
            "finished_at": done[-1].finished_at if done else None,
            "exercises": len(done[-1].result.exercises) if done and done[-1].result else 0}


def _goals(applied: int, answers: int) -> dict | None:
    """This week against the profile's weekly goals (None when no goal is set). Weeks start on Monday."""
    g = profile.settings().goals
    if not (g.applications or g.practice_answers):
        return None
    left = 6 - date.today().weekday()  # days left after today, Sunday = 0
    return {"days_left": left,
            "applications": {"done": applied, "goal": g.applications} if g.applications else None,
            "practice_answers": {"done": answers, "goal": g.practice_answers} if g.practice_answers else None}


def summary(days_ahead: int = 14) -> dict:
    today = date.today()
    horizon = (today + timedelta(days=days_ahead)).isoformat()
    apps = companies.list_companies()
    attempts = practice.store.load().attempts
    training = practice.profile()

    interviews = []
    for c, n in calendar.upcoming(days_back=0):
        if n.date[:10] > horizon:
            continue
        scores = [a.score for a in attempts if a.company_id == c.id]
        done = [p for p in prep.versions(c.id) if p.status == "done" and p.result]
        exercises = done[-1].result.exercises if done else []
        interviews.append({
            "company_id": c.id, "company": c.name, "position": c.position, "step_id": n.id, "step": n.name,
            "logo_file": c.logo_file, "logo_checked_at": c.logo_checked_at,
            "date": n.date[:10], "time": n.time, "duration_min": n.duration_min, "location": n.location,
            "days": (date.fromisoformat(n.date[:10]) - today).days, "prep": _prep_state(c.id),
            "practice_answers": len(scores), "practice_average": round(sum(scores) / len(scores), 1) if scores else None,
            "type": n.type(), "readiness": rules.readiness(c.id, attempts, exercises, training["needs_work"]),
        })
    silence = rules.nudges(apps, today)

    since = (datetime.now() - timedelta(days=RECENT_DAYS)).isoformat(timespec="seconds")
    names = {c.id: c.name for c in apps}
    waiting, recent = [], []
    for r in recordings.store.load().recordings:
        if r.company_id not in names:
            continue
        item = {"company_id": r.company_id, "company": names[r.company_id], "recording_id": r.id,
                "step": r.step_name, "at": r.uploaded_at}
        if r.status in ("choose_speaker", "uploaded"):
            waiting.append({**item, "what": "Pick your voice" if r.status == "choose_speaker" else "Ready to transcribe"})
        elif r.status == "done" and r.uploaded_at >= since and r.debrief:
            qs = r.debrief.questions
            recent.append({**item, "what": "Debrief ready", "detail": f"{len(qs)} questions"
                           + (f", average {sum(q.score for q in qs) / len(qs):.1f}/10" if qs else "")})
    for p in prep.store.load().preps:
        if p.status == "done" and (p.finished_at or "") >= since and p.company_id in names:
            recent.append({"company_id": p.company_id, "company": names[p.company_id], "what": "Interview prep ready",
                           "detail": f"{len(p.result.exercises)} exercises" if p.result else "", "at": p.finished_at})
        elif p.status == "running" and p.company_id in names:
            waiting.append({"company_id": p.company_id, "company": names[p.company_id], "what": "Prep being written",
                            "at": p.requested_at})
    recent.sort(key=lambda r: r["at"] or "", reverse=True)

    week_start = (today - timedelta(days=today.weekday())).isoformat()
    applied_this_week = sum(1 for c in apps for n in c.process.nodes
                            if n.state == "done" and n.type() == "applied" and (n.date or "")[:10] >= week_start)
    return {
        "date": today.isoformat(),
        "me": {k: v for k, v in profile.get().items() if k in ("greeting", "linkedin")},
        "server": maintenance.warnings(),   # owner only: backup missing/failed/old, unreadable data
        "interviews": interviews,
        "train_first": rules.train_first(interviews),
        "to_apply": rules.to_apply(apps, today),
        "follow_ups": silence["follow_ups"],
        "no_answer": silence["no_answer"],
        "debriefs": rules.debriefs(apps, {r.step for r in recordings.store.load().recordings if r.step}, today),
        "waiting": waiting,
        "mail": mail.inbox(include_done=0)["proposed"],   # news found in the user's email, to apply or dismiss
        "recent": recent[:6],
        "practice": {
            "to_review": training["to_review"][:3],
            "needs_work": training["needs_work"],
            "answers_this_week": sum(1 for a in attempts if a.at[:10] >= week_start),
        },
        "study": learning.agenda(days=7),
        "goals": _goals(applied_this_week, sum(1 for a in attempts if a.at[:10] >= week_start)),
        "week": {"applied": applied_this_week, "interviews_next_7_days": sum(1 for i in interviews if i["days"] <= 7),
                 "active": sum(1 for c in apps if c.status in ("Applied", "In process", "Offer"))},
    }
