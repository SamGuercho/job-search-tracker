"""Master CV: the single source of every bullet point you could put on a CV."""
from __future__ import annotations

import re

from .. import config
from ..models import Bullet, Education, Experience, MasterCV, Profile, SkillGroup, Variant
from ..storage import JsonDocument
from .companies import NotFound

store = JsonDocument(config.MASTER_CV_FILE, MasterCV, seed_name="master_cv.json")


def get_master() -> MasterCV:
    return store.load()


def _find_experience(cv: MasterCV, experience_id: str) -> Experience:
    for e in cv.experiences:
        if e.id == experience_id:
            return e
    raise NotFound(f"Experience {experience_id!r} not found")


def _find_bullet(cv: MasterCV, bullet_id: str) -> tuple[Experience, Bullet]:
    for e in cv.experiences:
        for b in e.bullets:
            if b.id == bullet_id:
                return e, b
    raise NotFound(f"Bullet {bullet_id!r} not found")


def _clean_keywords(keywords: list[str]) -> list[str]:
    seen, out = set(), []
    for k in keywords:
        k = k.strip()
        if k and k.lower() not in seen:
            seen.add(k.lower())
            out.append(k)
    return out


# ------------------------------------------------------------------ profile

def update_profile(**fields) -> Profile:
    with store.transaction() as cv:
        cv.profile = Profile.model_validate({**cv.profile.model_dump(), **fields})
    return cv.profile


# -------------------------------------------------------------- experiences

def add_experience(company: str, role: str = "", location: str = "", start: str = "",
                   end: str = "", position: int | None = None) -> Experience:
    exp = Experience(company=company, role=role, location=location, start=start, end=end)
    with store.transaction() as cv:
        cv.experiences.insert(len(cv.experiences) if position is None else position, exp)
    return exp


def update_experience(experience_id: str, **fields) -> Experience:
    fields.pop("bullets", None)  # bullets have their own actions
    with store.transaction() as cv:
        exp = _find_experience(cv, experience_id)
        for k, v in fields.items():
            setattr(exp, k, v)
    return exp


def delete_experience(experience_id: str) -> None:
    with store.transaction() as cv:
        cv.experiences.remove(_find_experience(cv, experience_id))


def move_experience(experience_id: str, offset: int) -> None:
    with store.transaction() as cv:
        exp = _find_experience(cv, experience_id)
        i = cv.experiences.index(exp)
        j = max(0, min(len(cv.experiences) - 1, i + offset))
        cv.experiences.insert(j, cv.experiences.pop(i))


# ------------------------------------------------------------------ bullets

def add_bullet(experience_id: str, text: str, keywords: list[str] | None = None,
               pinned: bool = False, priority: int = 0, lead: str = "", group: str = "") -> Bullet:
    bullet = Bullet(text=text, keywords=_clean_keywords(keywords or []), pinned=pinned,
                    priority=priority, lead=lead, group=group)
    with store.transaction() as cv:
        _find_experience(cv, experience_id).bullets.append(bullet)
    return bullet


def update_bullet(bullet_id: str, **fields) -> Bullet:
    if "keywords" in fields:
        fields["keywords"] = _clean_keywords(fields["keywords"])
    with store.transaction() as cv:
        _, bullet = _find_bullet(cv, bullet_id)
        for k, v in fields.items():
            setattr(bullet, k, v)
    return bullet


def _find_variant(cv: MasterCV, variant_id: str) -> tuple[Bullet, Variant]:
    for e in cv.experiences:
        for b in e.bullets:
            for v in b.variants:
                if v.id == variant_id:
                    return b, v
    raise NotFound(f"Wording {variant_id!r} not found")


def add_variant(bullet_id: str, text: str, lead: str | None = None, keywords: list[str] | None = None) -> Variant:
    """Add a wording to an achievement. Lead defaults to the main wording's; keywords to the known ones in the text."""
    with store.transaction() as cv:
        _, bullet = _find_bullet(cv, bullet_id)
        variant = Variant(text=text, lead=bullet.lead if lead is None else lead)
        variant.keywords = _clean_keywords(keywords if keywords is not None else detect_keywords(text, variant.lead))
        bullet.variants.append(variant)
    return variant


