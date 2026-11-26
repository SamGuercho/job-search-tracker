"""Match a job description against the master CV.

For every requirement of the job description, find the terms *written in your
CV* that cover it, in three passes:
  1. exact    - a known keyword appears in the job description
  2. synonym  - the job description uses another term of a synonym group
  3. ai       - the LLM maps requirements to your CV terms (optional, needs a key): the same thing, a synonym or
                 a concrete instance only; "related" terms are dropped (precision over recall: a missed gap hurts
                 more than a false one, which the user dismisses)
Requirements nothing covers are returned as gaps.
A covered requirement is `literal` when the job's own word (or a form of it) is written in the CV: that is what
an ATS looks for. Covered only through other words (a synonym, an instance), it is still a gap, but a resolvable
one (`reword`): a wording of one of `reword_bullets` that uses the job's word (cv.variant.draft) closes it.
When the model isn't sure a CV term means the same thing, it is an outright gap.
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
    in_education: bool = False
    literal: bool = False      # the job's word (or a form of it) is written in the CV, as an ATS expects
    reword: bool = False       # covered with other words only: a bullet needs a wording with the job's word
    reword_bullets: list[dict] = []  # {id, text}: the bullets that say it with other words (to draft a variant)


# Degrees: "M.Sc.", "Master's", "MS" all name the same level. Levels are ranked: a higher degree covers a lower one.
# Short capital forms are matched in capitals only and only before a degree context ("BS/MS in…", "MS degree", "MS or
# PhD"), so "200 ms", "MS SQL" and "Boston, MA" aren't degrees; "master" alone is not one either ("Scrum master").
_B, _A = r"(?<![\w.])", r"(?![\w])"
_DEGREE_CONTEXT = r"(?=\s*(?:/|\(|\bor\b|\bin\b|\bdegree))"
DEGREES = {  # rank: (patterns, any case), (patterns, capitals only)
    1: ([r"bachelor(?:'s|’s|s)?", r"b\.?\s?sc\.?", r"b\.?\s?eng\.?", r"b\.?\s?tech\.?", r"undergraduate degree"],
        [r"BS", r"BA", r"B\.S\.", r"B\.A\."]),
    2: ([r"master(?:'s|’s|s)", r"master\s+(?:degree|of)", r"m\.?\s?sc\.?", r"m\.?\s?eng\.?", r"postgraduate degree"],
        [r"MS", r"MA", r"M\.S\.", r"M\.A\."]),
    3: ([r"ph\.?\s?d\.?", r"doctorate", r"doctoral degree"], []),
}
_DEGREE_PATTERNS = {rank: [re.compile(_B + p + _A, re.IGNORECASE) for p in loose]
                    + [re.compile(_B + p + _A + _DEGREE_CONTEXT) for p in caps]
                    for rank, (loose, caps) in DEGREES.items()}


def degree_mentions(text: str) -> dict[int, str]:
    """The degree levels a text names: {rank: the first wording found}."""
    out = {}
    for rank, patterns in _DEGREE_PATTERNS.items():
        hits = [m for p in patterns for m in p.finditer(text or "")]
        if hits:
            out[rank] = min(hits, key=lambda m: m.start()).group(0).strip()
    return out


class _AITerm(BaseModel):
    term: str
    relation: Literal["same", "synonym", "instance", "related"]


class _AIRequirement(BaseModel):
    requirement: str
    must_have: bool
    cv_terms: list[_AITerm]


class _AIResult(BaseModel):
    requirements: list[_AIRequirement]


AI_INSTRUCTIONS = """You compare a job description with a candidate's CV terms, the way an ATS and a recruiter would.
1. List the 10 to 25 most important requirements of the job: skills, tools, methods, domains (industries, business
   areas), qualifications, soft skills. Write each as the keyword an ATS would search, 1 to 4 words, AS WORDED IN
   THE JOB DESCRIPTION: a noun phrase without the verb ("dashboards", not "develop and maintain dashboards";
   "replenishment", "supply chain", "large language models", "Kubernetes"). must_have = explicitly required.
