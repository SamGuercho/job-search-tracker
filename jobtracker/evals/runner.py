"""`python -m jobtracker eval …`: the commands of the fixing process (see .claude/skills/fix-flagged/SKILL.md).

    eval queue                        the flags to fix (from the monitor), grouped by category
    eval draft --flag 12 [--flag 13]  a draft case from flagged turn(s): snapshot, data, recorded outside calls
    eval push <case.json> [--freeze]  save a case on the server (frozen: it can't change anymore)
    eval pull                         the server's cases → evals/local/cases/
    eval run --label baseline [--cases a,b] [--drafts] [--repeat 3] [--yes]
                                      replay cases with the real model (costs money: --yes after you agreed)
    eval report <run>                 rewrite a run's report (after grading the rubric in results.json)
    eval compare <run-a> <run-b>      what changed between two runs (regressions first)
    eval resolve --flags 12,13 --fixed-in <commit>   mark flags fixed (they leave the queue)

Talks to the monitor at JOBTRACKER_MONITOR_URL (over Tailscale: the owner's access). Everything local is written
to evals/local/ (git-ignored: it holds real conversations).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import ssl
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from pathlib import Path

from .. import config
from .worker import EXTERNAL

LOCAL = config.ROOT / "evals" / "local"
CASES = LOCAL / "cases"
RUNS = LOCAL / "runs"
DEFAULT_CASE_COST = 0.02   # when the original turn's cost is unknown
TIMEOUT_S = 600


# ------------------------------------------------------------------ the monitor's API

def _tls() -> ssl.SSLContext:
    """Python's own certificate store is often empty (python.org builds on a Mac): use certifi's (Tailscale's
    certificates come from Let's Encrypt)."""
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except ImportError:
        return ssl.create_default_context()


def api(method: str, path: str, body=None):
    url = config.MONITOR_URL.rstrip("/") + path
    data = None if body is None else json.dumps(body, default=str).encode()
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=60, context=_tls()) as res:
            return json.loads(res.read() or b"null")
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        raise SystemExit(f"{method} {path}: {e.code} {detail[:300]}") from e
    except urllib.error.URLError as e:
        raise SystemExit(f"Can't reach the monitor at {config.MONITOR_URL} ({e.reason}). "
                         f"Set JOBTRACKER_MONITOR_URL (e.g. https://<pc>.<tailnet>.ts.net:8443).") from e


# ------------------------------------------------------------------ helpers

def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")[:40] or "case"


def _last_text(trace: dict) -> str:
    texts = [e["payload"].get("text") or "" for e in trace.get("events", []) if e["type"] == "llm.text"]
    return texts[-1] if texts else ""


def _message(trace: dict) -> tuple[str, str]:
    """What started the turn: the user's message, or the system note an approval sends."""
    turn = next((s for s in trace["spans"] if s["kind"] == "turn"), {"start": {}})
    start = turn["start"]
    if start.get("trigger") == "approval":
        verdict = "APPROVED" if start.get("approve") else "REJECTED"
        return "approval", f"[system note] The user {verdict} {start.get('action')}. Briefly confirm and continue."
    return "user_message", start.get("text") or ""


def _recorded(trace: dict) -> list[dict]:
    out = []
    for s in trace["spans"]:
        if s["kind"] != "tool" or s["start"].get("action") not in EXTERNAL:
            continue
        item = {"action": s["start"]["action"], "args": s["start"].get("args") or {}}
        try:
            data = json.loads(s["end"].get("result") or "{}")
        except ValueError:
            data = {}
        if s["status"] == "error" or "error" in data:
            item["error"] = s["end"].get("error") or data.get("error") or "failed"
        else:
            item["result"] = data.get("result")
        out.append(item)
    return out


def _tool_calls(trace: dict) -> list[dict]:
    return [{"action": s["start"].get("action"), "args": s["start"].get("args") or {}, "status": s["status"],
             "error": s["end"].get("error") or ""} for s in trace["spans"] if s["kind"] == "tool"]