def update_variant(variant_id: str, **fields) -> Variant:
    if "keywords" in fields:
        fields["keywords"] = _clean_keywords(fields["keywords"])
    with store.transaction() as cv:
        _, variant = _find_variant(cv, variant_id)
        for k, v in fields.items():
            setattr(variant, k, v)
    return variant


def delete_variant(variant_id: str) -> None:
    with store.transaction() as cv:
        bullet, variant = _find_variant(cv, variant_id)
        bullet.variants.remove(variant)


def delete_bullet(bullet_id: str) -> None:
    with store.transaction() as cv:
        exp, bullet = _find_bullet(cv, bullet_id)
        exp.bullets.remove(bullet)


# ------------------------------------------------- education / skills (lists)

def set_education(items: list[dict]) -> list[Education]:
    with store.transaction() as cv:
        cv.education = [Education.model_validate(i) for i in items]
    return cv.education


def set_skills(groups: list[dict]) -> list[SkillGroup]:
    with store.transaction() as cv:
        cv.skills = [SkillGroup.model_validate(g) for g in groups]
    return cv.skills


def set_languages(languages: list[str]) -> list[str]:
    with store.transaction() as cv:
        cv.languages = [l.strip() for l in languages if l.strip()]
    return cv.languages


def set_synonyms(groups: list[list[str]]) -> list[list[str]]:
    with store.transaction() as cv:
        cv.synonyms = [g for g in (_clean_keywords(g) for g in groups) if len(g) > 1]
    return cv.synonyms


def set_extra_keywords(keywords: list[str]) -> list[str]:
    with store.transaction() as cv:
        cv.extra_keywords = _clean_keywords(keywords)
    return cv.extra_keywords


# ----------------------------------------------------------------- keywords

def keyword_vocabulary() -> list[str]:
    """Every keyword known to the master CV (bullet tags, skills, extras), sorted."""
    cv = get_master()
    words = [k for e in cv.experiences for b in e.bullets for w in b.wordings() for k in w.keywords]
    words += [i for g in cv.skills for i in g.items]
    words += cv.extra_keywords
    return sorted(_clean_keywords(words), key=str.lower)


def keyword_pattern(keyword: str) -> re.Pattern:
    """Case-insensitive whole-word match that also works for 'C++', '.NET', 'CI/CD'.
    A trailing plural is accepted: 'LLM' matches 'LLMs', 'Agent' matches 'agents'."""
    # Spaces and hyphens are interchangeable: "Time Series" matches "time-series".
    kw = r"[\s\-]+".join(re.escape(part) for part in re.split(r"[\s\-]+", keyword.strip()))
    plural = "(?:e?s)?" if keyword.strip()[-1:].isalnum() else ""
    return re.compile(rf"(?<![\w]){kw}{plural}(?![\w])", re.IGNORECASE)


def extract_keywords(text: str) -> list[str]:
    """Return the known keywords that appear in a job description, most frequent first."""
    hits = []
    for kw in keyword_vocabulary():
        count = len(keyword_pattern(kw).findall(text))
        if count:
            hits.append((count, kw))
    return [kw for _, kw in sorted(hits, key=lambda h: (-h[0], h[1].lower()))]


def in_text(keyword: str, wording) -> bool:
    """Is the keyword written in this wording (an achievement's main wording or a variant)?"""
    return bool(keyword_pattern(keyword).search(f"{wording.lead} {wording.text}"))


def bullet_matches(wording, keywords: list[str]) -> list[str]:
    """Keywords literally written in a wording (label or text): selecting it puts exactly
    those words on the CV. Tags are only the vocabulary, not a match."""
    return [k for k in keywords if in_text(k, wording)]


def detect_keywords(text: str, lead: str = "") -> list[str]:
    """Known keywords (vocabulary) written in a bullet: suggestions for its tags."""
    probe = Bullet(text=text, lead=lead)
    return [k for k in keyword_vocabulary() if in_text(k, probe)]


def missing_tags() -> list[dict]:
    """Tags that don't appear in their bullet's text (they would never be matched)."""
    return [{"bullet_id": b.id, "wording_id": w.id, "experience": e.company, "text": w.text, "missing": miss}
            for e in get_master().experiences for b in e.bullets for w in b.wordings()
            if (miss := [k for k in w.keywords if not in_text(k, w)])]