2. For each requirement, cv_terms = the terms from the CV TERMS list below that cover it, copied exactly, each
   with its relation:
   - "same": the job's word itself or a form of it (dashboards -> "dashboard", analysis -> "Data Analysis")
   - "synonym": another name for exactly the same thing (large language models -> "LLM", BI -> "Business Intelligence")
   - "instance": a concrete tool or method that IS one of what the job names (vector databases -> "Pinecone",
     data visualization tools -> "Tableau")
   - "related": anything else (a broader field, an adjacent activity, a generic term). These are dropped.
   Precision matters more than recall: a requirement wrongly marked as covered hides a real gap from the
   candidate, while a false gap is simply dismissed. Use "synonym" or "instance" only when you are sure the CV
   term means exactly that; if you hesitate, it is "related" (an outright gap).
   NOT covered, so "related" or nothing:
   - a domain by an activity or a generic term: retail -> "customer-facing analytics", "B2B";
     supply chain -> "operational data"; inventory management -> "operational data", "Process Optimization"
   - a specific task by a vague catch-all: replenishment -> "practical solutions"; store managers -> "Communication"
   - a skill by a different skill: data analysis -> "Analytical & Critical Thinking"
   - every tool of the CV for one tool: Excel -> "Git", "PostgreSQL"
   Empty list if the CV does not cover it.

CV TERMS: {terms}"""


class _CV:
    """Snapshot of the master CV used for lookups."""

    def __init__(self):
        self.cv = master_cv.get_master()
        self.bullets = [b for e in self.cv.experiences for b in e.bullets]
        self.skills = [i for g in self.cv.skills for i in g.items]
        self.education = [" ".join(x for x in (e.degree, e.school, e.details) if x) for e in self.cv.education]
        # the user's degrees by level: {rank: the degree as written on the CV ("MSc Data Science")}
        self.degrees: dict[int, str] = {}
        for e in self.cv.education:
            for rank in degree_mentions(e.degree):
                self.degrees.setdefault(rank, e.degree.strip())

    def in_bullets(self, term: str) -> int:
        """Achievements with at least one wording that contains the term."""
        return sum(any(master_cv.in_text(term, w) for w in b.wordings()) for b in self.bullets)

    def in_skills(self, term: str) -> bool:
        pattern = master_cv.keyword_pattern(term)
        return any(pattern.search(i) for i in self.skills)

    def in_education(self, term: str) -> bool:
        pattern = master_cv.keyword_pattern(term)
        return any(pattern.search(e) for e in self.education)

    def has(self, term: str) -> bool:
        return bool(self.in_bullets(term) or self.in_skills(term) or self.in_education(term))

    def literal(self, jd_term: str) -> bool:
        """The job's own word (or a form of each of its words) is written in one bullet, skill or degree."""
        texts = [f"{w.lead} {w.text}" for b in self.bullets for w in b.wordings()] + self.skills + self.education
        return any(master_cv.written_in(jd_term, t) for t in texts)

    def degree_for(self, text: str) -> str:
        """The user's degree that meets a requirement naming degree levels ("Bachelor's or Master's": the lowest
        named), as written on the CV; "" if none does (or the text names no degree)."""
        named = degree_mentions(text)
        if not named or not self.degrees:
            return ""
        enough = [rank for rank in self.degrees if rank >= min(named)]
        return self.degrees[min(enough)] if enough else ""

    def terms(self) -> list[str]:
        """Vocabulary and synonym terms actually written somewhere in the CV, and the user's degrees."""
        candidates = master_cv.keyword_vocabulary() + [t for g in self.cv.synonyms for t in g]
        degrees = [e.degree.strip() for e in self.cv.education if e.degree.strip()]
        return master_cv._clean_keywords([t for t in candidates if self.has(t)] + degrees)


