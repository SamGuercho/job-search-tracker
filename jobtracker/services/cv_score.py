"""Match score: how much of a job's requirements a generated CV actually covers.

The requirements come from jd_match (kept on the company as `job_match`, so scoring again is free). The CV
side is the text of the generated Word file itself, so the score reflects what a recruiter reads: bullets cut
to fit one page and manual edits count.

  score  - % of the requirements written in this CV (required ones weigh double)
  best   - % your master CV covers: the most a tailored CV can reach
Each requirement is `covered` (in this CV), `cut` (in your master CV, not in this CV) or `missing`.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from docx import Document
from docx.oxml.ns import qn

from .. import users
from ..models import Company, JobMatch, MatchScore, Requirement
from . import companies, jd_match, master_cv

REQUIRED_WEIGHT = 2


def text_hash(text: str) -> str:
    return hashlib.sha256(" ".join(text.split()).encode()).hexdigest()[:16]


def from_match(text: str, match: dict) -> JobMatch:
    """Requirements of a jd_match result (covered and gaps)."""
    reqs = [Requirement(term=m.jd_term, keywords=m.keywords, required=m.must_have)
            for m in [*match["matches"], *match["gaps"]]]
    return JobMatch(text_hash=text_hash(text), ai_used=match["ai_used"], requirements=reqs)


def remember(company_id: str, text: str, match: dict) -> None:
    """Keep a jd_match result on the company (when it was made from its current job description)."""
    company = companies.get_company(company_id)
    if company.job_description and text_hash(company.job_description) == text_hash(text):
        companies.update_company(company_id, fetch_logo=False, job_match=from_match(text, match))


def requirements(company: Company, use_ai: bool = False) -> JobMatch | None:
    """The job's requirements: kept ones if they are from the current job description (and from AI when
    `use_ai`), else read again and kept."""
    text = company.job_description
    if not text.strip():
        return None
    kept = company.job_match
    if kept and kept.text_hash == text_hash(text) and (kept.ai_used or not use_ai):
        return kept
    match = jd_match.match_job_description(text, use_ai)
    if use_ai and not match["ai_used"] and kept and kept.text_hash == text_hash(text):
        return kept  # AI unavailable: keep what we had
    result = from_match(text, match)
    companies.update_company(company.id, fetch_logo=False, job_match=result)
    return result


def docx_text(path: Path) -> str:
    """All the text of a Word file, one line per paragraph (tables included)."""
    body = Document(str(path)).element.body
    return "\n".join("".join(t.text or "" for t in p.iter(qn("w:t"))) for p in body.iter(qn("w:p")))


def score(job: JobMatch, cv_text: str) -> MatchScore | None:
    def written(r: Requirement) -> bool:
        return any(master_cv.keyword_pattern(t).search(cv_text) for t in [r.term, *r.keywords])

    covered, cut, missing = [], [], []
    for r in job.requirements:
        (covered if written(r) else cut if r.keywords else missing).append(r)
    weight = lambda rs: sum(REQUIRED_WEIGHT if r.required else 1 for r in rs)
    total = weight(job.requirements)
    if not total:
        return None
    return MatchScore(score=round(100 * weight(covered) / total), best=round(100 * weight(covered + cut) / total),
                      covered=covered, cut=cut, missing=missing, ai_used=job.ai_used)


def for_company(ref: str, use_ai: bool = False) -> dict:
    """Score the application's latest generated CV against its job description, and keep the score on it."""
    company = companies.resolve(ref)
    if not company.job_description.strip():
        raise ValueError(f"{company.name} has no job description: add it first.")
    doc = next((d for d in company.documents if d.kind == "cv" and d.docx), None)
    path = users.output_dir() / doc.docx if doc else None
    if not path or not path.exists():
        raise ValueError(f"No CV generated for {company.name} yet: generate it first.")
    job = requirements(company, use_ai)
    result = score(job, docx_text(path))
    with companies.store.transaction() as book:
        stored = next(c for c in book.companies if c.id == company.id)
        for d in stored.documents:
            if d.created_at == doc.created_at and d.docx == doc.docx:
                d.match = result
                break
    return {"company": company.name, "company_id": company.id, "docx": doc.docx, "match": result}
