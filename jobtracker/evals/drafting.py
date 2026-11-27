"""`python -m jobtracker eval drafting`: how well variant drafts (services/wording.py) work in a job's word.

    eval drafting [--cases evals/local/drafting/cases.json] [--variant current --variant v2] [--repeat 2] --yes

Each case is an achievement + the job's word(s) + whether a truthful variant exists:
    {"id": "copilot-llm", "lead": "Copilot:", "text": "...", "keywords": ["LLM"], "expect": "draft" | "none"}
Every case runs with the real model (costs money: --yes), then an AI grader scores each kept draft:
natural (1-5: reads like a strong CV line, the word worked in, not bolted on), minimal (1-5: changes only what the
word needs), truthful (claims nothing the original doesn't support). The code guards (keyword written, no new
numbers, no repeated words) run as in the app; their rejections are counted.

Prompt versions to compare live in VARIANTS ("current" = the app's). Cases hold real CV text: they stay in
evals/local/ (git-ignored). Results: evals/local/drafting/runs/.
"""
from __future__ import annotations

import argparse
import contextvars
import json
import statistics
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from pydantic import BaseModel

from .. import config
from ..models import Bullet
from ..services import llm, wording

HOME = config.ROOT / "evals" / "local" / "drafting"

# Prompt versions to compare: "current" is the app's (wording.INSTRUCTIONS). Add a candidate here as a full
# INSTRUCTIONS string (with {count}), run both, keep the winner in wording.py. Tried and dropped (2026-11-27):
# a "swap, else rewrite the clause" rewrite scored lower on naturalness (3.44 vs 3.96) and truthfulness.
VARIANTS = {"current": ""}


class _Grade(BaseModel):
    draft: int          # 1-based number of the draft graded
    natural: int        # 1-5
    minimal: int        # 1-5
    truthful: bool
    note: str


class _Grades(BaseModel):
    grades: list[_Grade]


GRADER = """You grade alternative wordings of a CV achievement, written to contain a job's word (KEYWORD).
For each numbered DRAFT give:
- natural (1-5): 5 = reads like a strong line a good recruiter would write, the word worked into the sentence;
  3 = understandable but clunky; 1 = the word is bolted on or repeated ("a forecasting model using demand
  forecasting"), or the sentence is broken.
- minimal (1-5): 5 = only the phrase the word replaces changed; 3 = one clause rewritten; 1 = rewritten throughout
  with no need.
- truthful: false if the draft claims anything the ORIGINAL doesn't support (a new tool, scope, result, or the
  KEYWORD where it doesn't truly describe what the original says).
- note: a few words on the main problem ("" if none).
Be strict and consistent."""


def _grade(original: str, keywords: list[str], drafts: list[dict]) -> list[_Grade]:
    if not drafts:
        return []
    listing = "\n".join(f"DRAFT {i}: {(d['lead'] + ' ' + d['text']).strip()}" for i, d in enumerate(drafts, 1))
    out = llm.structured(GRADER, f"ORIGINAL: {original}\nKEYWORD: {', '.join(keywords)}\n\n{listing}", _Grades)
    return sorted(out.grades, key=lambda g: g.draft)[:len(drafts)]


def _run_one(case: dict, variant: str) -> dict:
    bullet = Bullet(text=case["text"], lead=case.get("lead", ""))
    r = wording.draft_for(bullet, case["keywords"], count=case.get("count", 2), instructions=VARIANTS[variant])
    grades = _grade(r["original"], case["keywords"], r["drafts"])
    return {"case": case["id"], "variant": variant, "expect": case["expect"], "original": r["original"],
            "drafts": [{**d, "grade": g.model_dump() if g else None}
                       for d, g in zip(r["drafts"], grades + [None] * (len(r["drafts"]) - len(grades)), strict=True)],
            "rejected": r["rejected"]}


def summarize(results: list[dict]) -> dict:
    def mean(xs):
        return round(statistics.mean(xs), 2) if xs else None
    want = [r for r in results if r["expect"] == "draft"]
    traps = [r for r in results if r["expect"] == "none"]
    graded = [d["grade"] for r in want for d in r["drafts"] if d["grade"]]
    best = [max(r["drafts"], key=lambda d: (d["grade"] or {}).get("natural", 0))["grade"]
            for r in want if r["drafts"] and r["drafts"][0]["grade"]]
    return {
        "runs": len(results),
        "found_a_draft": f"{sum(bool(r['drafts']) for r in want)}/{len(want)}",
        "refused_traps": f"{sum(not r['drafts'] for r in traps)}/{len(traps)}",
        "drafts_per_case": mean([len(r["drafts"]) for r in want]),
        "best_natural": mean([g["natural"] for g in best]),
        "all_natural": mean([g["natural"] for g in graded]),
        "share_natural_4plus": mean([g["natural"] >= 4 for g in graded]),
        "best_minimal": mean([g["minimal"] for g in best]),
        "truthful": mean([g["truthful"] for r in results for d in r["drafts"] if (g := d["grade"])]),
        "rejected_bolted_on": sum("bolted on" in x["reason"] for r in results for x in r["rejected"]),
        "rejected_no_keyword": sum("doesn't use" in x["reason"] for r in results for x in r["rejected"]),
    }


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobtracker eval drafting")
    ap.add_argument("--cases", default=str(HOME / "cases.json"))
    ap.add_argument("--variant", action="append", choices=list(VARIANTS))
    ap.add_argument("--repeat", type=int, default=2)
    ap.add_argument("--only", default="", help="comma-separated case ids")
    ap.add_argument("--yes", action="store_true", help="run with the real model (costs money)")
    args = ap.parse_args(argv)
    cases = json.loads(open(args.cases, encoding="utf-8").read())
    if args.only:
        cases = [c for c in cases if c["id"] in args.only.split(",")]
    variants = args.variant or ["current"]
    jobs = [(c, v) for v in variants for c in cases for _ in range(args.repeat)]
    print(f"{len(cases)} cases x {len(variants)} prompt version(s) x {args.repeat} = {len(jobs)} runs "
          f"(each: 1-2 drafting calls + 1 grading call, model {config.OPENAI_MODEL}).")
    if not args.yes:
        print("Add --yes to run them.")
        return 1

    def one(job):
        try:
            return _run_one(*job)
        except Exception as e:  # one failed call doesn't lose the run
            return {"case": job[0]["id"], "variant": job[1], "expect": job[0]["expect"], "error": str(e),
                    "drafts": [], "rejected": []}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda j: contextvars.copy_context().run(one, j), jobs))

    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    (HOME / "runs").mkdir(parents=True, exist_ok=True)
    report = {v: summarize([r for r in results if r["variant"] == v and "error" not in r]) for v in variants}
    out = HOME / "runs" / f"{stamp}-{'-'.join(variants)}.json"
    out.write_text(json.dumps({"summary": report, "results": results}, indent=2, ensure_ascii=False), encoding="utf-8")
    errors = [r for r in results if "error" in r]
    print(json.dumps(report, indent=2))
    if errors:
        print(f"{len(errors)} run(s) failed: {errors[0]['error']}")
    print(f"Details: {out}")
    return 0
