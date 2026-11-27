"""Building achievements: from the user's What / How / Result facts (compose), and finding where a job's words fit
in the achievements already written (place). Nothing is saved here: the user keeps drafts with cv.bullet.add /
cv.variant.add.

Same guards as the variant drafts (services/wording.py): no number that the user didn't give, keywords counted
only when written, swaps only when their phrase is in the text.
"""
from __future__ import annotations

import contextvars
from concurrent.futures import ThreadPoolExecutor
from typing import Literal

from pydantic import BaseModel

from .. import guides
from ..models import BulletParts
from ..textstyle import plain_dashes
from . import llm, master_cv, wording


class _Composed(BaseModel):
    order: Literal["how-led", "result-first", "compact"]
    lead: str
    text: str
    swaps: list[wording.Swap]


class _Compositions(BaseModel):
    drafts: list[_Composed]


COMPOSE = """You write ONE achievement (bullet point) of a CV from the user's What / How / Result facts, as
{count} drafts: order {orders}. "how-led" is the standard sentence of the guide, "result-first" opens on the
measurable result, "compact" is the shortest, still complete. Follow the achievement guide below.
Strict rules:
- Only the user's facts. Do not add tools, numbers, team sizes, results, scope or responsibilities they didn't
  give. Rephrase their words into strong CV language, but keep every number exactly as written.
- A missing part stays missing (no Result: end on the goal if given, else after the How).
- KEYWORDS (the job's words): the user picked them as true for this achievement. Write each one verbatim (or a
  close form of the word) wherever the facts are that thing, an instance of it or the activity itself ("HubSpot"
  is a CRM: "a CRM (HubSpot) workflow"). Skip one only if nothing in the facts relates to it; never invent a fact.
- Lead: "" unless LEAD is given.
- swaps: words of the draft that could be replaced 1-to-1 without changing its structure or facts (a verb, a
  near synonym), never a keyword; `phrase` copied exactly from the draft, 2-4 `options`, at most 3 swaps.
- The language of the user's facts.

=== ACHIEVEMENT GUIDE ===
"""


def compose(what: str, how: str = "", result: str = "", lead: str = "", keywords: list[str] = (),
            owner_id: str = "") -> dict:
    parts = BulletParts(what=what.strip(), how=how.strip(), result=result.strip())
    if not parts.what:
        raise ValueError("Say what you did (the What) first.")
    keywords = [k.strip() for k in keywords if k.strip()]
    context = ""
    if owner_id:
        cv = master_cv.get_master()
        owner = next((o for o in cv.bullet_owners() if o.id == owner_id), None)
        if owner is None:
            raise master_cv.NotFound(f"Experience or project {owner_id!r} not found")
        name = getattr(owner, "company", "") or getattr(owner, "name", "")
        context = f"\nWHERE (context only, don't repeat it): {owner.role} at {name}".rstrip()
    prompt = (f"WHAT: {parts.what}\nHOW: {parts.how or '(not given)'}\nRESULT: {parts.result or '(not given)'}"
              f"\nLEAD: {lead or '(none)'}\nKEYWORDS: {', '.join(keywords) or '(none)'}{context}")
    # Opening on the result only reads well when it is a number ("Cut stock-outs by 12%…"), not a goal
    orders = ["how-led", "result-first", "compact"] if wording.numbers(parts.result) else ["how-led", "compact"]
    instructions = (COMPOSE.format(count=len(orders), orders=", ".join(f'"{o}"' for o in orders))
                    + guides.read("bullets"))
    out = llm.structured(instructions, prompt, _Compositions)
    if keywords and not any(wording.describe(f"{d.lead} {d.text}", keywords)["covers"] for d in out.drafts):
        # the user picked these words: one more try that uses them where the facts allow
        out = llm.structured(instructions, prompt + f"\n\nNONE OF YOUR DRAFTS USED {' / '.join(keywords)}. Use them "
                             "wherever the facts are that thing or an instance of it.", _Compositions)

    allowed = wording.numbers(f"{parts.as_text()} {lead}")
    drafts, rejected, seen = [], [], set()
    for d in out.drafts:
        d.lead, d.text = plain_dashes(d.lead), plain_dashes(d.text)
        full = f"{d.lead} {d.text}".strip()
        invented = sorted(wording.numbers(full) - allowed)
        if invented:
            rejected.append({"text": full, "reason": f"adds numbers you didn't give: {', '.join(invented)}"})
        elif d.order not in orders or full.lower() in seen:
            continue
        elif twice := wording.repeated(full, keywords, parts.as_text()):
            rejected.append({"text": full, "reason": f"repeats {', '.join(map(repr, twice))}: the keyword is bolted on"})
        else:
            seen.add(full.lower())
            found = wording.describe(full, keywords)
            drafts.append({"order": d.order, "lead": d.lead, "text": d.text, **found,
                           "swaps": wording.clean_swaps(d.text, d.swaps, allowed, found["spans"])})
    return {"parts": parts.model_dump(), "keywords": keywords, "owner_id": owner_id,
            "drafts": drafts, "rejected": rejected}


