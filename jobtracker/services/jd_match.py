"""Match a job description against the master CV.

For every requirement of the job description, find the terms *written in your
CV* that cover it, in three passes:
  1. exact    - a known keyword appears in the job description
  2. synonym  - the job description uses another term of a synonym group
  3. ai       - the LLM maps requirements to your CV terms (optional, needs a key)
Requirements nothing covers are returned as gaps (bullets to reword).
"""
from __future__ import annotations

import difflib
import re
from typing import Literal

from pydantic import BaseModel

from . import llm, master_cv

Source = Literal["exact", "synonym", "ai"]


class Match(BaseModel):
    jd_term: str
    keywords: list[str] = []   # terms written in your CV that cover the requirement
    source: Source
    must_have: bool = False
    bullets: int = 0           # bullets containing at least one of the keywords
    in_skills: bool = False


class _AIRequirement(BaseModel):
    requirement: str
    must_have: bool
    cv_terms: list[str]


class _AIResult(BaseModel):
    requirements: list[_AIRequirement]


AI_INSTRUCTIONS = """You compare a job description with a candidate's CV terms.
1. List the 10 to 25 most important requirements of the job: skills, tools, methods, domains, qualifications,
   soft skills. Write each as a short term of 1 to 4 words AS WORDED IN THE JOB DESCRIPTION
   (e.g. "large language models", "vector databases", "Kubernetes"). must_have = explicitly required.
2. For each requirement, cv_terms = the terms from the CV TERMS list below that are the same thing, a synonym or
   abbreviation, or a concrete instance of it (e.g. "vector databases" -> "Pinecone"). Copy them exactly from the
   list. Be strict: no loosely related terms. Empty list if the CV does not cover it.

CV TERMS: {terms}"""


class _CV:
    """Snapshot of the master CV used for lookups."""

    def __init__(self):
        self.cv = master_cv.get_master()
        self.bullets = [b for e in self.cv.experiences for b in e.bullets]
        self.skills = [i for g in self.cv.skills for i in g.items]

    def in_bullets(self, term: str) -> int:
        """Achievements with at least one wording that contains the term."""
        return sum(any(master_cv.in_text(term, w) for w in b.wordings()) for b in self.bullets)

    def in_skills(self, term: str) -> bool:
        pattern = master_cv.keyword_pattern(term)
        return any(pattern.search(i) for i in self.skills)

    def has(self, term: str) -> bool:
        return bool(self.in_bullets(term) or self.in_skills(term))

    def terms(self) -> list[str]:
        """Vocabulary and synonym terms actually written somewhere in the CV."""
        candidates = master_cv.keyword_vocabulary() + [t for g in self.cv.synonyms for t in g]
        return [t for t in master_cv._clean_keywords(candidates) if self.has(t)]


def match_job_description(text: str, use_ai: bool = True) -> dict:
    cv = _CV()
    found = lambda term: bool(master_cv.keyword_pattern(term).search(text))
    matches: dict[str, Match] = {}

    def add(jd_term: str, keywords: list[str], source: Source, must_have: bool = False):
        key = re.sub(r"[\s\-]+", " ", jd_term.strip().lower())  # "Retrieval-Augmented" == "Retrieval Augmented"
        if key in matches:
            m = matches[key]
            m.keywords = master_cv._clean_keywords(m.keywords + keywords)
            m.must_have = m.must_have or must_have
        else:
            matches[key] = Match(jd_term=jd_term.strip(), keywords=master_cv._clean_keywords(keywords),
                                 source=source, must_have=must_have)

    # 1. exact: known keywords written in the job description
    for kw in master_cv.keyword_vocabulary():
        if found(kw):
            add(kw, [kw] if cv.has(kw) else [], "exact")

    # 2. synonyms: the job description's wording -> the wording used in the CV
    for group in cv.cv.synonyms:
        in_cv = [t for t in group if cv.has(t)]
        in_jd = [t for t in group if found(t)]
        # "Information Security" in the ad also matches "Security": keep only the longest wording
        in_jd = [t for t in in_jd if not any(t != u and t.lower() in u.lower() for u in in_jd)]
        for term in in_jd:
            add(term, in_cv, "synonym")

    # 3. AI: requirements phrased differently, mapped to CV terms only
    ai_used, ai_error = False, None
    if use_ai and llm.available():
        terms = cv.terms()
        allowed = {t.lower(): t for t in terms}
        try:
            result = llm.structured(AI_INSTRUCTIONS.format(terms=", ".join(terms)), text, _AIResult)
            ai_used = True
            for r in result.requirements:
                kws = [allowed[t.strip().lower()] for t in r.cv_terms if t.strip().lower() in allowed]
                add(r.requirement, kws, "ai", r.must_have)
        except llm.LLMUnavailable as e:
            ai_error = str(e)

    for m in matches.values():
        m.bullets = sum(any(master_cv.in_text(k, w) for k in m.keywords for w in b.wordings()) for b in cv.bullets)
        m.in_skills = any(cv.in_skills(k) for k in m.keywords)

    # With AI, drop word-level rows that an AI requirement already shows ("LLM" inside "large language models").
    ai_sets = [{k.lower() for k in m.keywords} for m in matches.values() if m.source == "ai" and m.keywords]
    for key, m in list(matches.items()):
        if m.source != "ai" and m.keywords and any({k.lower() for k in m.keywords} <= s for s in ai_sets):
            del matches[key]

    # A gap that is part of, or a synonym of, a covered requirement is not a real gap ("Agents" vs "AI Agents").
    covered = [m.jd_term.lower() for m in matches.values() if m.keywords]
    group_of = {t.lower(): i for i, g in enumerate(cv.cv.synonyms) for t in g}
    covered_terms = covered + [k.lower() for m in matches.values() for k in m.keywords]
    covered_groups = {group_of[c] for c in covered_terms if c in group_of}
    for key, m in list(matches.items()):
        term = m.jd_term.lower()
        if not m.keywords and (any(term in c for c in covered) or group_of.get(term) in covered_groups):
            del matches[key]

    ordered = sorted(matches.values(), key=lambda m: (not m.keywords, not m.must_have, -m.bullets, m.jd_term.lower()))
    return {
        "matches": [m for m in ordered if m.keywords],
        "gaps": [m for m in ordered if not m.keywords],
        "keywords": master_cv._clean_keywords([k for m in ordered for k in m.keywords]),
        "required": master_cv._clean_keywords([k for m in ordered if m.must_have for k in m.keywords]),
        "ai_used": ai_used,
        "ai_error": ai_error,
    }


def suggest(word: str, limit: int = 3) -> list[str]:
    """Closest CV terms to a typed word (typos, case, spacing)."""
    terms = _CV().terms()
    lower = {t.lower(): t for t in terms}
    hits = difflib.get_close_matches(word.lower(), list(lower), n=limit, cutoff=0.75)
    hits += [t for t in lower if word.lower() in t and t not in hits]
    return [lower[h] for h in hits[:limit]]
