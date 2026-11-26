"""Tailored CV generation: take every achievement in its most relevant wording, fit to one page, export.

Pipeline (each step is a separate function so an agent can call them alone):
  1. select_bullets(keywords)  -> TailoredCV with every achievement, ranked by relevance to the job
  2. fit_estimate(...)         -> drops the least relevant bullets until it fits 1 page
  3. generate_cv(...)          -> writes <output folder>/<Company>/<First>-<Last>-<Position>-<Company>.docx and .pdf
"""
from __future__ import annotations

import re
from statistics import median

from pydantic import BaseModel

from .. import config, users
from ..cv_templates import get_template
from ..models import DEFAULT_SECTIONS, Education, GeneratedDocument, Profile, SkillGroup
from . import companies, keyword_stats, master_cv, pdf
from .files import archive_previous, output_folder

MAX_PAGES = 1
MAX_PDF_ITERATIONS = 12


class TailoredBullet(BaseModel):
    id: str                 # achievement id
    experience_id: str      # the experience or project it belongs to
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
    fallback: bool = False   # matches none of the job's keywords: fills the page, cut first
    forced: bool = False     # included on purpose for this CV (the application's cv_include): never cut
    project: bool = False    # a project's achievement: professional experience comes first when trimming
    unique: list[str] = []   # (projects) important keywords no professional experience proves: kept the longest


class TailoredExperience(BaseModel):
    id: str
    company: str
    role: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[TailoredBullet] = []


class TailoredProject(BaseModel):
    id: str
    name: str
    role: str = ""
    link: str = ""
    start: str = ""
    end: str = ""
    bullets: list[TailoredBullet] = []


class TailoredCV(BaseModel):
    keywords: list[str]
    required: list[str] = []
    profile: Profile
    experiences: list[TailoredExperience]
    projects: list[TailoredProject] = []
    sections: list[str] = DEFAULT_SECTIONS   # the order of the sections on the page (master CV's choice)
    education: list[Education]
    skills: list[SkillGroup]
    languages: list[str] = []
    removed: list[TailoredBullet] = []
    left_out: list[TailoredProject] = []   # projects not on this CV (nothing relevant, or all cut); bullets in removed
    unmatched_keywords: list[str] = []

    def bullet_owners(self) -> list[TailoredExperience | TailoredProject]:
        return [*self.experiences, *self.projects]


# ------------------------------------------------------------- 1. selection

REQUIRED_WEIGHT = 2.0


