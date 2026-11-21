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
from datetime import datetime, timedelta
from pathlib import Path

from .. import config, users
from ..logs import log
from ..models import InterviewPrep, PrepBook, PrepResult
from ..storage import UserDocument
from . import companies, files, master_cv

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
   solution, a grading rubric whose points add up to 10, what a strong answer looks like, and common mistakes.
   The rubric must let another, simpler model grade the candidate's answer fairly and consistently.
5. Suggest smart questions the candidate can ask the interviewers.

Write in English, concise and concrete. Use Markdown inside text fields. Do not run shell commands or edit
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
    return "\n".join(lines)


def brief(ref: str, exercises: int = 5) -> dict:
    """Everything a model needs to write the prep: instructions, context and the JSON schema to return."""
    company = companies.resolve(ref)
    instructions = INSTRUCTIONS.format(company=company.name, website=company.website or "find it", count=exercises)
    return {"company_id": company.id, "company": company.name, "instructions": instructions,
            "context": _context(company), "output_schema": strict_schema()}


# ------------------------------------------------------------------ storage

def get(ref: str) -> InterviewPrep | None:
    company = companies.resolve(ref)
    prep = next((p for p in store.load().preps if p.company_id == company.id), None)
    if prep and prep.status == "running" and _stale(prep):
        interrupted = {"status": "error", "error": "Interrupted (the server restarted?). Try again."}
        prep = _store(prep.model_copy(update=interrupted))
    return prep


def _stale(prep: InterviewPrep) -> bool:
    started = datetime.fromisoformat(prep.requested_at)
    return datetime.now() - started > timedelta(minutes=config.PREP_TIMEOUT_MIN + 5)


def _store(prep: InterviewPrep) -> InterviewPrep:
    with store.transaction() as book:
        book.preps = [p for p in book.preps if p.company_id != prep.company_id] + [prep]
    return prep


def save(ref: str, result: dict | PrepResult, by: str) -> InterviewPrep:
    """Validate a prep pack against the schema and store it (replaces the previous one; its file is archived)."""
    company = companies.resolve(ref)
    parsed = result if isinstance(result, PrepResult) else PrepResult.model_validate(result)
    if not parsed.exercises:
        raise ValueError("The prep has no exercises")
    path = files.output_folder(company.name) / PREP_FILENAME
    files.archive_previous(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(markdown(company.name, company.position, parsed), encoding="utf-8")
    return _store(InterviewPrep(company_id=company.id, status="done", by=by, result=parsed,
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


def start(ref: str, exercises: int = 5) -> InterviewPrep:
    """Start a prep in the background (premium users only). Poll get() for the result."""
    if not users.is_premium():
        raise PermissionError("Interview prep is for premium accounts (JOBTRACKER_PREMIUM).")
    status = codex_status()
    if not status["available"]:
        raise PrepUnavailable(status["reason"])
    current = get(ref)
    if current and current.status == "running":
        return current
    company = companies.resolve(ref)
    task = brief(company.id, exercises)
    by = f"codex {config.PREP_MODEL} ({config.PREP_EFFORT})"
    prep = _store(InterviewPrep(company_id=company.id, status="running", by=by))
    user = users.current()
    threading.Thread(target=_run, args=(user, company.id, task, by), daemon=True, name=f"prep-{company.id}").start()
    return prep


def _run(user: str, company_id: str, task: dict, by: str) -> None:
    with users.acting_as(user), _one_at_a_time:
        try:
            result = run_codex(task)
            save(company_id, result, by)
            log.info("Interview prep done for %s", task["company"])
        except Exception as e:  # recorded on the prep, shown in the app
            log.warning("Interview prep failed for %s: %s", task["company"], e)
            _store(InterviewPrep(company_id=company_id, status="error", by=by, error=str(e)[:2_000],
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
