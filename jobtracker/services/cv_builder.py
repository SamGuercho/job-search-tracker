"""Tailored CV generation: select bullets by keyword, fit to one page, export.

Pipeline (each step is a separate function so an agent can call them alone):
  1. select_bullets(keywords)  -> TailoredCV with every matching bullet
  2. fit_to_page(...)          -> drops the weakest bullets until it fits 1 page
  3. generate_cv(...)          -> writes <output folder>/<Company>/<Name>_CV.docx and .pdf
"""
from __future__ import annotations

from pydantic import BaseModel

from .. import config
from ..cv_templates import get_template
from ..models import Education, GeneratedDocument, Profile, SkillGroup
from . import companies, master_cv, pdf
from .files import archive_previous, output_folder, safe_name

MAX_PAGES = 1
MAX_PDF_ITERATIONS = 12


class TailoredBullet(BaseModel):
    id: str                 # achievement id
    experience_id: str
    variant_id: str = ""    # "" = main wording
    wordings: int = 1       # how many wordings the achievement has
    text: str
    lead: str = ""
    group: str = ""
    matched: list[str] = []
    gain: float = 0         # weight of the keywords this bullet was the first to cover
    pinned: bool = False
    priority: int = 0
    redundant: bool = False  # matches, but only keywords other bullets already cover
    fallback: bool = False   # kept only so the role isn't empty


class TailoredExperience(BaseModel):
    id: str
    company: str
    role: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[TailoredBullet] = []


class TailoredCV(BaseModel):
    keywords: list[str]
    required: list[str] = []
    profile: Profile
    experiences: list[TailoredExperience]
    education: list[Education]
    skills: list[SkillGroup]
    languages: list[str] = []
    removed: list[TailoredBullet] = []
    unmatched_keywords: list[str] = []


# ------------------------------------------------------------- 1. selection

REQUIRED_WEIGHT = 2.0


def select_bullets(keywords: list[str], keep_unmatched_roles: bool = True,
                   required: list[str] | None = None, everything: bool = False) -> TailoredCV:
    """Choose achievements to cover as many job keywords as possible, never twice the same achievement.

    Greedy coverage: repeatedly take the achievement whose best wording covers the most keywords not
    covered yet (`required` keywords weigh double; priority breaks ties). Achievements that match only
    already-covered keywords are kept as `redundant` (first to go when trimming). Pinned achievements
    are always in. A role left empty keeps its highest-priority achievement as a `fallback`, unless
    keep_unmatched_roles=False, in which case the role is dropped.
    """
    keywords = [k.strip() for k in keywords if k.strip()]
    required_set = {k.strip().lower() for k in (required or [])}
    weight = lambda k: REQUIRED_WEIGHT if k.lower() in required_set else 1.0
    cv = master_cv.get_master()

    # Skills are always printed; they count for `unmatched_keywords` but bullets still compete for every
    # keyword, since a keyword proven in an experience is worth more than one only listed in Skills.
    skills, skill_covered = [], set()
    for g in cv.skills:
        hits = [i for i in g.items if any(master_cv.keyword_pattern(k).search(i) for k in keywords)]
        skill_covered.update(k.lower() for k in keywords for i in hits if master_cv.keyword_pattern(k).search(i))
        skills.append(SkillGroup(id=g.id, name=g.name, items=hits + [i for i in g.items if i not in hits]))

    # Every wording's matches, per achievement
    options = {}   # achievement id -> (experience, bullet, [(wording, matched)])
    for exp in cv.experiences:
        for b in exp.bullets:
            options[b.id] = (exp, b, [(w, master_cv.bullet_matches(w, keywords)) for w in b.wordings()])

    covered: set[str] = set()
    chosen: dict[str, TailoredBullet] = {}

    def pick(bid: str, redundant: bool = False) -> None:
        exp, b, wordings = options[bid]
        gain_of = lambda m: sum(weight(k) for k in m if k.lower() not in covered)
        wording, matched = max(wordings, key=lambda wm: (gain_of(wm[1]), len(wm[1]), wm[0] is b))
        gain = gain_of(matched)
        covered.update(k.lower() for k in matched)
        chosen[bid] = TailoredBullet(
            id=b.id, experience_id=exp.id, variant_id="" if wording is b else wording.id, wordings=len(wordings),
            text=wording.text, lead=wording.lead, group=b.group, matched=matched, gain=gain,
            pinned=b.pinned, priority=b.priority, redundant=redundant)

    order = list(options)
    if everything:  # general CV: every achievement in its main wording; trimming then goes by priority
        for bid in order:
            pick(bid)
    for bid, (_, b, _) in options.items():
        if b.pinned and bid not in chosen:
            pick(bid)
    while not everything:
        best, best_key = None, None
        for i, bid in enumerate(order):
            if bid in chosen:
                continue
            _, b, wordings = options[bid]
            gain = max(sum(weight(k) for k in m if k.lower() not in covered) for _, m in wordings)
            key = (gain, b.priority, max(len(m) for _, m in wordings), -i)
            if gain > 0 and (best_key is None or key > best_key):
                best, best_key = bid, key
        if best is None:
            break
        pick(best)
    for bid in order:  # matching, but nothing new: keep for now, trimmed first
        if bid not in chosen and any(m for _, m in options[bid][2]):
            pick(bid, redundant=True)

    experiences = []
    for exp in cv.experiences:
        bullets = [chosen[b.id] for b in exp.bullets if b.id in chosen]  # CV order, not pick order
        if not bullets and exp.bullets:
            if not keep_unmatched_roles:
                continue
            best = max(exp.bullets, key=lambda b: b.priority)
            bullets = [TailoredBullet(id=best.id, experience_id=exp.id, wordings=len(best.wordings()), text=best.text,
                                      lead=best.lead, group=best.group, priority=best.priority, fallback=True)]
        experiences.append(TailoredExperience(
            **exp.model_dump(include={"id", "company", "role", "location", "start", "end"}), bullets=bullets))

    covered |= skill_covered
    return TailoredCV(
        keywords=keywords, required=list(required or []), profile=cv.profile, experiences=experiences,
        education=cv.education, skills=skills, languages=cv.languages,
        unmatched_keywords=[k for k in keywords if k.lower() not in covered],
    )