def match_job_description(text: str, use_ai: bool = True) -> dict:
    cv = _CV()
    found = lambda term: bool(master_cv.keyword_pattern(term).search(text))
    matches: dict[str, Match] = {}

    def add(jd_term: str, keywords: list[str], source: Source, must_have: bool = False):
        key = re.sub(r"[\s\-]+", " ", jd_term.strip().lower())  # "Retrieval-Augmented" == "Retrieval Augmented"
        if key in matches:
            m = matches[key]
            if not (m.source == "exact" and m.keywords):  # written as in the job: other terms would only add noise
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

    # 3. degrees the job names ("M.Sc.", "Bachelor's", "PhD"), met by the user's degree at that level or above
    for wording in degree_mentions(text).values():
        degree = cv.degree_for(wording)
        add(wording, [degree] if degree else [], "exact")

    # 4. AI: requirements phrased differently, mapped to CV terms only
    ai_used, ai_error = False, None
    if use_ai and llm.available():
        terms = cv.terms()
        allowed = {t.lower(): t for t in terms}
        try:
            result = llm.structured(AI_INSTRUCTIONS.format(terms=", ".join(terms)), text, _AIResult)
            ai_used = True
            for r in result.requirements:
                kws = [allowed[t.term.strip().lower()] for t in r.cv_terms
                       if t.relation != "related" and t.term.strip().lower() in allowed]
                add(r.requirement, kws, "ai", r.must_have)
        except llm.LLMUnavailable as e:
            ai_error = str(e)

    for m in matches.values():
        by_degree = False
        if not m.keywords or (degree_mentions(m.jd_term) and cv.degree_for(m.jd_term)):
            # "Master's degree in a quantitative field", "BA/BSc": met by the user's degree at that level or above
            degree = cv.degree_for(m.jd_term)
            m.keywords, by_degree = ([degree], True) if degree else (m.keywords, False)
        carrying = [b for b in cv.bullets if any(master_cv.in_text(k, w) for k in m.keywords for w in b.wordings())]
        m.bullets = len(carrying)
        m.in_skills = any(cv.in_skills(k) for k in m.keywords)
        m.in_education = any(cv.in_education(k) for k in m.keywords)
        if m.keywords:
            m.literal = by_degree or (m.source == "exact" and m.jd_term in m.keywords) or cv.literal(m.jd_term)
            m.reword = not m.literal
            m.reword_bullets = [{"id": b.id, "text": f"{b.lead} {b.text}".strip()} for b in carrying[:3]] if m.reword else []

    # With AI, drop word-level rows that an AI requirement already shows ("LLM" inside "large language models"),
    # unless that requirement needs rewording while the word-level row is written as in the job.
    ai_sets = [({k.lower() for k in m.keywords}, m.literal) for m in matches.values() if m.source == "ai" and m.keywords]
    for key, m in list(matches.items()):
        if m.source != "ai" and m.keywords and any({k.lower() for k in m.keywords} <= s and (lit or not m.literal)
                                                   for s, lit in ai_sets):
            del matches[key]

    # The job names it twice ("Machine Learning" and "ML") and one of its wordings is written: nothing to reword.
    literal_sets = [{k.lower() for k in m.keywords} for m in matches.values() if m.literal]
    for key, m in list(matches.items()):
        if m.reword and m.source == "synonym" and any({k.lower() for k in m.keywords} <= s for s in literal_sets):
            del matches[key]

    # A gap that is part of, or a synonym of, a requirement written as in the job is not a real gap ("Agents" vs
    # "AI Agents"). Covered with other words doesn't count: "retail" stays a gap next to "retail performance".
    covered = [m.jd_term.lower() for m in matches.values() if m.keywords and m.literal]
    group_of = {t.lower(): i for i, g in enumerate(cv.cv.synonyms) for t in g}
    covered_terms = covered + [k.lower() for m in matches.values() if m.literal for k in m.keywords]
    covered_groups = {group_of[c] for c in covered_terms if c in group_of}
    for key, m in list(matches.items()):
        term = m.jd_term.lower()
        if not m.keywords and (any(term in c for c in covered) or group_of.get(term) in covered_groups):
            del matches[key]

    ordered = sorted(matches.values(), key=lambda m: (not m.keywords, m.reword, not m.must_have, -m.bullets,
                                                      m.jd_term.lower()))
    return {
        "matches": [m for m in ordered if m.keywords],          # covered: written as in the job, then `reword` ones
        "reword": [m for m in ordered if m.reword],             # covered with other words: needs a wording
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