def _case_files(ids: list[str] | None, drafts: bool) -> list[Path]:
    files = sorted(CASES.glob("*.json"))
    out = []
    for f in files:
        case = json.loads(f.read_text(encoding="utf-8"))
        if ids and case["id"] not in ids:
            continue
        if not ids and not (case.get("status") == "frozen" or (drafts and case.get("status") == "draft")):
            continue
        out.append(f)
    missing = set(ids or []) - {json.loads(f.read_text(encoding="utf-8"))["id"] for f in out}
    if missing:
        raise SystemExit(f"No local case {', '.join(sorted(missing))}: run `eval pull` or `eval draft` first")
    return out


def _git_version() -> str:
    try:
        sha = subprocess.run(["git", "rev-parse", "--short", "HEAD"], cwd=config.ROOT, capture_output=True,
                             text=True, check=True).stdout.strip()
        dirty = subprocess.run(["git", "status", "--porcelain", "--", "jobtracker"], cwd=config.ROOT,
                               capture_output=True, text=True).stdout.strip()
        return sha + ("+changes" if dirty else "")
    except (OSError, subprocess.CalledProcessError):
        return "?"


def verdict(result: dict) -> str:
    """pass / fail / pending (code checks pass, rubric not graded yet)."""
    if result.get("error") or not all(c["ok"] for c in result.get("checks") or []):
        return "fail"
    grades = [r.get("verdict") for r in result.get("rubric") or []]
    if any(g in ("no", False) for g in grades):
        return "fail"
    return "pending" if any(g in (None, "") for g in grades) else "pass"


# ------------------------------------------------------------------ commands

def cmd_queue(_args) -> None:
    queue = api("GET", "/api/review/queue?full=false")
    if not queue["flags"]:
        print("Nothing to fix.")
        return
    by_cat: dict[str, list] = {}
    for f in queue["flags"]:
        by_cat.setdefault(f"{f['group']} · {f['label']}", []).append(f)
    for cat, flags in sorted(by_cat.items(), key=lambda x: -len(x[1])):
        print(f"\n{cat} ({len(flags)})")
        for f in flags:
            where = f" on {f['span_label']}" if f.get("span_label") else ""
            print(f"  #{f['id']} [{f['status']}{', case ' + f['case_id'] if f.get('case_id') else ''}] {f['source']}"
                  f"{where} · turn {f['turn_id']} · “{(f.get('user_text') or '')[:80]}”\n     {f['note'][:200]}")


def cmd_draft(args) -> None:
    flag_ids = [int(x) for x in args.flag]
    all_flags = {f["id"]: f for f in api("GET", "/api/flags?status=*")}
    flags = [all_flags.get(i) for i in flag_ids]
    if None in flags:
        raise SystemExit(f"Unknown flag(s): {[i for i, f in zip(flag_ids, flags, strict=True) if f is None]}")
    turn_ids = {f["turn_id"] for f in flags}
    if len(turn_ids) != 1:
        raise SystemExit("A case is one turn: these flags are on different turns (draft one case per turn)")
    turn_id = turn_ids.pop()
    trace = api("GET", f"/api/turns/{urllib.parse.quote(turn_id, safe='')}")
    summary = trace.get("summary") or {}
    conv = api("GET", f"/api/conversations/{urllib.parse.quote(trace['conversation_id'], safe='')}")
    history = []
    for t in conv["turns"]:
        if t["turn_index"] >= trace["turn_index"]:
            break
        earlier = api("GET", f"/api/turns/{urllib.parse.quote(t['turn_id'], safe='')}")
        trigger, text = _message(earlier)
        history.append({"role": "user", "text": text})
        if reply := _last_text(earlier):
            history.append({"role": "assistant", "text": reply})
    trigger, message = _message(trace)
    user = summary.get("user_id") or ""
    first = flags[0]
    case_id = args.id or f"case-{first['id']}-{_slug(first['category'])}"
    case = {
        "id": case_id,
        "title": args.title or f"{first['label']}: {message[:60]}",
        "flag_ids": flag_ids,
        "status": "draft",
        "snapshot": {
            "trigger": trigger, "message": message, "history": history,
            "fixture": api("GET", f"/api/review/data?user={urllib.parse.quote(user)}"),
            "recorded": _recorded(trace),
            "source": {"turn_id": turn_id, "user_id": user, "date": trace.get("origin"),
                       "prompt_version": summary.get("prompt_version"), "status": summary.get("status"),
                       "cost_usd": summary.get("cost_usd"), "reply": _last_text(trace), "tool_calls": _tool_calls(trace),
                       "flags": [{k: f.get(k) for k in ("id", "category", "label", "source", "note", "span_label")}
                                 for f in flags]},
        },
        "checks": [],
        "rubric": [],
    }
    CASES.mkdir(parents=True, exist_ok=True)
    path = CASES / f"{case_id}.json"
    path.write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Draft case: {path}\n  message: {message[:100]}\n  history: {len(history)} messages · fixture: "
          f"{', '.join(case['snapshot']['fixture']) or 'none'} · recorded outside calls: {len(case['snapshot']['recorded'])}"
          f"\n  Next: write its checks and rubric, then `eval push {path} --freeze` once approved.")


