"""The person using the app: how to greet them and their LinkedIn, for Today, referral messages and the assistant.

Name and LinkedIn are the master CV's (one place to edit them, on the CV header too); the preferred first name
is only for greetings ("Good morning, Sam") and lives in profile.json.
"""
from __future__ import annotations

from ..models import UserProfile
from ..storage import UserDocument
from . import master_cv

store = UserDocument("profile.json", UserProfile)


def get() -> dict:
    cv = master_cv.get_master().profile
    mine = store.load()
    first = mine.preferred_name.strip() or (cv.name.split() or [""])[0]
    return {"name": cv.name, "preferred_name": mine.preferred_name, "first_name": first,
            "linkedin": cv.linkedin, "email": cv.email}


def update(name: str | None = None, preferred_name: str | None = None, linkedin: str | None = None) -> dict:
    changes = {k: " ".join(v.split()) for k, v in {"name": name, "linkedin": linkedin}.items() if v is not None}
    if changes.get("linkedin") and not changes["linkedin"].startswith(("http://", "https://")):
        changes["linkedin"] = "https://" + changes["linkedin"].lstrip("/")
    if changes:
        master_cv.update_profile(**changes)
    if preferred_name is not None:
        with store.transaction() as p:
            p.preferred_name = " ".join(preferred_name.split())
    return get()
