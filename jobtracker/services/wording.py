"""AI-drafted wordings of an achievement. Drafts are never saved here: the user
accepts them (cv.variant.add) after reading them.

A variant exists to put a job's word in the CV, so it is built AROUND the target keyword and changes as little of
the achievement as it can (guides/bullets.md).

Guards applied in code, whatever the model does:
  - a draft with a number that is not in the original (or in the user's facts) is rejected;
  - a draft that uses none of the target keywords (verbatim or a form of the word) is rejected; when that
    happens to every draft, the model gets one more try with its misses quoted back;
  - a draft that writes a keyword's word more often than the original did is rejected (bolted on);
  - with target keywords, a draft must cite the words of the original the keyword names (`evidence`), and they
    must really be in it: a keyword with nothing behind it is not true there; then a second AI call checks that
    the keyword honestly names those words and that the sentence reads naturally (fact_check), and drops the rest;
  - a draft identical to an existing wording is dropped;
  - keywords are only those literally written in the draft;
  - offered 1-to-1 swaps are kept only when their phrase is in the draft and no option adds a number.
"""
from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel

from .. import guides
from ..textstyle import plain_dashes
from . import llm, master_cv


class Swap(BaseModel):
    phrase: str          # words in the draft, exactly as written
    options: list[str]   # 1-to-1 replacements that keep the sentence's structure and facts


class _Draft(BaseModel):
    evidence: str        # the exact words of the original that the keyword names (grounds it in a fact)
    lead: str
    text: str
    edit: Literal["swap", "restructure"]   # swap: only the keyword's phrase changed; restructure: reworded
    swaps: list[Swap]


class _Drafts(BaseModel):
    drafts: list[_Draft]


INSTRUCTIONS = """You write UP TO {count} alternative wordings of ONE achievement of a CV, each built AROUND the TARGET
KEYWORDS so that an ATS searching for the job's word finds it. One natural draft is a complete answer: write
another only if it is just as natural and genuinely different.
Strict rules:
- Every draft MUST contain the target keyword written verbatim or as a close form of the same word
  (analysis/analyzed, dashboard/dashboards, optimize/optimization). A draft without it is worthless: never
  return one. With several target keywords, fit all of them where truthful; each draft needs at least one.
- Change as little as possible. Draft 1 keeps the ORIGINAL word for word and replaces only the phrase the keyword
  stands for (edit = "swap"). Rework the sentence (edit = "restructure") only when the keyword cannot fit
  otherwise, or to give a genuinely different option in later drafts. Every draft must read naturally, as a
  recruiter would write it: never bolt the keyword on next to words that already say it ("a CRM pipeline using
  CRM", "managed payroll with payroll processing"). Fewer drafts are better than awkward ones.
- evidence: copy EXACTLY the words of the ORIGINAL (or USER FACTS) that the keyword names: the thing it is
  another word for, a concrete instance of, or an activity that clearly is it ("Salesforce" for CRM, "AWS" for
  cloud, "kept the monthly reports up to date" for reporting). If no words of the original are that thing, the
  keyword isn't true here: return an empty list.
- Same facts only. Do not add tools, numbers, team sizes, results, scope or responsibilities that are not in
  the ORIGINAL or in USER FACTS. A keyword is allowed only where it truthfully describes what the original says
  ("Salesforce" can become "Salesforce CRM"; adding a tool, method or certification the original never mentions is
  NOT allowed). If no truthful draft can use the keyword, return an empty list.
- Fit the keyword to the sentence's grammar: singular or plural as needed ("vector databases" -> "as the vector
  database"), and never a plural noun as a modifier ("a sales report pipeline", not "a sales reports pipeline").
- One sentence, strong action verb, similar length (at most 20% longer). Keep the bold label (lead) as it is
  unless it contradicts the new wording; "" if there is none.
- Each draft must differ from the original and from the other existing wordings.
- swaps: other words of the draft (NOT the target keyword) that could be replaced 1-to-1 without changing the
  sentence's structure or facts, e.g. a verb ("Built" -> "Developed", "Designed") or a near synonym. `phrase` is
  copied exactly from the draft; 2-4 `options`. At most 3 swaps per draft; [] if none fits.

=== ACHIEVEMENT GUIDE ===
"""

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def numbers(text: str) -> set[str]:
    return set(_NUMBER.findall(text or ""))


def clean_swaps(text: str, swaps: list[Swap], allowed_numbers: set[str], protected: list[str] = ()) -> list[dict]:
    """Swaps worth offering: the phrase is in the text and isn't a keyword; options are new and add no number."""
    out, used = [], set()
    for s in swaps:
        phrase = plain_dashes(s.phrase.strip())
        if not phrase or phrase not in text or phrase.lower() in used:
            continue
        if any(p.lower() in phrase.lower() or phrase.lower() in p.lower() for p in protected):
            continue
        options = []
        for o in s.options:
            o = plain_dashes(o.strip())
            if o and o.lower() != phrase.lower() and o.lower() not in {x.lower() for x in options} \
                    and numbers(o) <= allowed_numbers:
                options.append(o)
        if options:
            used.add(phrase.lower())
            out.append({"phrase": phrase, "options": options[:4]})
    return out[:3]


