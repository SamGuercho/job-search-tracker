"""The person using the app: how to greet them, their LinkedIn, and what they're looking for.

First and last name and LinkedIn are the master CV's (one place to edit them, on the CV header too); everything else lives in
profile.json: the greeting name, target roles, places and work models (jobs outside them get a warning, see
fit()), salary expectation (the default for new applications), notice period and start date, the default
language of interview preps, weekly goals (shown on Today), and working and study hours (study sessions are
planned in them, services/learning.py). The assistant gets a short summary (context()).
"""
from __future__ import annotations

from ..models import StudyHours, UserProfile, WeeklyGoals
from ..storage import UserDocument
from . import master_cv

store = UserDocument("profile.json", UserProfile)
LIST_FIELDS = ("target_roles", "locations", "work_models")


def settings() -> UserProfile:
    return store.load()


def get() -> dict:
    cv = master_cv.get_master().profile
    mine = store.load()
    greeting = mine.preferred_name.strip() or cv.first_name
    return {**mine.model_dump(), "name": cv.name, "first_name": cv.first_name, "last_name": cv.last_name,
            "greeting": greeting, "linkedin": cv.linkedin, "email": cv.email}


def _clean(value):
    if isinstance(value, str):
        return " ".join(value.split())
    if isinstance(value, list):
        out = []
        for v in (" ".join(str(x).split()) for x in value):
            if v and v.lower() not in {o.lower() for o in out}:
                out.append(v)
        return out
    return value


def update(name: str | None = None, linkedin: str | None = None, first_name: str | None = None,
           last_name: str | None = None, **fields) -> dict:
    """Change any of: first and last name (or a full name, split at the first space), linkedin (master CV),
    and the UserProfile fields (None = unchanged)."""
    given = {"name": name, "first_name": first_name, "last_name": last_name, "linkedin": linkedin}
    cv_changes = {k: _clean(v) for k, v in given.items() if v is not None}
    if cv_changes.get("linkedin") and not cv_changes["linkedin"].startswith(("http://", "https://")):
        cv_changes["linkedin"] = "https://" + cv_changes["linkedin"].lstrip("/")
    if cv_changes:
        master_cv.update_profile(**cv_changes)
    changes = {k: v for k, v in fields.items() if v is not None and k in UserProfile.model_fields}
    if changes:
        with store.transaction() as p:
            data = p.model_dump()
            for k, v in changes.items():
                if k == "goals":
                    given = v.model_dump(exclude_unset=True) if hasattr(v, "model_dump") else v  # only the goals sent
                    v = WeeklyGoals.model_validate({**data["goals"], **given})
                    v = v.model_copy(update={g: max(0, min(getattr(v, g), 100))
                                             for g in ("applications", "practice_answers")})
                elif k == "study":
                    given = v.model_dump(exclude_unset=True) if hasattr(v, "model_dump") else v
                    v = StudyHours.model_validate({**data["study"], **given})
                    v = v.model_copy(update={"max_per_day_min": max(15, min(v.max_per_day_min, 600)),
                                             "weekend_days": sorted({d for d in v.weekend_days if 0 <= d <= 6})})
                elif k == "max_commute_min":
                    v = max(0, int(v)) or None
                data[k] = v if k in ("goals", "study") else _clean(v)
            updated = UserProfile.model_validate(data)
            for k in UserProfile.model_fields:
                setattr(p, k, getattr(updated, k))
    return get()


# --------------------------------------------------------------------- job fit

def _words(text: str) -> set[str]:
    from .headlines import _words as words  # same word rules as title variants (seniority words ignored)
    return words(text)


def role_matches(position: str, roles: list[str]) -> bool:
    """The job title has most of the words of one target role ("Staff Data Scientist" ~ "Data Scientist")."""
    job = _words(position)
    for role in roles:
        r = _words(role)
        if r and len(r & job) / len(r) >= 0.6:
            return True
    return False


def fit(company, p: UserProfile | None = None) -> list[str]:
    """Why this job may not be what you're looking for (empty: fits, or nothing to compare)."""
    p = p or store.load()
    out = []
    if p.target_roles and company.position and not role_matches(company.position, p.target_roles):
        out.append(f"not one of your target roles ({', '.join(p.target_roles)})")
    if p.work_models and company.work_model and company.work_model not in p.work_models:
        out.append(f"{company.work_model}: you want {' or '.join(p.work_models)}")
    if p.locations and company.location and company.work_model != "Remote" and not any(
            loc.lower() in company.location.lower() or company.location.lower() in loc.lower() for loc in p.locations):
        out.append(f"{company.location}: outside your areas ({', '.join(p.locations)})")
    return out


def context() -> str:
    """A few lines about what the user is looking for, for the assistant ("" when nothing is set)."""
    p = store.load()
    parts = [
        p.target_roles and f"target roles: {', '.join(p.target_roles)}",
        p.seniority and f"seniority: {p.seniority}",
        p.locations and f"areas: {', '.join(p.locations)}",
        p.work_models and f"work models: {', '.join(p.work_models)}",
        p.max_commute_min and f"max commute: {p.max_commute_min} min",
        p.salary_expectation and f"salary expectation: {p.salary_expectation}",
        p.notice_period and f"notice period: {p.notice_period}",
        p.available_from and f"available from: {p.available_from}",
        p.interview_language != "English" and f"interview prep language: {p.interview_language}",
        (p.goals.applications or p.goals.practice_answers)
        and f"weekly goals: {p.goals.applications} applications, {p.goals.practice_answers} practice answers",
    ]
    return "; ".join(x for x in parts if x)
