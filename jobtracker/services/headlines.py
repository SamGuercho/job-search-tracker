"""Title variants: the title under your name and the summary that goes with it, in several versions (e.g. "Data
Scientist", "AI Engineer", "Scientist"), each with its own summary. One is the default.

For each application the closest variant is picked automatically, like achievement wordings: it compares the
job's title with each variant's title (ignoring seniority words like Senior, Staff, Lead), then the job's
keywords with the variant's hint words. Only texts the user wrote are used, so nothing is invented. The default
is used when no variant fits the job better. An application can also pin a variant, and still override the
title or summary for itself (company.cv_title / cv_summary).
"""
from __future__ import annotations

import re

from ..models import Headline
from . import companies, master_cv

SENIORITY = {"junior", "jr", "mid", "senior", "sr", "staff", "principal", "lead", "head", "chief", "director",
             "manager", "i", "ii", "iii", "iv", "intern", "student", "associate", "distinguished", "of", "the", "and"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z0-9+#]+", (text or "").lower()) if w not in SENIORITY}


def variants() -> list[Headline]:
    return master_cv.get_master().headlines


def default() -> Headline | None:
    hs = variants()
    return next((h for h in hs if h.default), hs[0] if hs else None)


def score(h: Headline, position: str, keywords: list[str], job_text: str) -> float:
    """Title words in common with the job's title (most of the weight), then hint words found in the job."""
    title, job = _words(h.title), _words(position)
    title_fit = len(title & job) / len(title) if title and job else 0.0
    haystack = " ".join([job_text or "", " ".join(keywords or [])]).lower()
    hints = sum(1 for k in h.keywords if k.strip() and k.lower() in haystack)
    return round(10 * title_fit + hints, 2)


def choose(company=None, keywords: list[str] | None = None) -> dict:
    """The variant for an application: {"headline", "how": "pinned" | "closest" | "default", "score"}."""
    hs = variants()
    if not hs:
        return {"headline": None, "how": "none", "score": 0}
    if company is not None and company.cv_headline:
        pinned = next((h for h in hs if h.id == company.cv_headline), None)
        if pinned:
            return {"headline": pinned, "how": "pinned", "score": None}
    fallback = default()
    if company is None:
        return {"headline": fallback, "how": "default", "score": 0}
    words = keywords if keywords is not None else company.keywords
    ranked = sorted(((score(h, company.position, words, company.job_description), h.default, h) for h in hs),
                    key=lambda t: (t[0], t[1]), reverse=True)
    best_score, _, best = ranked[0]
    if best_score <= 0:
        return {"headline": fallback, "how": "default", "score": 0}
    return {"headline": best, "how": "closest", "score": best_score}


def effective(company_ref: str | None = None, keywords: list[str] | None = None) -> dict:
    """What an application's CV will show at the top: title and summary, and where each comes from."""
    c = companies.resolve(company_ref) if company_ref else None
    pick = choose(c, keywords)
    h = pick["headline"]
    return {
        "variant": h.model_dump() if h else None, "how": pick["how"], "score": pick["score"],
        "title": (c.cv_title if c and c.cv_title else (h.title if h else "")),
        "summary": (c.cv_summary if c and c.cv_summary else (h.summary if h else "")),
        "title_tailored": bool(c and c.cv_title), "summary_tailored": bool(c and c.cv_summary),
        "variants": [{"id": x.id, "title": x.title, "default": x.default} for x in variants()],
    }


# --------------------------------------------------------------------- editing

def _save(update) -> list[Headline]:
    with master_cv.store.transaction() as cv:
        update(cv)
        if cv.headlines and not any(h.default for h in cv.headlines):
            cv.headlines[0].default = True
        d = next((h for h in cv.headlines if h.default), None)
        if d:  # the profile keeps mirroring the default (other features read it)
            cv.profile = cv.profile.model_copy(update={"title": d.title, "summary": d.summary})
        return list(cv.headlines)


def add(title: str, summary: str = "", keywords: list[str] | None = None, make_default: bool = False) -> Headline:
    hints = [k.strip() for k in keywords or [] if k.strip()]
    new = Headline(title=" ".join(title.split()), summary=summary.strip(), keywords=hints)
    if not new.title:
        raise ValueError("Give the variant a title")

    def update(cv):
        if make_default or not cv.headlines:
            for h in cv.headlines:
                h.default = False
            new.default = True
        cv.headlines.append(new)
    _save(update)
    return new


def update(headline_id: str, title: str | None = None, summary: str | None = None, keywords: list[str] | None = None,
           make_default: bool | None = None) -> Headline:
    def change(cv):
        h = next((x for x in cv.headlines if x.id == headline_id), None)
        if h is None:
            raise companies.NotFound(f"No title variant {headline_id!r}")
        if title is not None and title.strip():
            h.title = " ".join(title.split())
        if summary is not None:
            h.summary = summary.strip()
        if keywords is not None:
            h.keywords = [k.strip() for k in keywords if k.strip()]
        if make_default:
            for x in cv.headlines:
                x.default = x.id == h.id
    return next(h for h in _save(change) if h.id == headline_id)


def delete(headline_id: str) -> list[Headline]:
    def change(cv):
        if not any(h.id == headline_id for h in cv.headlines):
            raise companies.NotFound(f"No title variant {headline_id!r}")
        if len(cv.headlines) == 1:
            raise ValueError("Keep at least one title")
        cv.headlines = [h for h in cv.headlines if h.id != headline_id]
    return _save(change)


def pin(company_ref: str, headline_id: str) -> dict:
    """Use this variant for this application ("" = pick the closest automatically)."""
    c = companies.resolve(company_ref)
    if headline_id and not any(h.id == headline_id for h in variants()):
        raise companies.NotFound(f"No title variant {headline_id!r}")
    companies.update_company(c.id, cv_headline=headline_id)
    return effective(c.id)
