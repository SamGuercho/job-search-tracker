"""Interview prep: a strong model researches the company and writes the prep pack; the app stores and shows it.

The app itself never does the heavy thinking. It builds a *brief* (fixed coaching instructions, the application,
the user's CV, and the exact JSON format to return), hands it to a strong model, then validates and saves the
answer. Two ways to get a model to do it:

- the "Prepare interview" button (prep.start): runs Codex on the server, signed in with the owner's ChatGPT
  plan (no API billing), with JOBTRACKER_PREP_MODEL at JOBTRACKER_PREP_EFFORT and web search. Premium users only
  (users.is_premium), one prep at a time, in the background: it takes minutes.
- any MCP client the user runs (Codex or Claude on their computer): prep.brief, then prep.save.

The cheap in-app assistant then uses the saved exercises and rubrics to quiz the user and grade the answers.
"""
from __future__ import annotations

import json
import subprocess
import tempfile
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

from .. import config, users
from ..logs import log
from ..models import InterviewPrep, PrepBook, PrepResult
from ..storage import UserDocument
from . import companies, files, master_cv, push, usage

store = UserDocument("preps.json", PrepBook)
PREP_FILENAME = "Interview_Prep.md"
_one_at_a_time = threading.Lock()  # one Codex run at a time: they share one ChatGPT plan's limits

INSTRUCTIONS = """You are an expert interview coach and a senior engineer/hiring manager in this field.
Prepare the candidate below for their next interviews at {company}, as the best coach would.

1. Research the company with web search: its website ({website}), products, customers, business model, tech
   stack, engineering blog, recent news, and what candidates report about its interview process for this kind
   of role. Prefer primary sources. List the URLs you used in `sources`. Never invent facts: if something is
   unknown, say it is unknown or a guess.
2. Work out the likely interview process for this role (stages, format, what each stage tests) and the topics
   to revise, most likely first.
3. Map the candidate's real experience (their CV below) to the role: strengths to push, gaps to prepare.
   Never invent experience for the candidate.
4. Write {count} exercises the candidate is likely to face in the technical interviews, from easy to hard,
   matching the job's actual requirements (e.g. coding, SQL, system design, case study, depending on the role).
   For each: why this company would ask it, the prompt as an interviewer would say it, a complete reference
   solution, a grading rubric whose points add up to 10, what a strong answer looks like, common mistakes, and
   `estimated_min`: the minutes a focused study session needs to learn the notion from this candidate's level
   and do the exercise (15 to 90; the app schedules the session in their free time).
   The rubric must let another, simpler model grade the candidate's answer fairly and consistently.
   If a practice history is given below: never repeat an exercise already practised; put more weight on the
   weak topics and the points the candidate missed (with new exercises, not the same ones); match the
   difficulty to their scores (harder where they score well). Focus most on the capabilities furthest from
   their target.
   Give each exercise a `capability`: the broad skill it trains, reusing exactly one of these names when one
   fits: {capabilities}. Only if none fits, use a short new name (1 to 3 words).
5. Suggest smart questions the candidate can ask the interviewers.

Write all the text in {language}, concise and concrete (keep the JSON field names and the `difficulty` and
`capability` values in English). Use Markdown inside text fields. Do not run shell commands or edit
files: research and answer only. Your final message must be the JSON object matching the given schema.
"""


class PrepUnavailable(RuntimeError):
    pass


# -------------------------------------------------------------------- brief

def strict_schema() -> dict:
    """PrepResult's JSON schema in the strict form structured output needs (every field required, no extras)."""
    def fix(node):
        if isinstance(node, dict):
            if isinstance(node.get("title"), str):  # the schema's label, not a property named "title"
                node.pop("title")
            node.pop("default", None)
            if node.get("type") == "object" and "properties" in node:
                node["required"] = list(node["properties"])
                node["additionalProperties"] = False
            for value in node.values():
                fix(value)
        elif isinstance(node, list):
            for value in node:
                fix(value)
        return node
    return fix(PrepResult.model_json_schema())


def _context(company) -> str:
    c = company
    lines = [f"Company: {c.name}", f"Position: {c.position or 'unknown'}", f"Website: {c.website or 'unknown'}",
             f"Job posting: {c.job_url or 'unknown'}", f"Location: {c.location or 'unknown'}",
             f"Seniority: {c.seniority or 'unknown'}", f"Stage reached: {c.process.stage()}"]
    if c.notes.strip():
        lines.append(f"\nCandidate's notes on this application (interviews so far, people met…):\n{c.notes.strip()}")
    if c.job_description.strip():
        lines.append(f"\nJob description:\n{c.job_description.strip()[:15_000]}")
    cv = master_cv.get_master().model_dump(mode="json", exclude_none=True)
    lines.append(f"\nCandidate's CV (JSON):\n{json.dumps(cv, ensure_ascii=False)[:20_000]}")
    from . import practice, recordings  # (they import prep)
    interviews = recordings.summary_for_prep(c.id)
    if interviews:
        lines.append(f"\nWhat happened in the candidate's interviews at {c.name} so far (from recordings; prepare the "
                     f"NEXT stage, build on what was asked and on what to improve):\n{interviews}")
    history = practice.summary_for_prep()
    if history:
        lines.append(f"\nCandidate's practice history (all applications):\n{history}")
    return "\n".join(lines)