def select_bullets(keywords: list[str], keep_unmatched_roles: bool = True,
                   required: list[str] | None = None, everything: bool = False,
                   include: list[str] | None = None) -> TailoredCV:
    """Take every achievement once, in the wording most relevant to the job; trimming to the page comes later.

    Projects' achievements are picked like experiences', but professional experience has priority when trimming:
    a project's achievement is kept the longest only when it proves an important keyword (`unique`) that no
    professional experience does; otherwise it is cut before any experience's (see _remove_weakest). A project
    whose achievements all match nothing is left out of a job's CV (into `removed`): projects have no minimum.
    Important = required by the job, or asked for by the user's applications at least as often as the job's
    median keyword (keyword_stats); for a general CV (no keywords), the user's strong keywords.
    Greedy coverage ranks them: repeatedly take the achievement whose best wording covers the most keywords
    not covered yet (`required` keywords weigh double; priority breaks ties). Achievements that match only
    already-covered keywords are `redundant`; those matching no keyword are `fallback` (main wording) and are
    the first cut when the CV is too long, then the redundant ones. Pinned achievements are never cut, nor the
    ones in `include` (achievement ids forced into this CV: an application's cv_include).
    keep_unmatched_roles=False drops the roles where nothing matches. A short job description therefore
    still gets a full CV: nothing is removed unless the page is full.
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
    options = {}   # achievement id -> (experience or project, bullet, [(wording, matched)])
    for exp in cv.bullet_owners():
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
    for bid in order:  # matching, but nothing new: kept, trimmed after the unmatched ones
        if bid not in chosen and any(m for _, m in options[bid][2]):
            pick(bid, redundant=True)
    for bid in order:  # matching nothing: kept to fill the page, trimmed first
        if bid not in chosen:
            pick(bid)
            chosen[bid].fallback = not everything
    for bid in include or []:  # an id that no longer exists is ignored
        if bid in chosen:
            chosen[bid].pinned = chosen[bid].forced = True
    _mark_projects(cv, chosen, keywords, required_set)

    removed: list[TailoredBullet] = []
    left_out: list[TailoredProject] = []

    def tailored(owners, model, fields, is_project=False):
        out = []
        for o in owners:
            bullets = [chosen[b.id] for b in o.bullets if b.id in chosen]  # CV order, not pick order
            unmatched = bullets and all(b.fallback and not b.forced for b in bullets)
            if unmatched and (is_project or not keep_unmatched_roles):
                if is_project:  # nothing in it makes sense for this job: the project stays out (can be forced in)
                    removed.extend(bullets)
                    left_out.append(model(**o.model_dump(include=fields)))
                continue
            out.append(model(**o.model_dump(include=fields), bullets=bullets))
        return out

    experiences = tailored(cv.experiences, TailoredExperience, {"id", "company", "role", "location", "start", "end"})
    projects = tailored(cv.projects, TailoredProject, {"id", "name", "role", "link", "start", "end"}, is_project=True)

    covered |= skill_covered
    return TailoredCV(
        keywords=keywords, required=list(required or []), profile=cv.profile, experiences=experiences,
        projects=projects, sections=cv.sections, education=cv.education, skills=skills, languages=cv.languages,
        unmatched_keywords=[k for k in keywords if k.lower() not in covered], removed=removed, left_out=left_out,
    )


def _mark_projects(cv, chosen: dict[str, TailoredBullet], keywords: list[str], required: set[str]) -> None:
    """Flag projects' achievements, and their `unique` keywords: important ones no professional experience proves."""
    in_experience = lambda k: any(master_cv.in_text(k, w) for e in cv.experiences for b in e.bullets
                                  for w in b.wordings())
    history = keyword_stats.importance()
    if keywords:  # a job's CV: required, or asked for at least as often as the job's median keyword
        bar = median([history.get(k.lower(), 0) for k in keywords])
        important = lambda k: k.lower() in required or history.get(k.lower(), 0) >= bar
    else:  # general CV: the user's strong keywords
        strong = keyword_stats.strong_keywords(history)
        important = lambda k: k.lower() in strong
    for p in cv.projects:
        for b in p.bullets:
            t = chosen.get(b.id)
            if t is None:
                continue
            t.project = True
            # what the chosen wording proves: the job's keywords it matches, or (general CV) its own written tags
            wording = next((w for w in b.wordings() if (w.id if w is not b else "") == t.variant_id), b)
            proves = t.matched or master_cv.bullet_matches(wording, wording.keywords)
            t.unique = [k for k in proves if important(k) and not in_experience(k)]


GENERAL_FOLDER = "Master CV"


def cv_file_stem(p, position: str, company: str) -> str:
    """Samuel-Benguerchon-Senior-Data-Scientist-Sensi-AI: hyphens only (no spaces to break in a mail or a
    link); an empty part is left out (the general CV has no company)."""
    def part(v: str) -> str:  # words only: "ACME & Sons" -> "ACME-Sons", "" stays ""
        words = re.sub(r"[^\w\-&. ]+", " ", v or "").split()
        return "-".join(w.strip(".&") for w in words if re.search(r"\w", w))

    stem = config.CV_FILENAME.format(first_name=part(p.first_name), last_name=part(p.last_name),
                                     name=part(p.name), position=part(position), company=part(company))
    return re.sub(r"([-_ ])[-_ ]+", r"\1", stem).strip("-_ .") or "CV"


def generate_general(template: str | None = None, export_pdf: bool = True) -> dict:
    """A CV for no job in particular (headhunters, networking): every achievement in its main wording,
    trimmed to one page by priority (pinned first, then highest priority). Saved in <output>/Master CV/."""
    return generate_cv([], company_name=GENERAL_FOLDER, template=template, export_pdf=export_pdf, everything=True)


def generate_full(template: str | None = None, export_pdf: bool = True) -> dict:
    """The whole master CV, to proofread it: every achievement in its main wording, every project, nothing cut,
    as many pages as it takes. Saved next to the general CV, in <output>/Master CV/, with "-Full" in its name."""
    return generate_cv([], company_name=GENERAL_FOLDER, template=template, export_pdf=export_pdf, everything=True,
                       fit=False)


def use_summary(cv: TailoredCV, company, keywords: list[str] | None = None) -> dict:
    """The top of the CV: the title is the application's own, else the job's title (its position), else the
    variant's; the summary is the application's own, else the one of the title variant closest to the job (see
    headlines.py). Returns what was used, for the report."""
    from . import headlines

    pick = headlines.choose(company, keywords)
    h = pick["headline"]
    own_summary = company.cv_summary.strip() if company is not None else ""
    title, title_from = headlines.title_for(company, h)
    summary = own_summary or (h.summary if h else "")
    update = {k: v for k, v in {"title": title, "summary": summary}.items() if v}
    if update:
        cv.profile = cv.profile.model_copy(update=update)
    return {"title": title, "title_from": title_from, "variant": h.title if h else None, "how": pick["how"],
            "summary": summary,
            **(headlines.summary_advice(summary, title, bool(own_summary)) if company is not None else {}),
            "title_tailored": bool(company is not None and company.cv_title.strip()),
            "summary_tailored": bool(company is not None and company.cv_summary.strip())}


