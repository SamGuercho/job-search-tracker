"""`python -m jobtracker eval matching`: how well the job matching (services/jd_match.py) reads a job and a CV.

    eval matching [--cases evals/matching/cases.json] [--repeat 3] [--only job-id,job-id] --yes

The cases are synthetic job descriptions and CVs from several professions (no one's real data, so they are
committed), with an answer key per job, written once and reviewed by a person:
    keywords: what a recruiter would search for: {"term", "aliases": [other acceptable wordings], "required",
              "status": {cv id: "covered" | "variant" | "gap"}}
    forbidden: what must never come out as a requirement (the job title, traits, generic words)
Each job is matched against each of its CVs with the real model (costs money: --yes), `repeat` times, and scored:

  Extraction (did it pull the right keywords out of the job?)
    recall_required / recall_optional  expected keywords found
    precision                          extracted items that are expected keywords (the rest are listed as
                                       `extras`: noise, or a keyword the answer key should add)
    forbidden_hits                     should be 0
  Coverage (given the CV, is each keyword's status right?)
    status_accuracy                    expected keywords with the right status
    false_gap       covered by the CV, shown as a gap (the app looks useless)
    false_cover     a real gap, shown as covered (hides a gap: the worst for truthfulness)
    needless_variant covered as written, sent to "fixable with a variant" (wastes the user's time)
    missed_variant  needs a variant, shown as covered as written (the ATS won't find the word)
  Stability
    stability       how alike the extracted keyword sets of the repeats are (1 = identical)

Results: evals/local/matching/runs/ (git-ignored).
"""
from __future__ import annotations

import argparse
import contextvars
import itertools
import json
import re
import statistics
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

from .. import config
from ..models import MasterCV
from ..services import jd_match, master_cv

CASES = config.ROOT / "evals" / "matching" / "cases.json"
RUNS = config.ROOT / "evals" / "local" / "matching" / "runs"
STATUSES = ("covered", "variant", "gap")


def _norm(term: str) -> str:
    return re.sub(r"[^a-z0-9+#]", "", term.lower())


def same(found: str, expected: str) -> bool:
    """Is an extracted requirement the expected keyword? Same letters, a plural, or each one's words (or forms of
    them) written in the other: "dashboard development" is "dashboards", "PowerBI" is "Power BI"."""
    a, b = _norm(found), _norm(expected)
    if not a or not b:
        return False
    if a == b or a.rstrip("s") == b.rstrip("s"):
        return True
    if re.search(r"/|\bor\b", found + " " + expected):  # "Looker or Tableau" is its own requirement, not "Looker"
        return False
    return master_cv.written_in(expected, found) and master_cv.written_in(found, expected)


def _matches(found: str, keyword: dict) -> bool:
    return any(same(found, t) for t in [keyword["term"], *keyword.get("aliases", [])])


def status_of(row: dict, bucket: str) -> str:
    if bucket == "gaps" or not row.get("keywords"):
        return "gap"
    return "variant" if row.get("reword") else "covered"


def score_run(job: dict, cv_id: str, result: dict) -> dict:
    """One matching result against the job's answer key, for one CV."""
    rows = [(m, "matches") for m in result["matches"] if not m.get("reword")]
    rows += [(m, "reword") for m in result["reword"]] + [(m, "gaps") for m in result["gaps"]]
    found = [(m["jd_term"], status_of(m, bucket)) for m, bucket in rows]

    used, per_keyword = set(), []
    for k in job["keywords"]:  # every keyword counts for extraction; only labelled ones for coverage
        hits = [i for i, (term, _) in enumerate(found) if _matches(term, k)]
        used.update(hits)
        # several rows for one keyword ("SQL" and "SQL skills"): the best status wins
        statuses = {found[i][1] for i in hits}
        got = next((s for s in STATUSES if s in statuses), None)
        per_keyword.append({"term": k["term"], "required": k.get("required", False), "found": bool(hits),
                            "expected": k.get("status", {}).get(cv_id), "got": got})
    forbidden = [term for term, _ in found if any(same(term, f) for f in job.get("forbidden", []))]
    extras = [term for i, (term, _) in enumerate(found) if i not in used and term not in forbidden]
    return {"job": job["id"], "cv": cv_id, "keywords": per_keyword, "forbidden": forbidden, "extras": extras,
            "extracted": [term for term, _ in found]}


def _ratio(hits: int, total: int) -> float | None:
    return round(hits / total, 3) if total else None