def repeated(full: str, keywords: list[str], before: str) -> list[str]:
    """Words of a keyword written more often in the draft than in the text it came from: the keyword was bolted
    onto a sentence that already said it ("a forecasting model … using demand forecasting"). Same word only (a
    plural counts): "experimental design … experimentation process" is fine."""
    out = []
    for k in keywords:
        for w in re.findall(r"[A-Za-z0-9][A-Za-z0-9+#]*", k):
            if w.lower() in master_cv.FILLER_WORDS or w in out:
                continue
            pattern = master_cv.keyword_pattern(w)
            if len(pattern.findall(full)) > max(len(pattern.findall(before or "")), 1):
                out.append(w)
    return out


class _Verdict(BaseModel):
    draft: int      # 1-based
    true: bool
    natural: bool   # reads like a line a good recruiter would write (no redundant or bolted-on wording)
    why: str


class _Verdicts(BaseModel):
    verdicts: list[_Verdict]


CHECK = """You fact-check CV wordings written to contain a job's word (KEYWORD). The writer cited the words of the
ORIGINAL achievement that the keyword is supposed to name (EVIDENCE). Judge like a fair recruiter, not a pedant.
Answer `true` when a recruiter would accept the keyword as describing what the original says:
- the same thing, another spelling or a synonym;
- a category the cited thing belongs to ("Salesforce" -> CRM, "AWS" -> cloud platforms);
- the activity the original clearly did ("built and maintained the sales reports" -> reporting; "ran split tests on
  the landing page" -> A/B testing). Building, maintaining, creating or running something all count as working on it.
Answer `false` only when the draft would mislead: a different tool or skill (Excel is not SQL), something the
original never did (using a tool is not administering it), a stronger claim than the original (helped -> led,
a user -> an expert), or a named tool, system, platform or certification the original doesn't name. When the
keyword is a kind of software or system (a CRM, an ERP, a ticketing system, a data warehouse), the original must
name a product of that kind; doing the work by hand or with unnamed tools ("contacted customers", "ordered from
suppliers") does not count.
natural: false when the sentence reads clumsily: a redundant pair ("managed inventory management", "X dashboards
for data visualization dashboards"), the keyword stuck in as a label or modifier where it doesn't belong, or
broken grammar. why: a few words on what is wrong ("" if nothing)."""


def fact_check(original: str, keywords: list[str], drafts: list[tuple[str, str]]) -> list[_Verdict]:
    """A second, sceptical reader for drafts carrying a job's word: [(evidence, full text)] -> verdicts."""
    listing = "\n".join(f"DRAFT {i}: {text}\nEVIDENCE {i}: {ev}" for i, (ev, text) in enumerate(drafts, 1))
    out = llm.structured(CHECK, f"ORIGINAL: {original}\nKEYWORD: {', '.join(keywords)}\n\n{listing}", _Verdicts)
    by = {v.draft: v for v in out.verdicts}
    return [by.get(i, _Verdict(draft=i, true=False, natural=False, why="not checked"))
            for i in range(1, len(drafts) + 1)]


def _grounded(evidence: str, source: str) -> bool:
    """The model's evidence is words of the original (quotes and case aside), not something it made up."""
    norm = lambda t: re.sub(r"\s+", " ", re.sub(r"[“”\"']", "", t or "")).strip().lower()  # noqa: E731
    return bool(norm(evidence)) and norm(evidence) in norm(source)


def describe(full: str, keywords: list[str]) -> dict:
    """The keywords a text uses (`covers`), where (`spans`) and its known keyword tags."""
    covers = [k for k in keywords if master_cv.written_in(k, full)]
    spans = list(dict.fromkeys(s for k in covers for s in master_cv.written_spans(k, full)))
    probe = master_cv.Bullet(text=full)
    tags = master_cv._clean_keywords([k for k in covers if master_cv.in_text(k, probe)]
                                     + master_cv.detect_keywords(full))
    return {"covers": covers, "spans": spans, "keywords": tags}


def _with_forms(keyword: str) -> str:
    """'vector databases' -> 'vector databases (or "vector database")': the model may pick the form that fits."""
    last = keyword.rsplit(" ", 1)[-1]
    if len(last) > 3 and last.endswith("s") and not last.endswith(("ss", "us", "is")) and last[:-1].isalpha():
        return f'{keyword} (or "{keyword[:-1]}")'
    return keyword


def _prompt(bullet, keywords: list[str], facts: str) -> str:
    existing = "\n".join(f"- {w.lead} {w.text}".strip() for w in bullet.variants) or "(none)"
    true_facts = "; ".join(x for x in (bullet.parts.as_text() if bullet.parts else "", facts) if x) or "(none)"
    return (f"ORIGINAL: {bullet.lead} {bullet.text}".strip()
            + f"\nOTHER EXISTING WORDINGS:\n{existing}"
            + f"\nTARGET KEYWORDS: {', '.join(map(_with_forms, keywords)) or '(none: just vary the wording)'}"
            + f"\nUSER FACTS (true, may be used): {true_facts}")