def cmd_push(args) -> None:
    path = Path(args.file)
    case = json.loads(path.read_text(encoding="utf-8"))
    if args.freeze:
        if not case.get("checks") and not case.get("rubric"):
            raise SystemExit("A case needs checks or a rubric before it's frozen")
        case["status"] = "frozen"
    saved = api("POST", "/api/cases", case)
    path.write_text(json.dumps(saved, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Saved {saved['id']} on the server ({saved['status']}); flags {saved['flag_ids']} are now in this case.")


def cmd_pull(_args) -> None:
    CASES.mkdir(parents=True, exist_ok=True)
    cases = api("GET", "/api/cases?status=draft,frozen")
    for case in cases:
        (CASES / f"{case['id']}.json").write_text(json.dumps(case, ensure_ascii=False, indent=2), encoding="utf-8")
    frozen = sum(c["status"] == "frozen" for c in cases)
    print(f"{len(cases)} cases in {CASES} ({frozen} frozen, {len(cases) - frozen} drafts)")


def _run_one(case_file: Path, attempt: int, work: Path) -> dict:
    case = json.loads(case_file.read_text(encoding="utf-8"))
    folder = work / f"{case['id']}-{attempt}"
    folder.mkdir(parents=True, exist_ok=True)
    env = {**os.environ,
           "JOBTRACKER_DATA_DIR": str(folder / "data"), "JOBTRACKER_OUTPUT_DIR": str(folder / "out"),
           "JOBTRACKER_USERS_DIR": str(folder / "users"), "JOBTRACKER_TELEMETRY_DB": str(folder / "telemetry.db"),
           "JOBTRACKER_TELEMETRY": "1", "JOBTRACKER_TELEMETRY_BACKUP_DIR": "", "JOBTRACKER_TAILSCALE_USERS": "0",
           "JOBTRACKER_REMINDERS": "0", "JOBTRACKER_LOGOS": "0"}
    out = folder / "result.json"
    proc = subprocess.run([sys.executable, "-m", "jobtracker.evals.worker", str(case_file), str(out)], cwd=config.ROOT,
                          env=env, capture_output=True, text=True, timeout=TIMEOUT_S)
    if out.exists():
        result = json.loads(out.read_text(encoding="utf-8"))
    else:
        result = {"case_id": case["id"], "error": f"worker failed: {proc.stderr[-1500:]}", "checks": [], "rubric": []}
    result.update({"attempt": attempt, "title": case.get("title"), "message": case["snapshot"]["message"],
                   "flag_ids": case.get("flag_ids"), "case_status": case.get("status")})
    print(f"  {case['id']} #{attempt}: {verdict(result)}  ({result.get('status')}, ${result.get('cost_usd') or 0:.4f})")
    return result


def cmd_run(args) -> None:
    files = _case_files([c for c in (args.cases or "").split(",") if c] or None, args.drafts)
    if not files:
        raise SystemExit("No cases to run (frozen ones by default; --drafts to include drafts)")
    cost = sum((json.loads(f.read_text(encoding="utf-8"))["snapshot"].get("source") or {}).get("cost_usd")
               or DEFAULT_CASE_COST for f in files) * args.repeat
    print(f"{len(files)} cases × {args.repeat} = {len(files) * args.repeat} runs with the real model, "
          f"about ${cost:.2f} (from what the original turns cost).")
    if not args.yes:
        raise SystemExit("Not started: add --yes once this cost is agreed.")
    run_dir = RUNS / f"{_slug(args.label)}-{datetime.now():%Y%m%d-%H%M%S}"
    work = run_dir / "work"
    jobs = [(f, n) for f in files for n in range(1, args.repeat + 1)]
    with ThreadPoolExecutor(max_workers=args.parallel) as pool:
        results = list(pool.map(lambda j: _run_one(j[0], j[1], work), jobs))
    data = {"label": args.label, "created": datetime.now().isoformat(timespec="seconds"), "code": _git_version(),
            "prompt_version": next((r.get("prompt_version") for r in results if r.get("prompt_version")), None),
            "results": results}
    (run_dir / "results.json").write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    _write_report(run_dir)
    print(f"\nRun: {run_dir}\nReport: {run_dir / 'report.md'} (grade the rubric in results.json, then `eval report`)")


def _write_report(run_dir: Path) -> str:
    data = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    lines = [f"# Eval run {data['label']}", "",
             f"{data['created']} · code {data['code']} · prompt {data['prompt_version']}", ""]
    counts: dict[str, int] = {}
    for r in data["results"]:
        counts[verdict(r)] = counts.get(verdict(r), 0) + 1
    lines += [" · ".join(f"**{k}** {v}" for k, v in sorted(counts.items())), ""]
    lines += ["| Case | # | Verdict | Status | Checks | Rubric | Tools | Cost |", "|---|---|---|---|---|---|---|---|"]
    for r in data["results"]:
        checks_ok = sum(c["ok"] for c in r.get("checks") or [])
        graded = [x for x in r.get("rubric") or [] if x.get("verdict") not in (None, "")]
        yes = sum(x.get("verdict") in ("yes", True) for x in graded)
        lines.append(f"| {r['case_id']} | {r.get('attempt')} | {verdict(r)} | {r.get('status')} | "
                     f"{checks_ok}/{len(r.get('checks') or [])} | {yes}/{len(r.get('rubric') or [])} graded "
                     f"{len(graded)} | {len(r.get('tool_calls') or [])} | ${r.get('cost_usd') or 0:.4f} |")
    for r in data["results"]:
        lines += ["", f"## {r['case_id']} #{r.get('attempt')}: {verdict(r)}", "", f"> {r.get('message', '')[:400]}", ""]
        if r.get("error"):
            lines += [f"**Error:** {r['error'][:800]}", ""]
        for t in r.get("tool_calls") or []:
            lines.append(f"- tool `{t['action']}` {json.dumps(t.get('args'), ensure_ascii=False)[:200]} → {t['status']}"
                         + (f" ({t['error'][:120]})" if t.get("error") else ""))
        lines += ["", "**Reply:**", "", (r.get("reply") or "(none)").strip(), ""]
        for c in r.get("checks") or []:
            lines.append(f"- {'✓' if c['ok'] else '✕'} {json.dumps(c['check'], ensure_ascii=False)} · {c['detail']}")
        for x in r.get("rubric") or []:
            mark = {"yes": "✓", True: "✓", "no": "✕", False: "✕"}.get(x.get("verdict"), "?")
            lines.append(f"- {mark} {x['question']}" + (f" · {x['reason']}" if x.get("reason") else ""))
    text = "\n".join(lines) + "\n"
    (run_dir / "report.md").write_text(text, encoding="utf-8")
    return text


def _run_dir(name: str) -> Path:
    path = Path(name)
    if not path.is_absolute() and not path.exists():
        matches = sorted(RUNS.glob(f"{name}-[0-9]*")) or sorted(RUNS.glob(f"{name}*"))  # "after" ≠ "after2"
        if not matches:
            raise SystemExit(f"No run {name} in {RUNS}")
        path = matches[-1]
    return path


def cmd_report(args) -> None:
    print(_write_report(_run_dir(args.run)))


def cmd_compare(args) -> None:
    def load(name):
        data = json.loads((_run_dir(name) / "results.json").read_text(encoding="utf-8"))
        by: dict[str, list[str]] = {}
        for r in data["results"]:
            by.setdefault(r["case_id"], []).append(verdict(r))
        return data, by
    (a, va), (b, vb) = load(args.a), load(args.b)
    side = lambda d: f"{d['label']} ({d['code']}, prompt {d['prompt_version']})"  # noqa: E731
    print(f"{side(a)} → {side(b)}")
    rows = []
    for case in sorted(set(va) | set(vb)):
        before, after = va.get(case, []), vb.get(case, [])
        was_ok, is_ok = bool(before) and "fail" not in before, bool(after) and "fail" not in after
        change = ("REGRESSED" if was_ok and not is_ok else "fixed" if before and not was_ok and is_ok
                  else "new" if not before else "same")
        rows.append((change, case, ",".join(before) or "–", ",".join(after) or "–"))
    order = {"REGRESSED": 0, "fixed": 1, "new": 2, "same": 3}
    for change, case, before, after in sorted(rows, key=lambda r: order[r[0]]):
        print(f"  {change:9} {case:40} {before:>20} → {after}")


def cmd_resolve(args) -> None:
    for flag_id in [int(x) for x in args.flags.split(",") if x]:
        out = api("PATCH", f"/api/flags/{flag_id}", {"status": args.status, "fixed_in": args.fixed_in})
        print(f"  flag #{out['id']}: {out['status']}" + (f" (fixed in {out['fixed_in']})" if out.get("fixed_in") else ""))


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="python -m jobtracker eval", description=__doc__,
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("queue").set_defaults(fn=cmd_queue)
    p = sub.add_parser("draft")
    p.add_argument("--flag", action="append", required=True)
    p.add_argument("--id")
    p.add_argument("--title")
    p.set_defaults(fn=cmd_draft)
    p = sub.add_parser("push")
    p.add_argument("file")
    p.add_argument("--freeze", action="store_true")
    p.set_defaults(fn=cmd_push)
    sub.add_parser("pull").set_defaults(fn=cmd_pull)
    p = sub.add_parser("run")
    p.add_argument("--label", required=True)
    p.add_argument("--cases")
    p.add_argument("--drafts", action="store_true")
    p.add_argument("--repeat", type=int, default=1)
    p.add_argument("--parallel", type=int, default=4)
    p.add_argument("--yes", action="store_true")
    p.set_defaults(fn=cmd_run)
    p = sub.add_parser("report")
    p.add_argument("run")
    p.set_defaults(fn=cmd_report)
    p = sub.add_parser("compare")
    p.add_argument("a")
    p.add_argument("b")
    p.set_defaults(fn=cmd_compare)
    p = sub.add_parser("resolve")
    p.add_argument("--flags", required=True)
    p.add_argument("--fixed-in", default="")
    p.add_argument("--status", default="fixed", choices=["fixed", "archived", "dismissed", "open"])
    p.set_defaults(fn=cmd_resolve)
    args = parser.parse_args(argv)
    args.fn(args)
    return 0