# ------------------------------------------------------------------ place

class _Slot(BaseModel):
    keyword: str
    bullet_id: str   # "" when no achievement truthfully says it
    why: str


class _Slots(BaseModel):
    slots: list[_Slot]


PLACE = """You find, for each of a job's KEYWORDS, the ONE achievement of the candidate's CV where it can be written
truthfully by changing a few words: the achievement already describes that thing with other words (a synonym, a
concrete instance, the same activity). Rules:
- bullet_id: an id from ACHIEVEMENTS, or "" when no achievement truly says it (then it is a real gap). Never pick
  an achievement only because it is in the same field or uses a different tool of the same kind (Excel for SQL).
  An achievement that does the thing with other words IS a slot: "kept the payroll spreadsheets of 40 staff up to
  date" fits "payroll", "Excel" and "record keeping".
- Prefer an achievement where the change is smallest. Put keywords in the same achievement when they naturally
  belong together there (at most 3 per achievement).
- why: one short line (what in the achievement the keyword describes, or why there is no slot).
Return one slot per keyword, in the order given."""


def place(keywords: list[str], max_per_bullet: int = 3) -> dict:
    keywords = list(dict.fromkeys(k.strip() for k in keywords if k.strip()))
    if not keywords:
        raise ValueError("Pick at least one keyword.")
    cv = master_cv.get_master()
    owners = {b.id: (o, b) for o in cv.bullet_owners() for b in o.bullets}
    listing = "\n".join(
        f"[{b.id}] ({getattr(o, 'company', '') or getattr(o, 'name', '')}) {b.lead} {b.text}".strip()
        + (f" | facts: {b.parts.as_text()}" if b.parts else "")
        for o, b in owners.values())
    out = llm.structured(PLACE, f"KEYWORDS: {', '.join(keywords)}\n\nACHIEVEMENTS:\n{listing}", _Slots)

    groups: dict[str, list[str]] = {}
    why: dict[str, str] = {}
    for s in out.slots:
        k = next((x for x in keywords if x.lower() == s.keyword.strip().lower()), None)
        if k is None or k in why:
            continue
        why[k] = s.why
        if s.bullet_id in owners and len(groups.get(s.bullet_id, [])) < max_per_bullet:
            groups.setdefault(s.bullet_id, []).append(k)

    def draft(item):
        bullet_id, kws = item
        return wording.draft_variants(bullet_id, kws, count=2)

    with ThreadPoolExecutor(max_workers=min(4, len(groups) or 1)) as pool:
        results = list(pool.map(lambda item: contextvars.copy_context().run(draft, item), groups.items()))

    placements, placed = [], set()
    for (bullet_id, kws), r in zip(groups.items(), results, strict=True):
        if not r["drafts"]:
            continue
        o, _ = owners[bullet_id]
        covered = [k for k in kws if any(k in d["covers"] for d in r["drafts"])]  # only what the drafts say
        placed.update(covered)
        placements.append({"bullet_id": bullet_id, "owner": getattr(o, "company", "") or getattr(o, "name", ""),
                           "original": r["original"], "keywords": covered,
                           "why": "; ".join(why[k] for k in covered if why.get(k)),
                           "drafts": r["drafts"]})
    no_slot = [{"keyword": k, "why": why.get(k, "")} for k in keywords if k not in placed]
    return {"placements": placements, "no_slot": no_slot}