def draft_variants(bullet_id: str, keywords: list[str], count: int = 3, facts: str = "") -> dict:
    cv = master_cv.get_master()
    bullet = next((b for e in cv.bullet_owners() for b in e.bullets if b.id == bullet_id), None)
    if bullet is None:
        raise master_cv.NotFound(f"Achievement {bullet_id!r} not found")
    return draft_for(bullet, keywords, count, facts)


def draft_for(bullet, keywords: list[str], count: int = 3, facts: str = "", instructions: str = "") -> dict:
    """Drafts for an achievement (saved or not). `instructions` replaces INSTRUCTIONS (the drafting eval compares
    versions of the prompt with it)."""
    bullet_id = bullet.id
    keywords = [k.strip() for k in keywords if k.strip()]
    instructions = (instructions or INSTRUCTIONS).format(count=count) + guides.read("bullets")
    prompt = _prompt(bullet, keywords, facts)
    allowed = numbers(f"{bullet.lead} {bullet.text} {facts} {bullet.parts.as_text() if bullet.parts else ''}")
    seen = {f"{w.lead} {w.text}".strip().lower() for w in bullet.wordings()}
    original = f"{bullet.lead} {bullet.text}".strip()
    parts_text = bullet.parts.as_text() if getattr(bullet, "parts", None) else ""

    drafts, rejected = [], []

    def review(result: _Drafts) -> list[str]:
        """Sort the model's drafts into kept and rejected; returns the fixable misses (no keyword, bolted on)."""
        missed = []
        for d in result.drafts:
            d.lead, d.text = plain_dashes(d.lead), plain_dashes(d.text)
            full = f"{d.lead} {d.text}".strip()
            invented = sorted(numbers(full) - allowed)
            found = describe(full, keywords)
            if invented:
                rejected.append({"text": full, "reason": f"adds numbers not in the original: {', '.join(invented)}"})
            elif full.lower() in seen:
                rejected.append({"text": full, "reason": "same as an existing wording"})
            elif keywords and not found["covers"]:
                rejected.append({"text": full, "reason": f"doesn't use {' or '.join(map(repr, keywords))}"})
                missed.append(full)
            elif keywords and not _grounded(d.evidence, f"{original} {facts} {parts_text}"):
                rejected.append({"text": full, "reason": "no words of the original say it"
                                 + (f" (cited “{d.evidence}”)" if d.evidence.strip() else "")})
            elif twice := repeated(full, keywords, original):
                rejected.append({"text": full, "reason": f"repeats {', '.join(map(repr, twice))}: the keyword is"
                                                         " bolted on, not worked in"})
                missed.append(f"{full}  <- repeats {', '.join(twice)}: replace the phrase that already says it")
            else:
                seen.add(full.lower())
                drafts.append({"lead": d.lead, "text": d.text, "edit": d.edit, "evidence": d.evidence, **found,
                               "swaps": clean_swaps(d.text, d.swaps, allowed, found["spans"])})
        return missed

    def check(found: list[dict]) -> tuple[list[dict], list[str]]:
        """A second reader: is the job's word honestly what the original says, and does it read naturally?"""
        if not (keywords and found):
            return found, []
        verdicts = fact_check(original, keywords, [(d["evidence"], f"{d['lead']} {d['text']}".strip()) for d in found])
        kept, notes = [], []
        for d, v in zip(found, verdicts, strict=True):
            full = f"{d['lead']} {d['text']}".strip()
            if v.true and v.natural:
                kept.append(d)
                continue
            reason = f"not true here: {v.why}" if not v.true else f"reads clumsily: {v.why}"
            rejected.append({"text": full, "reason": reason})
            if v.true:  # true but clumsy: worth another try
                notes.append(f"{full}  <- {reason}")
        return kept, notes

    missed = review(llm.structured(instructions, prompt, _Drafts))
    drafts[:], clumsy = check(list(drafts))
    if not drafts and (missed or clumsy):  # one more try, told exactly what was wrong
        retry = (prompt + "\n\nYOUR PREVIOUS DRAFTS WERE THROWN AWAY:\n" + "\n".join(f"- {m}" for m in missed + clumsy)
                 + f"\nWrite drafts that contain {' / '.join(keywords)} (or a form of the word) and read naturally: if"
                   " inserting the word next to the existing phrase is clumsy, rewrite that clause around it. A category"
                   " reads well with its product in parentheses or as an apposition (\"a CRM (HubSpot)\", \"tracked"
                   " orders in Shopify, our e-commerce platform\"). Return [] if it can't be said truthfully.")
        review(llm.structured(instructions, retry, _Drafts))
        drafts[:], _ = check(list(drafts))
    # Most keywords first, then the smallest change
    drafts.sort(key=lambda d: (-len(d["covers"]), d["edit"] != "swap"))
    return {"bullet_id": bullet_id, "original": f"{bullet.lead} {bullet.text}".strip(),
            "keywords": keywords, "drafts": drafts, "rejected": rejected}
