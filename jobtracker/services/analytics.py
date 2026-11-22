"""Job search analytics, computed from the applications' process graphs.

A step counts when it is done; its type (applied, response, hr, technical,
interview, reference, offer) comes from Node.type(). Timing metrics only use
steps that have a date, and say how many applications they are based on.
"""
from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from statistics import median

from ..models import Company
from . import companies

FUNNEL = [("applied", "Applied"), ("response", "Got a response"), ("interview", "Interviewed"),
          ("technical", "Technical interview"), ("offer", "Offer")]
INTERVIEW_TYPES = {"hr", "technical", "interview"}


def _d(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _facts(c: Company) -> dict:
    """What happened in one application."""
    order = c.process.order()
    done = [n for n in order if n.state == "done"]
    types = {n.type() for n in done}
    applied = bool(done)  # any done step means the application went out
    applied_date = next((_d(n.date) for n in done if n.type() == "applied" and n.date), None) \
        or (_d(done[0].date) if done else None)
    after_applied = [n for n in done if n.type() != "applied"]
    response_dates = [d for n in after_applied if (d := _d(n.date))]
    offer_dates = [d for n in done if n.type() == "offer" and (d := _d(n.date))]
    closed = c.process.closed.state == "done"
    return {
        "company": c, "applied": applied, "applied_date": applied_date,
        "response": bool(after_applied), "response_date": min(response_dates) if response_dates else None,
        "interview": bool(types & INTERVIEW_TYPES), "technical": "technical" in types, "offer": "offer" in types,
        "offer_date": min(offer_dates) if offer_dates else None,
        "interviews": sum(n.type() in INTERVIEW_TYPES for n in done),
        "technical_interviews": sum(n.type() == "technical" for n in done),
        "closed": closed, "closed_date": _d(c.process.closed.date),
        "no_answer": closed and c.process.closed.notes.strip().lower() == "no answer",
        "stopped_at": (done[-1].name if done else "Before applying") if closed else None,
        "stopped_type": (done[-1].type() if done else "not applied") if closed else None,
    }


def _rate(part: int, whole: int) -> float | None:
    return round(100 * part / whole, 1) if whole else None


def _days(pairs: list[tuple[date, date]]) -> dict:
    values = [(b - a).days for a, b in pairs if a and b and b >= a]
    return {"median": median(values) if values else None, "average": round(sum(values) / len(values), 1) if values else None,
            "n": len(values)}


def summary() -> dict:
    apps = [_facts(c) for c in companies.list_companies()]
    applied = [a for a in apps if a["applied"]]
    count = lambda key, pool=applied: sum(bool(a[key]) for a in pool)

    funnel = [{"key": k, "label": label, "count": count(k)} for k, label in FUNNEL]
    for step, prev in zip(funnel[1:], funnel, strict=False):
        step["from_previous"] = _rate(step["count"], prev["count"])

    start = min((a["applied_date"] for a in applied if a["applied_date"]), default=None)
    first_offer = min((a["offer_date"] for a in applied if a["offer_date"]), default=None)

    # Every week from the first application to this week, including empty ones (an even time axis)
    week_of = lambda d: f"{d.isocalendar()[0]}-W{d.isocalendar()[1]:02d}"
    weeks = Counter(week_of(a["applied_date"]) for a in applied if a["applied_date"])
    if start:
        day = start - timedelta(days=start.weekday())
        while day <= date.today():
            weeks.setdefault(week_of(day), 0)
            day += timedelta(days=7)

    def breakdown(attr: str, empty: str) -> list[dict]:
        groups = defaultdict(list)
        for a in applied:
            groups[getattr(a["company"], attr) or empty].append(a)
        return sorted(({"group": g, "applied": len(v), "responses": count("response", v),
                        "interviews": count("interview", v), "offers": count("offer", v),
                        "response_rate": _rate(count("response", v), len(v))} for g, v in groups.items()),
                      key=lambda r: -r["applied"])

    closed = [a for a in apps if a["closed"]]
    return {
        "today": date.today().isoformat(),
        "totals": {
            "applications": len(apps), "not_applied": len(apps) - len(applied), "applied": len(applied),
            "active": sum(a["applied"] and not a["closed"] and not a["offer"] for a in apps),
            "responses": count("response"), "interviews": sum(a["interviews"] for a in applied),
            "technical_interviews": sum(a["technical_interviews"] for a in applied),
            "offers": count("offer"), "closed": len(closed),
            "no_answer": sum(a["no_answer"] for a in closed),  # closed as "No answer": silence, not a rejection
        },
        "rates": {
            "response_rate": _rate(count("response"), len(applied)),
            "interview_rate": _rate(count("interview"), len(applied)),
            "offer_rate": _rate(count("offer"), len(applied)),
            "closed_without_offer": _rate(sum(not a["offer"] for a in closed), len(applied)),
        },
        "funnel": funnel,
        "stopped_at": [{"stage": s, "count": n} for s, n in
                       Counter(a["stopped_at"] for a in closed).most_common()],
        "timing": {
            "search_started": start.isoformat() if start else None,
            "days_searching": (date.today() - start).days if start else None,
            "days_to_first_offer": (first_offer - start).days if start and first_offer else None,
            "days_to_response": _days([(a["applied_date"], a["response_date"]) for a in applied]),
            "days_to_offer": _days([(a["applied_date"], a["offer_date"]) for a in applied]),
            "days_to_close": _days([(a["applied_date"], a["closed_date"]) for a in closed if a["applied"]]),
        },
        "weekly_applications": [{"week": w, "count": n} for w, n in sorted(weeks.items())],
        "by_source": breakdown("source_type", "Unknown source"),
        "by_work_model": breakdown("work_model", "Unknown"),
        "applications": [{
            "id": a["company"].id, "name": a["company"].name, "position": a["company"].position,
            "status": a["company"].status, "stage": a["company"].process.stage(),
            "applied_date": a["applied_date"].isoformat() if a["applied_date"] else None,
            "days_to_response": (a["response_date"] - a["applied_date"]).days
            if a["applied_date"] and a["response_date"] else None,
            "interviews": a["interviews"], "stopped_at": a["stopped_at"],
        } for a in apps],
    }
