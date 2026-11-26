"""Master CV: the single source of every bullet point you could put on a CV."""
from __future__ import annotations

import re

from ..models import Bullet, Education, Experience, MasterCV, Profile, Project, SkillGroup, Variant, normalize_sections
from ..storage import UserDocument
from .companies import NotFound

store = UserDocument("master_cv.json", MasterCV, seed_name="master_cv.json")


def get_master() -> MasterCV:
    return store.load()


def _find_experience(cv: MasterCV, experience_id: str) -> Experience:
    for e in cv.experiences:
        if e.id == experience_id:
            return e
    raise NotFound(f"Experience {experience_id!r} not found")


def _find_project(cv: MasterCV, project_id: str) -> Project:
    for p in cv.projects:
        if p.id == project_id:
            return p
    raise NotFound(f"Project {project_id!r} not found")


def _find_owner(cv: MasterCV, owner_id: str) -> Experience | Project:
    """The experience or project with this id (both hold achievements)."""
    for o in cv.bullet_owners():
        if o.id == owner_id:
            return o
    raise NotFound(f"Experience or project {owner_id!r} not found")


def _find_bullet(cv: MasterCV, bullet_id: str) -> tuple[Experience | Project, Bullet]:
    for e in cv.bullet_owners():
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
    """A full `name` alone is split again into first and last name (first word / the rest)."""
    if fields.get("name") is not None and fields.get("first_name") is None and fields.get("last_name") is None:
        first, *rest = fields["name"].split() or [""]
        fields.update(first_name=first, last_name=" ".join(rest))
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


# ----------------------------------------------------------------- projects

def add_project(name: str, role: str = "", link: str = "", start: str = "", end: str = "",
                position: int | None = None) -> Project:
    project = Project(name=name, role=role, link=link.strip(), start=start, end=end)
    with store.transaction() as cv:
        cv.projects.insert(len(cv.projects) if position is None else position, project)
    return project


def update_project(project_id: str, **fields) -> Project:
    fields.pop("bullets", None)  # bullets have their own actions
    with store.transaction() as cv:
        project = _find_project(cv, project_id)
        for k, v in fields.items():
            setattr(project, k, v.strip() if k == "link" and isinstance(v, str) else v)
    return project


def delete_project(project_id: str) -> None:
    with store.transaction() as cv:
        cv.projects.remove(_find_project(cv, project_id))


def move_project(project_id: str, offset: int) -> None:
    with store.transaction() as cv:
        project = _find_project(cv, project_id)
        i = cv.projects.index(project)
        j = max(0, min(len(cv.projects) - 1, i + offset))
        cv.projects.insert(j, cv.projects.pop(i))


# ------------------------------------------------------------- section order

def set_sections(sections: list[str]) -> list[str]:
    """The order of the CV's sections (summary, skills, experience, projects, education, languages).
    Unknown names are dropped; a missing section goes back where the default order puts it."""
    with store.transaction() as cv:
        cv.sections = normalize_sections(sections)
    return cv.sections


# ------------------------------------------------------------------ bullets

def add_bullet(experience_id: str, text: str, keywords: list[str] | None = None,
               pinned: bool = False, priority: int = 0, lead: str = "", group: str = "") -> Bullet:
    bullet = Bullet(text=text, keywords=_clean_keywords(keywords or []), pinned=pinned,
                    priority=priority, lead=lead, group=group)
    with store.transaction() as cv:  # experience_id: an experience's or a project's id
        _find_owner(cv, experience_id).bullets.append(bullet)
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
    for e in cv.bullet_owners():
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
        cv.languages = [lang.strip() for lang in languages if lang.strip()]
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
    """Every keyword known to the master CV (experience and project bullet tags, skills, extras), sorted."""
    cv = get_master()
    words = [k for e in cv.bullet_owners() for b in e.bullets for w in b.wordings() for k in w.keywords]
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


# Word families: an ATS finds a job's word in a CV when it is written there, or a form of it ("analysis",
# "analyze", "analytics"; "replenish", "replenishment"). A synonym ("KPI Reporting" for "retail performance
# tracking") is not found: the bullet needs a wording that uses the job's word.
_SUFFIXES = sorted(["izations", "isations", "ization", "isation", "ations", "ation", "ments", "ment", "ities", "ings",
                    "ness", "tics", "ical", "ally", "ence", "ance", "ions", "ion", "ity", "ing", "ers", "ist", "ize",
                    "ise", "ate", "ive", "ous", "tic", "sis", "ies", "ied", "ed", "es", "er", "al", "ly", "ze",
                    "se", "st", "s", "y", "e"], key=len, reverse=True)
FILLER_WORDS = {"a", "an", "and", "or", "of", "in", "on", "with", "the", "to", "for", "using", "use", "based",
                "experience", "experienced", "proficiency", "proficient", "knowledge", "strong", "skills", "skill",
                "ability", "familiarity", "understanding", "working", "solid", "good", "excellent", "hands",
                "etc", "e.g", "such", "as", "related", "relevant", "least"}


def stem(word: str) -> str:
    """'analysis', 'analyze', 'analytics' -> 'analy'; 'management', 'manager', 'managing' -> 'manag'.
    Short words and acronyms only lose a plural ('KPIs' -> 'kpi'; 'data' stays apart from 'database')."""
    w = word.lower()
    if len(w) <= 4 or not w.isalpha():
        return w[:-1] if len(w) > 3 and w.endswith("s") and w[-2] != "s" else w
    for _ in range(3):
        cut = next((s for s in _SUFFIXES if w.endswith(s) and len(w) - len(s) >= 4), None)
        if not cut:
            break
        w = w[:-len(cut)]
    if len(w) >= 6 and w.endswith(("yz", "ys", "iz")):  # "analyz(ed)" ~ "analysis", "optimiz(ing)" ~ "optimize"
        w = w[:-2]
    if len(w) >= 5 and w[-1] == w[-2] and w[-1] not in "aeiou":  # "shipp" -> "ship", "fulfill" -> "fulfil"
        w = w[:-1]
    return w


def word_stems(text: str, drop_filler: bool = False) -> set[str]:
    words = re.findall(r"[A-Za-z0-9][A-Za-z0-9+#]*", text or "")
    return {stem(w) for w in words if not (drop_filler and w.lower() in FILLER_WORDS)}


def written_in(term: str, text: str) -> bool:
    """Is the job's term (or a form of each of its words) written in this text? 'Excel/Google Sheets': either.
    'data analysis' is found in 'analyzed sales data'; 'supply chain' is not found in 'operational data'."""
    if keyword_pattern(term).search(text or ""):
        return True
    have = word_stems(text)
    for alternative in re.split(r"\s*/\s*|\s+or\s+", term):
        wanted = word_stems(alternative, drop_filler=True)
        if wanted and wanted <= have:
            return True
    return False


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
    return [{"bullet_id": b.id, "wording_id": w.id, "experience": getattr(e, "company", None) or e.name,
             "text": w.text, "missing": miss}
            for e in get_master().bullet_owners() for b in e.bullets for w in b.wordings()
            if (miss := [k for k in w.keywords if not in_text(k, w)])]