def brief(ref: str, exercises: int = 5, language: str = "") -> dict:
    """Everything a model needs to write the prep: instructions, context and the JSON schema to return."""
    company = companies.resolve(ref)
    from . import practice  # (imports prep)
    instructions = INSTRUCTIONS.format(company=company.name, website=company.website or "find it", count=exercises,
                                       capabilities=", ".join(practice.known_capabilities()),
                                       language=language.strip() or _default_language())
    return {"company_id": company.id, "company": company.name, "instructions": instructions,
            "context": _context(company), "output_schema": strict_schema()}


# ------------------------------------------------------------------ storage

MAX_VERSIONS = 10  # finished preps kept per application (older ones remain as archived .md files)


def versions(ref: str) -> list[InterviewPrep]:
    """Every version of an application's prep, oldest first (including a running or failed latest run)."""
    company = companies.resolve(ref)
    out = []
    for p in (p for p in store.load().preps if p.company_id == company.id):
        if p.status == "running" and _stale(p):
            p = _store(p.model_copy(update={"status": "error", "error": "Interrupted (the server restarted?). Try again."}))
        out.append(p)
    return sorted(out, key=lambda p: p.requested_at)


def get(ref: str, version: str | None = None) -> InterviewPrep | None:
    """The latest finished prep (or the given version id); if none finished yet, the latest run."""
    all_ = versions(ref)
    if version:
        return next((p for p in all_ if p.id == version), None)
    done = [p for p in all_ if p.status == "done"]
    return done[-1] if done else (all_[-1] if all_ else None)


def latest_run(ref: str) -> InterviewPrep | None:
    """The newest run if it is newer than the shown prep and not finished (running, or failed)."""
    all_ = versions(ref)
    return all_[-1] if all_ and all_[-1].status != "done" else None


def _stale(prep: InterviewPrep) -> bool:
    started = datetime.fromisoformat(prep.requested_at)
    return datetime.now() - started > timedelta(minutes=config.PREP_TIMEOUT_MIN + 5)


def _store(prep: InterviewPrep) -> InterviewPrep:
    """Add a version, or replace the one with the same id (a run that finished). A finished run replaces the
    failed runs before it, and only the last MAX_VERSIONS finished preps are kept."""
    with store.transaction() as book:
        mine = [p for p in book.preps if p.company_id == prep.company_id and p.id != prep.id] + [prep]
        if prep.status == "done":
            mine = [p for p in mine if p.status != "error"]
        done = [p for p in mine if p.status == "done"]
        dropped = {p.id for p in done[:-MAX_VERSIONS]} if len(done) > MAX_VERSIONS else set()
        book.preps = [p for p in book.preps if p.company_id != prep.company_id] + \
            [p for p in mine if p.id not in dropped]
    return prep


def save(ref: str, result: dict | PrepResult, by: str, run_id: str | None = None) -> InterviewPrep:
    """Validate a prep pack against the schema and store it as the newest version. Interview_Prep.md always holds
    the newest one (the previous file goes to archive/); earlier versions stay readable in the app."""
    company = companies.resolve(ref)
    parsed = result if isinstance(result, PrepResult) else PrepResult.model_validate(result)
    if not parsed.exercises:
        raise ValueError("The prep has no exercises")
    path = files.output_folder(company.name) / PREP_FILENAME
    files.archive_previous(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown(company.name, company.position, parsed), encoding="utf-8")
    return _store(InterviewPrep(**({"id": run_id} if run_id else {}), company_id=company.id, status="done", by=by,
                                result=parsed,
                                finished_at=datetime.now().isoformat(timespec="seconds"), file=files.relative(path)))


def markdown(name: str, position: str, r: PrepResult) -> str:
    out = [f"# Interview prep – {name}" + (f" ({position})" if position else ""), "",
           "## The company", r.company_summary, "", "## Likely interview process", r.interview_process, "",
           "## Focus topics", *[f"- {t}" for t in r.focus_topics], "", "## Your angle", r.candidate_angle, "",
           "## Exercises"]
    for i, e in enumerate(r.exercises, 1):
        out += ["", f"### {i}. {e.title} ({e.kind}, {e.difficulty})", f"*Why they'd ask it:* {e.why}", "",
                e.prompt, "", "<details><summary>Solution, rubric, strong answer</summary>", "", e.solution, "",
                "**Rubric (10 points)**", *[f"- {p.point} ({p.points})" for p in e.rubric], "",
                "**Strong answer:** " + e.strong_answer, "", "**Common mistakes**",
                *[f"- {m}" for m in e.common_mistakes], "", "</details>"]
    out += ["", "## Questions to ask them", *[f"- {q}" for q in r.questions_to_ask], "",
            "## Sources", *[f"- {s}" for s in r.sources], ""]
    return "\n".join(out)


# ------------------------------------------------------------- codex runner