def summarize(scored: list[dict]) -> dict:
    kws = [k for s in scored for k in s["keywords"]]
    required = [k for k in kws if k["required"]]
    optional = [k for k in kws if not k["required"]]
    labelled = [k for k in kws if k["expected"]]
    extracted = sum(len(s["extracted"]) for s in scored)
    confusion = Counter((k["expected"], k["got"] or "missing") for k in labelled)
    return {
        "runs": len(scored),
        "recall_required": _ratio(sum(k["found"] for k in required), len(required)),
        "recall_optional": _ratio(sum(k["found"] for k in optional), len(optional)),
        "precision": _ratio(extracted - sum(len(s["extras"]) + len(s["forbidden"]) for s in scored), extracted),
        "forbidden_hits": sum(len(s["forbidden"]) for s in scored),
        "status_accuracy": _ratio(sum(k["expected"] == k["got"] for k in labelled), len(labelled)),
        "false_gap": confusion[("covered", "gap")] + confusion[("variant", "gap")],
        "false_cover": confusion[("gap", "covered")] + confusion[("gap", "variant")],
        "needless_variant": confusion[("covered", "variant")],
        "missed_variant": confusion[("variant", "covered")],
        "confusion": {f"{e} -> {g}": n for (e, g), n in sorted(confusion.items()) if e != g},
    }


def stability(scored: list[dict]) -> float | None:
    """Mean Jaccard similarity of the extracted keyword sets between repeats of the same job and CV."""
    by_pair: dict[tuple, list[set]] = {}
    for s in scored:
        by_pair.setdefault((s["job"], s["cv"]), []).append({_norm(t) for t in s["extracted"]})
    sims = [len(a & b) / len(a | b) for sets in by_pair.values() for a, b in itertools.combinations(sets, 2)
            if a | b]
    return round(statistics.mean(sims), 3) if sims else None


def problems(scored: list[dict]) -> list[str]:
    """What to look at, one line per job and CV (most repeated first)."""
    lines = Counter()
    for s in scored:
        for k in s["keywords"]:
            if not k["found"]:
                lines[f"{s['job']} / {s['cv']}: missed {'required ' if k['required'] else ''}“{k['term']}”"] += 1
            elif k["expected"] and k["expected"] != k["got"]:
                lines[f"{s['job']} / {s['cv']}: “{k['term']}” {k['expected']} -> shown as {k['got']}"] += 1
        for f in s["forbidden"]:
            lines[f"{s['job']}: forbidden “{f}” extracted"] += 1
        for e in s["extras"]:
            lines[f"{s['job']}: extra “{e}” (noise, or add it to the answer key)"] += 1
    return [f"{line}  (x{n})" for line, n in lines.most_common()]


def run_case(job: dict, cv_id: str, cv: MasterCV) -> dict:
    with master_cv.reading(cv):
        result = jd_match.match_job_description(job["jd"], use_ai=True)
    result = json.loads(json.dumps(result, default=lambda o: o.model_dump()))
    if result.get("ai_error"):
        raise RuntimeError(result["ai_error"])
    return score_run(job, cv_id, result)


def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(prog="python -m jobtracker eval matching")
    ap.add_argument("--cases", default=str(CASES))
    ap.add_argument("--repeat", type=int, default=3)
    ap.add_argument("--only", default="", help="comma-separated job ids")
    ap.add_argument("--yes", action="store_true", help="run with the real model (costs money)")
    args = ap.parse_args(argv)
    data = json.loads(open(args.cases, encoding="utf-8").read())
    cvs = {cid: MasterCV.model_validate(cv) for cid, cv in data["cvs"].items()}
    jobs = [j for j in data["jobs"] if not args.only or j["id"] in args.only.split(",")]
    pairs = [(j, cid) for j in jobs for cid in j["cvs"]]
    runs = [(j, cid) for j, cid in pairs for _ in range(args.repeat)]
    print(f"{len(jobs)} jobs, {len(pairs)} job+CV pairs x {args.repeat} = {len(runs)} runs "
          f"(one matching call each, model {config.OPENAI_MODEL}).")
    if not args.yes:
        print("Add --yes to run them.")
        return 1

    def one(item):
        job, cid = item
        try:
            return run_case(job, cid, cvs[cid])
        except Exception as e:  # one failed call doesn't lose the run
            return {"job": job["id"], "cv": cid, "error": str(e)}
    with ThreadPoolExecutor(max_workers=6) as pool:
        results = list(pool.map(lambda r: contextvars.copy_context().run(one, r), runs))

    scored = [r for r in results if "error" not in r]
    errors = [r for r in results if "error" in r]
    summary = {**summarize(scored), "stability": stability(scored)}
    RUNS.mkdir(parents=True, exist_ok=True)
    out = RUNS / f"{datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
    out.write_text(json.dumps({"summary": summary, "problems": problems(scored), "results": results}, indent=2,
                              ensure_ascii=False), encoding="utf-8")
    print(json.dumps(summary, indent=2))
    print("\nTo look at:\n" + "\n".join(f"  {p}" for p in problems(scored)[:40]))
    if errors:
        print(f"\n{len(errors)} run(s) failed: {errors[0]['error']}")
    print(f"\nDetails: {out}")
    return 0
