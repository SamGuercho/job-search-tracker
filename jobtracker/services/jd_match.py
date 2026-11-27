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
    in_languages: bool = False
    category: bool = False     # names a family ("BI tools", "cloud platforms"): a CV tool of that family covers it
    literal: bool = False      # the job's word (or a form of it) is written in the CV, as an ATS expects
    reword: bool = False       # covered with other words only: a bullet needs a wording with the job's word
    reword_bullets: list[dict] = []  # {id, text}: the bullets that say it with other words (to draft a variant)


# Degrees: "M.Sc.", "Master's", "MS" all name the same level. Levels are ranked: a higher degree covers a lower one.
# Short capital forms are matched in capitals only and only before a degree context ("BS/MS in…", "MS degree", "MS or
# PhD"), so "200 ms", "MS SQL" and "Boston, MA" aren't degrees; "master" alone is not one either ("Scrum master").
_B, _A = r"(?<![\w.])", r"(?![\w])"
_DEGREE_CONTEXT = r"(?=\s*(?:/|\(|\bor\b|\bin\b|\bdegree))"
DEGREES = {  # rank: (patterns, any case), (patterns, capitals only)
    1: ([r"bachelor(?:'s|’s|s)?", r"b\.?\s?sc\.?", r"b\.?\s?eng\.?", r"b\.?\s?tech\.?", r"b\.?\s?com\.?", r"bba",
         r"undergraduate degree"],
        [r"BS", r"BA", r"B\.S\.", r"B\.A\."]),
    2: ([r"master(?:'s|’s|s)", r"master\s+(?:degree|of)", r"m\.?\s?sc\.?", r"m\.?\s?eng\.?", r"m\.?\s?com\.?", r"mba",
         r"postgraduate degree"],
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


class _AIEvidence(BaseModel):
    achievement: str     # its id from ACHIEVEMENTS
    words: str           # the exact words of that achievement that say it
    relation: Literal["same", "does", "related"]


class _AIRequirement(BaseModel):
    requirement: str
    must_have: bool
    category: bool       # a family of tools/methods ("BI tools", "cloud platforms"), not one of them
    searchable: bool     # a recruiter would type it in an ATS search (not a trait, the job title, years...)
    cv_terms: list[_AITerm]
    evidence: list[_AIEvidence]


class _AIResult(BaseModel):
    requirements: list[_AIRequirement]


AI_INSTRUCTIONS = """You compare a job description with a candidate's CV terms, the way an ATS and a recruiter would.
1. List the 10 to 25 most important requirements of the job. Each one is the KEYWORD a recruiter would type in an
   ATS search, 1 to 3 words, as the job words it:
   - the specific thing only, without generic words around it: "Python skills" -> "Python", "experience with
     Salesforce" -> "Salesforce", "reporting automation tools" -> "reporting automation". Drop words like skills,
     experience, knowledge, proficiency, development, tools, ability when the rest names the thing on its own;
     keep them when it doesn't ("web development", "BI tools").
   - kinds: tools, technologies, methods, domains (industries, business areas), certifications, degrees (with the
     level: "Bachelor's degree in a quantitative field"), spoken languages (the language name: "German"), and
     concrete soft skills (stakeholder management, presenting to executives).
   - NOT requirements: the job's own title or seniority ("Data Analyst", "Senior"), years of experience, and vague
     traits ("curiosity", "hands-on experience", "analytical approach", "fast learner", "team player").
   must_have = explicitly required. category = the requirement names a family of tools or methods rather than one
   ("BI tools", "cloud platforms", "CRM systems", "statistical methods"). searchable = false for anything a recruiter
   would not type in an ATS search (a personality trait, an attitude, the job title, years of experience); those
   are dropped, so when in doubt about a trait, false.
2. For each requirement, cv_terms = the terms from the CV TERMS list below that cover it, copied exactly, each
   with its relation:
   - "same": the job's word itself, a form of it or another spelling (dashboards -> "dashboard", "Power BI" ->
     "PowerBI", a spoken language -> that language)
   - "synonym": another name for exactly the same thing ("ML" -> "Machine Learning", "CRM" -> "Customer
     Relationship Management")
   - "instance": a concrete tool or method that IS one of what the job names ("cloud platforms" -> "AWS",
     "BI tools" -> "Looker", "version control" -> "Git")
   - "related": anything else: a broader field, an adjacent activity, a generic term, a different tool of the same
     kind ("Looker" is not "Tableau"). These are dropped.
   Use "synonym" or "instance" only when you are sure; if you hesitate, it is "related". Empty list if nothing in
   the CV covers it.
3. For each requirement, evidence = the ACHIEVEMENTS below (and job titles, marked [title]) that show it: the
   achievement's id, the exact words of it that show it (copied, as short as possible), and the relation:
   - "same": those words are the requirement or a form of it
   - "does": the achievement IS doing that work, in other words. A recruiter reading it would say "yes, this person
     has done <requirement>": "trained 4 new hires on the till" does staff training; "planned the weekly delivery
     routes for 9 vans" does route planning; "kept the monthly spend under $5K" does budget management.
   - "related": it only touches the topic, or would need a claim it doesn't make (a different tool, a bigger scope,
     leading instead of helping). These are dropped.
   Read what the achievement did, not only its keywords. Empty list if none shows it.

CV TERMS: {terms}

ACHIEVEMENTS:
{achievements}"""


class _CV:
    """Snapshot of the master CV used for lookups."""

    def __init__(self):
        self.cv = master_cv.get_master()
        self.bullets = [b for e in self.cv.bullet_owners() for b in e.bullets]  # experiences and projects
        self.skills = [i for g in self.cv.skills for i in g.items]
        self.education = [" ".join(x for x in (e.degree, e.school, e.details) if x) for e in self.cv.education]
        # job titles are on the CV too: "Customer Support Lead" says "customer support"
        self.roles = [o.role.strip() for o in self.cv.bullet_owners() if (o.role or "").strip()]
        # spoken languages count like any keyword: a job asking for German is met by "German (Fluent)"
        self.languages = [master_cv.language_name(x) for x in self.cv.languages if master_cv.language_name(x)]
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

    def in_languages(self, term: str) -> bool:
        pattern = master_cv.keyword_pattern(term)
        return any(pattern.fullmatch(lang) or pattern.search(lang) for lang in self.languages)

    def in_roles(self, term: str) -> bool:
        pattern = master_cv.keyword_pattern(term)
        return any(pattern.search(r) for r in self.roles)

    def has(self, term: str) -> bool:
        return bool(self.in_bullets(term) or self.in_skills(term) or self.in_education(term)
                    or self.in_languages(term) or self.in_roles(term))

    def other_texts(self) -> list[str]:
        """Everything on the CV besides the achievements: skills, degrees, languages, job titles."""
        return self.skills + self.education + self.languages + self.roles

    def achievements(self) -> tuple[str, dict[str, str]]:
        """The listing the AI reads ([id] text), and short id -> full text of every wording and job title."""
        texts, lines = {}, []
        for b in self.bullets:
            for i, w in enumerate(b.wordings()):
                key = b.id[:8] if i == 0 else f"{b.id[:8]}.{i}"
                texts[key] = f"{w.lead} {w.text}".strip()
                lines.append(f"[{key}] {texts[key]}")
        for i, role in enumerate(self.roles):
            texts[f"title{i}"] = role
            lines.append(f"[title{i}] [title] {role}")
        return "\n".join(lines) or "(none)", texts

    def literal(self, jd_term: str) -> bool:
        """The job's own word (or a form of each of its words) is written in one bullet, skill or degree."""
        texts = [f"{w.lead} {w.text}" for b in self.bullets for w in b.wordings()] + self.other_texts()
        return any(master_cv.written_in(jd_term, t) for t in texts)

    def written_forms(self, jd_term: str) -> list[str]:
        """How the CV writes the job's term, if it does ("joins" -> ["joined"]): the words to search the CV for."""
        for text in [f"{w.lead} {w.text}" for b in self.bullets for w in b.wordings()] + self.other_texts():
            if master_cv.written_in(jd_term, text):
                if master_cv.keyword_pattern(jd_term).search(text):
                    return [jd_term]
                spans = [x for x in master_cv.written_spans(jd_term, text) if not master_cv.is_generic(x)]
                return spans[:1] or [jd_term]
        return []

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
        return master_cv._clean_keywords([t for t in candidates if self.has(t) and not master_cv.is_generic(t)]
                                         + degrees + self.languages)


_FAMILY = re.compile(r"\b(tools?|platforms?|systems?|software|suites?|databases?|frameworks?|libraries|services|"
                     r"stacks?|methods|methodologies|technologies|providers?|languages)\b", re.IGNORECASE)


def _is_family(requirement: str) -> bool:
    """Only a named family ("BI tools", "cloud platforms") is met by one of its members, not "email marketing"."""
    return bool(_FAMILY.search(requirement))


def _quote(words: str, text: str | None) -> str:
    """The cited words as the CV writes them, if they really are in that text (at most 8 words), else ""."""
    words = (words or "").strip().strip(".,;:\"'“”")
    if not text or not words or len(words.split()) > 8:
        return ""
    i = text.lower().find(words.lower())
    return text[i:i + len(words)] if i >= 0 else ""


def _initials(term: str) -> str:
    return "".join(w[0] for w in re.findall(r"[A-Za-z]+", term)).lower()


def match_job_description(text: str, use_ai: bool = True) -> dict:
    cv = _CV()
    found = lambda term: bool(master_cv.keyword_pattern(term).search(text))
    matches: dict[str, Match] = {}

    def add(jd_term: str, keywords: list[str], source: Source, must_have: bool = False, category: bool = False):
        jd_term = master_cv.strip_punctuation(jd_term)
        if not jd_term or master_cv.is_generic(jd_term):
            return
        key = re.sub(r"[\s\-]+", "", jd_term.lower())  # "Retrieval-Augmented" == "Retrieval Augmented" == "PowerBI"
        if key in matches:
            m = matches[key]
            if not (m.source == "exact" and m.keywords):  # written as in the job: other terms would only add noise
                m.keywords = master_cv._clean_keywords(m.keywords + keywords)
            m.must_have = m.must_have or must_have
            m.category = m.category or category
        else:
            matches[key] = Match(jd_term=jd_term, keywords=master_cv._clean_keywords(keywords),
                                 source=source, must_have=must_have, category=category)

    # 1. exact: known keywords and the user's spoken languages written in the job description
    for kw in master_cv.keyword_vocabulary() + cv.languages:
        if found(kw):
            add(kw, [kw] if cv.has(kw) else [], "exact")

    # 2. synonyms: the job description's wording -> the wording used in the CV
    for group in cv.cv.synonyms:
        in_cv = [t for t in group if cv.has(t)]
        if not in_cv:  # nothing of it in this CV: the AI pass reports real gaps, a synonym list would add noise
            continue
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
        listing, said = cv.achievements()
        try:
            result = llm.structured(AI_INSTRUCTIONS.format(terms=", ".join(terms) or "(none)", achievements=listing),
                                    text, _AIResult)
            ai_used = True
            for r in result.requirements:
                if not r.searchable:
                    continue
                kws = [allowed[t.term.strip().lower()] for t in r.cv_terms
                       if t.relation != "related" and t.term.strip().lower() in allowed]
                # What an achievement says in its own words ("led a team of 6 agents"), checked to be really there:
                # it covers the requirement even when no keyword is tagged
                kws += [w for e in r.evidence if e.relation != "related" and (w := _quote(e.words, said.get(e.achievement)))]
                add(r.requirement, kws, "ai", r.must_have, _is_family(r.requirement) and r.category)
        except llm.LLMUnavailable as e:
            ai_error = str(e)

    for m in matches.values():
        if not m.keywords and (written := cv.written_forms(m.jd_term)):
            m.keywords = written  # the job's word is in the CV in another form ("joins" -> "joined"): covered
        by_degree = False
        if not m.keywords or (degree_mentions(m.jd_term) and cv.degree_for(m.jd_term)):
            # "Master's degree in a quantitative field", "BA/BSc": met by the user's degree at that level or above
            degree = cv.degree_for(m.jd_term)
            m.keywords, by_degree = ([degree], True) if degree else (m.keywords, False)
        carrying = [b for b in cv.bullets if any(master_cv.in_text(k, w) for k in m.keywords for w in b.wordings())]
        m.bullets = len(carrying)
        m.in_skills = any(cv.in_skills(k) for k in m.keywords)
        m.in_education = any(cv.in_education(k) for k in m.keywords)
        m.in_languages = any(cv.in_languages(k) for k in m.keywords)
        if m.keywords:
            # A family ("BI tools") is met by naming one of its tools: a recruiter reads "Tableau", nothing to reword.
            m.literal = (by_degree or m.category or m.in_languages
                         or (m.source == "exact" and m.jd_term in m.keywords) or cv.literal(m.jd_term))
            m.reword = not m.literal
            m.reword_bullets = [{"id": b.id, "text": f"{b.lead} {b.text}".strip()} for b in carrying[:3]] if m.reword else []

    # With AI, drop word-level rows that an AI requirement already shows ("LLM" inside "large language models"),
    # unless that requirement needs rewording while the word-level row is written as in the job.
    # Only when it is the same requirement in other words (its acronym, a synonym, or written inside it): "HubSpot"
    # stays its own requirement next to "email marketing", even though HubSpot is how the CV covers both.
    group_of = {t.lower(): i for i, g in enumerate(cv.cv.synonyms) for t in g}

    def same_requirement(word: str, ai_term: str) -> bool:
        w, a = word.lower(), ai_term.lower()
        return (_initials(ai_term) == re.sub(r"[^a-z]", "", w) or master_cv.written_in(word, ai_term)
                or (w in group_of and group_of.get(a) == group_of[w]))
    ai_rows = [m for m in matches.values() if m.source == "ai" and m.keywords]
    for key, m in list(matches.items()):
        if m.source != "ai" and m.keywords and any(
                {k.lower() for k in m.keywords} <= {k.lower() for k in a.keywords} and (a.literal or not m.literal)
                and same_requirement(m.jd_term, a.jd_term) for a in ai_rows):
            del matches[key]

    # The job names it twice ("Machine Learning" and "ML") and one of its wordings is written: nothing to reword.
    literal_sets = [{k.lower() for k in m.keywords} for m in matches.values() if m.literal]
    for key, m in list(matches.items()):
        if m.reword and m.source == "synonym" and any({k.lower() for k in m.keywords} <= s for s in literal_sets):
            del matches[key]

    # A gap that is part of, or a synonym of, a requirement written as in the job is not a real gap ("Agents" vs
    # "AI Agents"). Covered with other words doesn't count: "retail" stays a gap next to "retail performance".
    covered = [m.jd_term.lower() for m in matches.values() if m.keywords and m.literal]
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
    same = re.sub(r"[\W_]+", "", word.lower())  # never offer the word itself back ("Master's" vs "Master’s")
    return [lower[h] for h in hits if re.sub(r"[\W_]+", "", h) != same][:limit]
