"""AI-drafted wordings of an achievement. Drafts are never saved here: the user
accepts them (cv.variant.add) after reading them.

Guards applied in code, whatever the model does:
  - a draft with a number that is not in the original (or in the user's facts) is rejected;
  - a draft identical to an existing wording is dropped;
  - keywords are only those literally written in the draft.
"""
from __future__ import annotations

import re

from pydantic import BaseModel

from .. import guides
from ..textstyle import plain_dashes
from . import llm, master_cv


class _Draft(BaseModel):
    lead: str
    text: str


class _Drafts(BaseModel):
    drafts: list[_Draft]


INSTRUCTIONS = """You rewrite ONE achievement of a CV into {count} alternative wordings.
Strict rules:
- Same facts only. Do not add tools, numbers, team sizes, results, scope or responsibilities that are not in
  the ORIGINAL or in USER FACTS. When unsure, leave it out.
- Work the TARGET KEYWORDS in only where they truthfully describe what the original says (e.g. "GPT-based"
  can become "LLM-based"; adding "Kubernetes" when it is not mentioned is NOT allowed). Skip a keyword rather
  than stretch the truth. Different drafts may emphasise different keywords.
- One sentence, strong action verb, similar length (at most 20% longer). Keep the bold label (lead) as it is
  unless it contradicts the new wording; "" if there is none.
- Each draft must differ from the original and from the other existing wordings.

=== CV WRITING GUIDE (follow its achievement rules) ===
"""

_NUMBER = re.compile(r"\d+(?:[.,]\d+)?")


def draft_variants(bullet_id: str, keywords: list[str], count: int = 3, facts: str = "") -> dict:
    cv = master_cv.get_master()
    bullet = next((b for e in cv.bullet_owners() for b in e.bullets if b.id == bullet_id), None)
    if bullet is None:
        raise master_cv.NotFound(f"Achievement {bullet_id!r} not found")

    existing = "\n".join(f"- {w.lead} {w.text}".strip() for w in bullet.variants) or "(none)"
    prompt = (f"ORIGINAL: {bullet.lead} {bullet.text}".strip()
              + f"\nOTHER EXISTING WORDINGS:\n{existing}"
              + f"\nTARGET KEYWORDS: {', '.join(keywords) or '(none: just vary the wording)'}"
              + f"\nUSER FACTS (true, may be used): {facts or '(none)'}")
    result = llm.structured(INSTRUCTIONS.format(count=count) + guides.read("cv"), prompt, _Drafts)

    allowed_numbers = set(_NUMBER.findall(f"{bullet.lead} {bullet.text} {facts}"))
    seen = {f"{w.lead} {w.text}".strip().lower() for w in bullet.wordings()}
    drafts, rejected = [], []
    for d in result.drafts:
        d.lead, d.text = plain_dashes(d.lead), plain_dashes(d.text)
        full = f"{d.lead} {d.text}".strip()
        invented = [n for n in _NUMBER.findall(full) if n not in allowed_numbers]
        if invented:
            rejected.append({"text": full, "reason": f"adds numbers not in the original: {', '.join(invented)}"})
        elif full.lower() in seen:
            rejected.append({"text": full, "reason": "same as an existing wording"})
        else:
            seen.add(full.lower())
            probe = master_cv.Bullet(text=d.text, lead=d.lead)
            drafts.append({"lead": d.lead, "text": d.text,
                           "covers": [k for k in keywords if master_cv.in_text(k, probe)],
                           "keywords": master_cv._clean_keywords(
                               [k for k in keywords if master_cv.in_text(k, probe)]
                               + master_cv.detect_keywords(d.text, d.lead))})
    return {"bullet_id": bullet_id, "original": f"{bullet.lead} {bullet.text}".strip(),
            "drafts": drafts, "rejected": rejected}