def codex_status() -> dict:
    """Whether the button can work here: Codex installed and signed in."""
    try:
        done = subprocess.run([config.CODEX_BIN, "login", "status"], capture_output=True, text=True, timeout=20)
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "reason": f"Codex is not installed on the server ({e.__class__.__name__})."}
    text = (done.stdout + done.stderr).strip()
    if done.returncode != 0 or "not logged in" in text.lower():
        return {"available": False, "reason": "Codex is not signed in on the server: see the home-server guide."}
    return {"available": True, "reason": text.splitlines()[0] if text else "signed in"}


def auto_start(company) -> bool:
    """A technical interview was just scheduled and this application has no prep yet: start one in the background
    (premium accounts; JOBTRACKER_AUTO_PREP=0 turns it off). Never raises: the step change must not fail."""
    today = datetime.now().date().isoformat()
    scheduled = any(n.state == "planned" and n.type() == "technical" and (n.date or "")[:10] >= today
                    for n in company.process.steps)
    try:
        if not (config.AUTO_PREP and scheduled and users.is_premium()) or versions(company.id):
            return False
    except Exception:  # noqa: BLE001
        return False
    user = users.current()

    def run() -> None:
        with users.acting_as(user):
            try:
                if codex_status()["available"] and not versions(company.id):
                    start(company.id)
                    log.info("Interview prep started by itself for %s (technical interview scheduled)", company.name)
            except Exception as e:  # noqa: BLE001  (best effort: the user can still press Prepare)
                log.warning("Automatic prep for %s not started: %s", company.name, e)
    threading.Thread(target=run, daemon=True, name=f"auto-prep-{company.id}").start()
    return True


def _default_language() -> str:
    from . import profile
    return profile.settings().interview_language or "English"


def start(ref: str, exercises: int = 5, language: str = "") -> InterviewPrep:
    """Start a prep in the background (premium users only). Poll get() for the result."""
    if not users.is_premium():
        raise PermissionError("Interview prep is for premium accounts (JOBTRACKER_PREMIUM).")
    status = codex_status()
    if not status["available"]:
        raise PrepUnavailable(status["reason"])
    current = latest_run(ref)
    if current and current.status == "running":
        return current
    company = companies.resolve(ref)
    task = brief(company.id, exercises, language)
    by = f"codex {config.PREP_MODEL} ({config.PREP_EFFORT})"
    prep = _store(InterviewPrep(company_id=company.id, status="running", by=by))
    user = users.current()
    threading.Thread(target=_run, args=(user, company.id, prep.id, task, by), daemon=True,
                     name=f"prep-{company.id}").start()
    return prep


def _run(user: str, company_id: str, run_id: str, task: dict, by: str) -> None:
    with users.acting_as(user), usage.feature("prep.start"), _one_at_a_time:
        try:
            started = time.monotonic()
            result = run_codex(task)
            usage.record_plan(config.PREP_MODEL, time.monotonic() - started)
            save(company_id, result, by, run_id)
            push.notify("Interview prep ready", f"{task['company']}: research, likely questions and "
                        f"{len(result.exercises)} exercises.", f"/#/company/{company_id}", tag=f"prep:{company_id}")
            log.info("Interview prep done for %s", task["company"])
        except Exception as e:  # recorded on the prep, shown in the app
            log.warning("Interview prep failed for %s: %s", task["company"], e)
            push.notify("Interview prep failed", f"{task['company']}: open the application to see why and retry.",
                        f"/#/company/{company_id}", tag=f"prep:{company_id}")
            _store(InterviewPrep(id=run_id, company_id=company_id, status="error", by=by, error=str(e)[:2_000],
                                 finished_at=datetime.now().isoformat(timespec="seconds")))


def run_codex(task: dict) -> PrepResult:
    """One non-interactive Codex run with web search, forced to answer in the prep schema."""
    with tempfile.TemporaryDirectory(prefix="prep-") as tmp:
        schema, answer = Path(tmp) / "schema.json", Path(tmp) / "answer.json"
        schema.write_text(json.dumps(task["output_schema"]), encoding="utf-8")
        cmd = [config.CODEX_BIN, "--search", "exec", "--model", config.PREP_MODEL,
               "-c", f'model_reasoning_effort="{config.PREP_EFFORT}"',
               "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "--color", "never",
               "--output-schema", str(schema), "--output-last-message", str(answer), "-"]
        prompt = f"{task['instructions']}\n\n---\n\n{task['context']}"
        try:
            done = subprocess.run(cmd, input=prompt, capture_output=True, text=True, cwd=tmp,
                                  timeout=config.PREP_TIMEOUT_MIN * 60)
        except subprocess.TimeoutExpired as e:
            raise PrepUnavailable(f"Codex took more than {config.PREP_TIMEOUT_MIN} minutes") from e
        if done.returncode != 0 or not answer.exists():
            detail = (done.stderr or done.stdout).strip()[-1_500:]
            raise PrepUnavailable(f"Codex failed (exit {done.returncode}): {detail}")
        text = answer.read_text(encoding="utf-8").strip()
    start_, end = text.find("{"), text.rfind("}")  # tolerate a stray code fence around the JSON
    return PrepResult.model_validate_json(text[start_:end + 1] if start_ >= 0 else text)