# ------------------------------------------------------------- 2. trimming

def _remove_weakest(cv: TailoredCV) -> TailoredBullet | None:
    """Drop the least useful bullet. Professional experience comes first: projects' achievements are cut before any
    experience's, except those proving an important keyword no experience does (`unique`), cut last. Within that:
    those matching no keyword first, then those only repeating covered keywords, then the smallest coverage gain
    and priority, avoiding emptying a role (a project may go entirely: it then leaves the CV), latest in the CV."""
    candidates = []
    for ei, exp in enumerate(cv.bullet_owners()):
        for bi, b in enumerate(exp.bullets):
            if b.pinned:
                continue
            tier = (2 if b.unique else 0) if b.project else 1
            last_of_role = len(exp.bullets) == 1 and not b.project
            key = (tier, not b.fallback, not b.redundant, last_of_role, b.gain, b.priority, len(b.matched), -ei, -bi)
            candidates.append((key, exp, b))
    if not candidates:
        return None
    _, exp, bullet = min(candidates, key=lambda c: c[0])
    exp.bullets.remove(bullet)
    cv.removed.append(bullet)
    if bullet.project and not exp.bullets:
        cv.projects.remove(exp)
        cv.left_out.append(exp)
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
                everything: bool = False, include: list[str] | None = None, fit: bool = True) -> dict:
    """Create a one-page tailored CV (.docx + .pdf) in <output folder>/<Company>/ (fit=False: nothing is cut,
    any number of pages: generate_full).
    An existing CV with the same name is moved to <Company>/archive/ first.

    Give either an existing `company_id` (the files are attached to it) or a
    free `company_name`. Returns paths, page count and what was kept/removed. The application's forced
    achievements (cv_include) and `include` are never cut.
    """
    company = companies.get_company(company_id) if company_id else None
    if company is None and company_name:
        company = companies.find_by_name(company_name)
    name = company.name if company else (company_name or "General")
    tpl = get_template(template)

    include = [*(company.cv_include if company else []), *(include or [])]
    cv = select_bullets(keywords, keep_unmatched_roles, required, everything, include)
    headline = use_summary(cv, company, keywords)
    engine = pdf.available_engine() if export_pdf else None
    # With a real PDF check we can afford an optimistic estimate; otherwise be safe.
    if fit:
        fit_estimate(cv, template, budget=1.05 if engine else 0.97)

    folder = output_folder(name)
    stem = cv_file_stem(cv.profile, (company.position if company else "") or cv.profile.title,
                        "" if name == GENERAL_FOLDER else name) + ("" if fit else "-Full")
    docx_path, pdf_path = folder / f"{stem}.docx", folder / f"{stem}.pdf"
    archive_previous(docx_path, pdf_path)

    tpl.render(cv, docx_path)
    pages, warnings = None, []
    if engine:
        try:
            for _ in range(MAX_PDF_ITERATIONS):
                pdf.convert(docx_path, pdf_path)
                pages = pdf.page_count(pdf_path)
                if not fit or pages <= MAX_PAGES or not _remove_weakest(cv):
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

    rel = lambda p: p.relative_to(users.output_dir()).as_posix() if p and p.exists() else None
    document = GeneratedDocument(keywords=cv.keywords, docx=rel(docx_path), pdf=rel(pdf_path))
    match = None
    if company:
        companies.add_document(company.id, document)
        if not company.keywords:
            companies.update_company(company.id, keywords=cv.keywords)
        if company.job_description.strip() and document.docx:
            from . import cv_score
            try:
                match = cv_score.for_company(company.id)["match"]
            except Exception as e:  # the CV is made; a failed score must not hide it
                warnings.append(f"Match score not computed: {e}")

    return {
        "company": name,
        "company_id": company.id if company else None,
        "folder": str(folder),
        "docx": document.docx,
        "pdf": document.pdf,
        "pages": pages,
        "estimated_pages": round(tpl.estimate_pages(cv), 2),
        "pdf_engine": engine,
        "kept": [b.model_dump() for e in cv.bullet_owners() for b in e.bullets],
        "removed": [b.model_dump() for b in cv.removed],
        "unmatched_keywords": cv.unmatched_keywords,
        "match": match.model_dump() if match else None,   # how much of the job's requirements this CV covers
        "headline": headline,   # title/summary used at the top, and whether it came from a variant or the application
        "warnings": warnings,
    }