GENERAL_FOLDER = "Master CV"


def generate_general(template: str | None = None, export_pdf: bool = True) -> dict:
    """A CV for no job in particular (headhunters, networking): every achievement in its main wording,
    trimmed to one page by priority (pinned first, then highest priority). Saved in <output>/Master CV/."""
    return generate_cv([], company_name=GENERAL_FOLDER, template=template, export_pdf=export_pdf, everything=True)


def use_summary(cv: TailoredCV, company) -> TailoredCV:
    """An application's tailored summary replaces the master one in its CV."""
    if company is not None and company.cv_summary.strip():
        cv.profile = cv.profile.model_copy(update={"summary": company.cv_summary.strip()})
    return cv


# ------------------------------------------------------------- 2. trimming

def _remove_weakest(cv: TailoredCV) -> TailoredBullet | None:
    """Drop the least useful bullet: redundant ones first, then fallbacks, then the smallest
    coverage gain and priority, avoiding emptying a role, latest in the CV."""
    candidates = []
    for ei, exp in enumerate(cv.experiences):
        for bi, b in enumerate(exp.bullets):
            if b.pinned:
                continue
            key = (not b.redundant, not b.fallback, len(exp.bullets) == 1, b.gain, b.priority, len(b.matched), -ei, -bi)
            candidates.append((key, exp, b))
    if not candidates:
        return None
    _, exp, bullet = min(candidates, key=lambda c: c[0])
    exp.bullets.remove(bullet)
    cv.removed.append(bullet)
    return bullet


def fit_estimate(cv: TailoredCV, template_name: str | None = None, budget: float = 1.0) -> TailoredCV:
    template = get_template(template_name)
    while template.estimate_pages(cv) > budget * MAX_PAGES and _remove_weakest(cv):
        pass
    return cv


# --------------------------------------------------------------- 3. export

def generate_cv(keywords: list[str], company_id: str | None = None, company_name: str | None = None,
                template: str | None = None, export_pdf: bool = True,
                keep_unmatched_roles: bool = True, required: list[str] | None = None,
                everything: bool = False) -> dict:
    """Create a one-page tailored CV (.docx + .pdf) in <output folder>/<Company>/.
    An existing CV with the same name is moved to <Company>/archive/ first.

    Give either an existing `company_id` (the files are attached to it) or a
    free `company_name`. Returns paths, page count and what was kept/removed.
    """
    company = companies.get_company(company_id) if company_id else None
    if company is None and company_name:
        company = companies.find_by_name(company_name)
    name = company.name if company else (company_name or "General")
    tpl = get_template(template)

    cv = select_bullets(keywords, keep_unmatched_roles, required, everything)
    use_summary(cv, company)
    engine = pdf.available_engine() if export_pdf else None
    # With a real PDF check we can afford an optimistic estimate; otherwise be safe.
    fit_estimate(cv, template, budget=1.05 if engine else 0.97)

    folder = output_folder(name)
    stem = config.CV_FILENAME.format(name=safe_name(cv.profile.name or "Candidate"), company=safe_name(name))
    docx_path, pdf_path = folder / f"{stem}.docx", folder / f"{stem}.pdf"
    archive_previous(docx_path, pdf_path)

    tpl.render(cv, docx_path)
    pages, warnings = None, []
    if engine:
        try:
            for _ in range(MAX_PDF_ITERATIONS):
                pdf.convert(docx_path, pdf_path)
                pages = pdf.page_count(pdf_path)
                if pages <= MAX_PAGES or not _remove_weakest(cv):
                    break
                tpl.render(cv, docx_path)
            if pages and pages > MAX_PAGES:
                warnings.append(f"Still {pages} pages: only pinned bullets are left. Unpin some or shorten them.")
        except pdf.ConversionError as e:
            warnings.append(str(e))
            pdf_path = None
    else:
        pdf_path = None
        if export_pdf:
            warnings.append("No PDF engine found (install LibreOffice or Microsoft Word); page fit is estimated.")

    rel = lambda p: p.relative_to(config.OUTPUT_DIR).as_posix() if p and p.exists() else None
    document = GeneratedDocument(keywords=cv.keywords, docx=rel(docx_path), pdf=rel(pdf_path))
    if company:
        companies.add_document(company.id, document)
        if not company.keywords:
            companies.update_company(company.id, keywords=cv.keywords)

    return {
        "company": name,
        "company_id": company.id if company else None,
        "folder": str(folder),
        "docx": document.docx,
        "pdf": document.pdf,
        "pages": pages,
        "estimated_pages": round(tpl.estimate_pages(cv), 2),
        "pdf_engine": engine,
        "kept": [b.model_dump() for e in cv.experiences for b in e.bullets],
        "removed": [b.model_dump() for b in cv.removed],
        "unmatched_keywords": cv.unmatched_keywords,
        "warnings": warnings,
    }
