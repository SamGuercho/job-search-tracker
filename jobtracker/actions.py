"""Every operation of the app, as named actions.

These are the only entry points used by the web UI (`POST /api/actions/<name>`),
the CLI (`python -m jobtracker <name> '<json>'`) and agents (see skills/).
Keep business logic in services/; actions only adapt inputs and outputs.
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, create_model

from . import config, maintenance, users
from .cv_templates import TEMPLATES
from .models import (
    SOURCE_TYPES,
    STATUSES,
    WORK_MODELS,
    Bullet,
    Company,
    Contact,
    Education,
    Experience,
    MasterCV,
    Profile,
    SkillGroup,
    SourceType,
    StudyHours,
    UpdateSide,
    Variant,
    WeeklyGoals,
    WorkModel,
)
from .process import CATEGORIES, Node, NodeState
from .registry import action
from .services import (
    analytics,
    assistant,
    calendar,
    companies,
    cv_builder,
    cv_import,
    cv_score,
    headlines,
    jd_match,
    jobs,
    learning,
    linkedin,
    llm,
    logos,
    mail,
    master_cv,
    practice,
    prep,
    profile,
    push,
    recordings,
    rules,
    stories,
    today,
    usage,
    wording,
)
from .services import summary as summary_service  # `summary` is also a parameter name below
from .services.cv_import import ImportMode


def partial(model: type[BaseModel], exclude: set[str] = frozenset()) -> type[BaseModel]:
    """Same fields as `model`, all optional: used for "update" payloads."""
    fields = {n: (Optional[f.annotation], None) for n, f in model.model_fields.items() if n not in exclude}
    # Unknown fields are errors (e.g. "status", which is derived from the process graph)
    return create_model(f"{model.__name__}Patch", __config__=ConfigDict(extra="forbid"), **fields)


CompanyPatch = partial(Company, exclude={"id", "status", "process", "documents", "created_at", "cv_summary", "cv_title",
                                         "cv_headline", "cv_include",
                                         "job_description_file", "job_description_saved_at", "logo_file", "logo_source",
                                         "logo_checked_at", "referral_asked_at"})
StepPatch = partial(Node, exclude={"id"})
ProfilePatch = partial(Profile)
ExperiencePatch = partial(Experience, exclude={"id", "bullets"})
BulletPatch = partial(Bullet, exclude={"id", "variants"})
VariantPatch = partial(Variant, exclude={"id"})


def _patch(p: BaseModel) -> dict:
    return {k: getattr(p, k) for k in p.model_fields_set}


# ================================================================ companies

@action("app.info")
def app_info():
    """Static lists the UI needs: application statuses, step states, CV templates."""
    return {"statuses": STATUSES, "step_states": ["todo", "planned", "done"], "step_types": CATEGORIES,
            "work_models": WORK_MODELS, "source_types": SOURCE_TYPES,
            "templates": [{"name": t.name, "label": t.label} for t in TEMPLATES.values()],
            "llm_available": llm.available(), "user": users.current() or users.owner(),
            "premium": users.is_premium(), "version": maintenance.version(),
            "recordings": llm.openai_available() and bool(recordings.shutil.which(config.FFMPEG_BIN)),
            "follow_ups_due": sum(len(v) for v in rules.nudges(companies.list_companies()).values())}


@action("company.list")
def company_list(status: Optional[str] = None, query: Optional[str] = None):
    """List tracked companies, optionally filtered by status or a text query (name/position/location)."""
    return companies.list_companies(status, query)


@action("company.get")
def company_get(id: str):
    """Full details of one company, including its process steps and generated CVs. `id`: its id, or the company
    name / position."""
    return companies.resolve(id)


@action("company.create")
def company_create(name: str, position: str = "",
                   contact: Optional[Contact] = None, referee: str = "", location: str = "",
                   website: str = "", seniority: str = "", qualities: list[str] = [],
                   notes: str = "", job_description: str = "", keywords: list[str] = [],
                   work_model: WorkModel = "", address: str = "", job_url: str = "",
                   source_type: SourceType = "", source_detail: str = "",
                   salary_expectation: str = "", salary_posted: str = ""):
    """Add a company/position to the tracker. `qualities` are the main requirements of the role.
    It starts with the default process (Applied, Response received, First HR interview, Reference check,
    Rejected/Closed), all off: status "Not applied". Mark steps done with process.set_state when they happen.
    The job description is also saved as Job_Description.md in the company's folder (postings disappear).
    source_type: Job board / Company website / Headhunter / Referral / network / Other (+ source_detail)."""
    return companies.create_company(
        name=name, position=position, contact=contact or Contact(), referee=referee,
        location=location, website=website, seniority=seniority, qualities=qualities,
        notes=notes, job_description=job_description, keywords=keywords, work_model=work_model,
        address=address, job_url=job_url, source_type=source_type, source_detail=source_detail,
        salary_expectation=salary_expectation, salary_posted=salary_posted)


@action("company.update")
def company_update(id: str, fields: CompanyPatch):
    """Update some fields of a company (only the fields given are changed)."""
    return companies.update_company(id, **_patch(fields))


@action("company.delete", confirm=True)
def company_delete(id: str):
    """Delete a company and its process (generated files on disk are kept)."""
    companies.delete_company(id)
    return {"deleted": id}


@action("company.find_logo")
def company_find_logo(company: str):
    """Look for the company's logo on its website now (done automatically when a website is added). Without a
    website, the website is found first (job posting site, contact's email domain, <name>.com…, verified against
    the company's name) and saved: say so to the user (`website_found`)."""
    return logos.refresh(company)


@action("company.remove_logo")
def company_remove_logo(company: str):
    """Remove the company's logo: its initials are shown instead."""
    return logos.remove(company)


@action("company.log_update")
def company_log_update(id: str, by: UpdateSide = "me", date: Optional[str] = None, note: str = ""):
    """Record news from you ('me') or from the company ('them'); resets the 'days since update' counter.
    `date` is YYYY-MM-DD (default today). An optional note is appended to the company notes."""
    return companies.log_update(id, by, date, note)


# ============================================================ interview prep

def _prep_view(p) -> dict | None:
    return p.model_dump(mode="json") if p else None


@action("prep.get")
def prep_get(company: str, version: Optional[str] = None):
    """The interview prep pack of an application (company research, likely interview process, focus topics,
    exercises with reference solution and a 10-point grading rubric, questions to ask), or null if none: the
    latest finished one, or `version` (an id from `versions`). `status` is running (being written, takes
    minutes), done or error; `run` is a newer run still running or failed. Each exercise is `category`
    "technical" (a reference solution) or "story" (a "tell me about a time…" question: `stories` names the
    user's stories that answer it, `solution` says how to answer, never a model story). To practice with the
    user: give one exercise's prompt at a time, wait for their answer, grade it against the rubric (score /10,
    points hit and missed), record it with practice.record, then show the solution if they want it."""
    p = prep.get(company, version)
    if p is None:
        return None
    run = prep.latest_run(company)
    return {**_prep_view(p),
            "versions": [{"id": v.id, "finished_at": v.finished_at, "by": v.by}
                         for v in prep.versions(company) if v.status == "done"],
            "run": _prep_view(run) if run and run.id != p.id else None}


@action("practice.record")
def practice_record(company: str, exercise: str, score: int, missed: list[str] = [], feedback: str = "",
                    topic: str = "", kind: str = "", capability: str = "", session: str = "", story: str = ""):
    """Log a graded practice answer: `exercise` is the prep exercise's number or title, `score` out of 10
    (from the rubric), `missed` the rubric points the answer missed, `feedback` one or two lines. For an
    exercise that isn't in the prep, also give its topic, kind and capability (the broad skill: SQL, Statistics,
    System design…). In a study session, give its id as `session`. For a story question, give the story the
    user told as `story` (id or title). Call it after every graded answer.
    `learning` in the result says what it changed: the session completed and what comes next (a check, a 3rd
    check, or learned), or checks suggested for a new notion: tell the user."""
    attempt = practice.record(company, exercise, score, missed, feedback, topic, kind, capability, story)
    return {**attempt.model_dump(mode="json"), "learning": learning.on_attempt(attempt, session)}


# --------------------------------------------------------------------- mail

@action("mail.status")
def mail_status():
    """The user's email connection: whether the server can read email (`configured`), whether their Gmail is
    connected (read-only), its address, the last check and its error. Connecting happens in the app (Profile →
    Email): it needs the user's Google sign-in, which you can't do for them."""
    return mail.status()


@action("mail.check")
def mail_check():
    """Look at the emails received since the last check now (it also runs by itself every few minutes). Only
    emails naming one of the user's applications are read; each becomes a proposed update."""
    return mail.check()


@action("mail.inbox")
def mail_inbox():
    """News found in the user's email: `proposed` updates waiting for them (application, what happened, a
    one-line summary, interview date/time/place, and `action`: what applying it does), and recent ones applied or
    dismissed."""
    return mail.inbox()


@action("mail.apply")
def mail_apply(update: str):
    """Apply a proposed email update (its id from mail.inbox) when the user agrees: closes the application for a
    rejection, plans the interview step for an invite with a date, marks Applied for a confirmation, and logs the
    news from the company."""
    return mail.apply(update)


@action("mail.dismiss")
def mail_dismiss(update: str):
    """Dismiss a proposed email update (wrong application, not news): nothing changes."""
    return mail.dismiss(update)


@action("mail.disconnect", confirm=True)
def mail_disconnect():
    """Stop reading the user's email: forgets the access and asks Google to revoke it."""
    return mail.disconnect()


# ------------------------------------------------------------------ stories

@action("story.list")
def story_list():
    """The user's story bank: their true stories for "tell me about a time…" questions (Situation, Task, Action,
    Result, lesson), each with the competencies it answers, the master CV achievements it tells, the STAR parts
    still `missing` and its practice scores; `coverage`: the common competencies and the stories that answer
    each; `gaps`: common competencies with no story yet (what to write next)."""
    return stories.overview()


@action("story.get")
def story_get(story: str):
    """One story (id or title) with its achievements and practice."""
    s = stories.resolve(story)
    return stories.view(s, attempts=[a for a in practice.store.load().attempts if a.story_id == s.id])


@action("story.add")
def story_add(title: str, situation: str = "", task: str = "", action: str = "", result: str = "", lesson: str = "",
              competencies: list[str] = [], achievements: list[str] = []):
    """Save a new story in the bank. Only the user's own facts, in their words tidied up: never add a fact, number
    or outcome they didn't give. `title`: a short name they'll recognise. `situation` (context, 1-2 sentences),
    `task` (what was at stake, their responsibility), `action` (what THEY did, with "I"), `result` (outcome, with
    numbers when they have them), `lesson` (what they learned or would do differently). `competencies`: the
    questions it answers (see story.list's `coverage`; usually 2-3). `achievements`: ids of the master CV
    achievements it tells (from cv.master.get)."""
    return stories.view(stories.add(title, situation, task, action, result, lesson, competencies, achievements))


@action("story.update")
def story_update(story: str, title: Optional[str] = None, situation: Optional[str] = None,
                 task: Optional[str] = None, action: Optional[str] = None, result: Optional[str] = None,
                 lesson: Optional[str] = None, competencies: Optional[list[str]] = None,
                 achievements: Optional[list[str]] = None):
    """Change a story (id or title): only the fields given; lists replace the previous ones. Same rule as
    story.add: only facts the user gave."""
    return stories.view(stories.update(story, title=title, situation=situation, task=task, action=action,
                                       result=result, lesson=lesson, competencies=competencies,
                                       achievements=achievements))


@action("story.delete", confirm=True)
def story_delete(story: str):
    """Delete a story from the bank (id or title). Its practice answers stay in the practice log."""
    return stories.delete(story)


@action("learning.agenda")
def learning_agenda(days: int = 7):
    """Study sessions planned from now to `days` ahead (today's, then upcoming), checks suggested for new
    notions, and sessions just moved because they were missed (they move to the next free slot by themselves)."""
    return learning.agenda(days)


@action("learning.courses")
def learning_courses(company: Optional[str] = None):
    """Courses (topics to learn, each a teach session then spaced checks), all or one application's: state
    (proposed, suggested, scheduled, done), outcome (learned, needs_work), sessions with times and scores."""
    return learning.courses(company)


@action("learning.session")
def learning_session(session: str):
    """How to run a study session: the topic, the exercise (teach) or the points the user missed last time
    (check: ask NEW questions that cover them), and step-by-step `instructions` to follow."""
    return learning.session_brief(session)


@action("learning.propose")
def learning_propose(company: str, exercises: Optional[list[str]] = None):
    """Propose study slots for an application's interview prep: one course per exercise (or the given exercise
    titles), each a teach session (estimated time) then check 1 (1-2 days later) and check 2 (3-4 days after
    check 1), in the user's free study time between now and the interview. Nothing is booked until
    learning.accept: show the proposal and ask first."""
    return learning.propose(company, exercises)


@action("learning.accept")
def learning_accept(company: Optional[str] = None, courses: Optional[list[str]] = None):
    """Book proposed courses (all of an application's, or the given course ids) or suggested checks (by course
    id): their sessions go on Today and in the calendar feed."""
    return learning.accept(company, courses)


@action("learning.decline")
def learning_decline(company: Optional[str] = None, courses: Optional[list[str]] = None):
    """Drop an application's proposed courses, or the given courses (proposed, suggested or scheduled): their
    planned sessions are freed."""
    return learning.decline(company, courses)


@action("learning.move")
def learning_move(session: str, start: Optional[str] = None):
    """Move a planned study session to `start` (YYYY-MM-DDTHH:MM, local time) or, without it, to the next free
    slot. Later checks of the course move too if they'd come too soon."""
    return learning.move(session, start)


@action("learning.complete")
def learning_complete(session: str, score: int, missed: list[str] = []):
    """Mark a study session done without a practice answer (practice.record with `session` does it by itself):
    score /10 and the rubric points missed."""
    return learning.complete(session, score, missed)


@action("practice.capabilities")
def practice_capabilities():
    """Progress per capability (SQL, Statistics, System design…): level now and at the start (average of the
    last / first 3 answers), change, best score, target and gap, and every answer over time. Furthest from
    target (or weakest) first. For "am I improving in X?" or "what should I work on?"."""
    return practice.capabilities()


@action("practice.set_target")
def practice_set_target(capability: str, target: Optional[int] = None):
    """Set the score /10 the user aims for on a capability (e.g. SQL: 8 before onsite interviews); no target
    removes it. New preps then focus on the capabilities furthest from their target."""
    return practice.set_target(capability, target)


@action("practice.rename_capability", confirm=True)
def practice_rename_capability(capability: str, to: str):
    """Rename a capability, or merge it into an existing one (e.g. "Stats" into "Statistics"): its answers,
    target and prep exercises move to `to`. A merge can't be undone."""
    return practice.rename(capability, to)


@action("practice.classify")
def practice_classify():
    """Sort practice answers that have no capability yet (logged before capabilities existed) into
    capabilities: by keywords, then the cheap model for the rest. Returns how many were sorted."""
    return practice.classify()


@action("practice.profile")
def practice_profile():
    """The user's training profile from their practice log: totals, scores by topic (weakest first), exercises
    done, and `to_review` (exercises whose last score is below 7, weakest first, with the points missed). Use it
    for "what should I review?", and to avoid repeating exercises already done well."""
    return practice.profile()


@action("practice.history")
def practice_history(company: Optional[str] = None):
    """Practice attempts, newest first (one application's if `company` is given): exercise, score /10, missed
    points, feedback, date."""
    return [a.model_dump(mode="json") for a in practice.history(company)]


@action("prep.status")
def prep_status():
    """Whether the 'Prepare interview' button can run here: premium account, Codex installed and signed in."""
    if not users.is_premium():
        return {"premium": False, "available": False, "reason": "Interview prep is for premium accounts."}
    return {"premium": True, **prep.codex_status(), "model": config.PREP_MODEL, "effort": config.PREP_EFFORT}


@action("prep.start")
def prep_start(company: str, exercises: int = 5, language: str = ""):
    """Start writing the interview prep for an application on the server (Codex with a strong model and web
    search; premium accounts only). Runs in the background for several minutes: check prep.get for the result.
    Replaces the previous prep (its file is archived). `language`: the language of the prep's text (e.g. Hebrew
    for an HR round in Hebrew)."""
    return _prep_view(prep.start(company, max(1, min(exercises, 10)), language))


@action("prep.brief")
def prep_brief(company: str, exercises: int = 5, language: str = ""):
    """For an AI client writing the prep itself (e.g. Codex or Claude through MCP): the coaching instructions,
    the application and CV context, and the JSON schema to fill. Follow the instructions (research with web
    search), then call prep.save with the JSON object."""
    return prep.brief(company, max(1, min(exercises, 10)), language)


@action("prep.save")
def prep_save(company: str, result: dict, by: str = "assistant via MCP"):
    """Save an interview prep written from prep.brief: `result` must match the brief's output_schema. Replaces
    the previous prep (its file is archived) and writes Interview_Prep.md in the company folder."""
    return _prep_view(prep.save(company, result, by))


# ================================================================ recordings

@action("recording.list")
def recording_list(company: str):
    """An application's interview recordings (upload them in the app): status, length, estimated and actual
    costs, speakers, and the debrief once analyzed (summary, questions with graded answers, to improve,
    thank-you email)."""
    return [recordings.view(r) for r in recordings.list_for(company)]


@action("recording.get")
def recording_get(id: str):
    """One recording (see recording.list)."""
    return recordings.view(recordings.get(id))


@action("recording.transcript")
def recording_transcript(id: str):
    """The full transcript of a recording, with [CANDIDATE] / [INTERVIEWER] labels when known."""
    return recordings.transcript(id)


@action("recording.transcribe")
def recording_transcribe(id: str, language: str = ""):
    """Transcribe an uploaded recording (OpenAI, billed per minute: see its estimate_usd first), in the
    background. Then the user picks their voice (recording.choose_speaker) and the debrief is written.
    `language`: the debrief's language ("" = the interview's)."""
    return recordings.view(recordings.transcribe(id, language))


@action("recording.choose_speaker")
def recording_choose_speaker(id: str, me: str):
    """Say which speaker label (A, B…) is the user, then write the debrief and grade the answers (background)."""
    return recordings.view(recordings.choose_speaker(id, me))


@action("recording.reanalyze")
def recording_reanalyze(id: str, language: Optional[str] = None):
    """Write the debrief again (e.g. in another language); replaces its graded answers in the practice log."""
    return recordings.view(recordings.reanalyze(id, language))


@action("recording.delete", confirm=True)
def recording_delete(id: str):
    """Delete a recording, its audio file and the practice answers graded from it."""
    recordings.delete(id)
    return {"deleted": id}


# ===================================================================== today

@action("today.get")
def today_get():
    """What to do now: interviews in the next 14 days (with prep status and readiness 0-100) and the one to
    train for first, applications not applied yet (oldest first), follow-ups due and "probably no answer"
    (rules by stage: see services/rules.py), interviews to debrief, recordings waiting for the user, what came
    back recently, what to review, the week in numbers."""
    return today.summary()


@action("today.snooze")
def today_snooze(company: str, days: int = rules.SNOOZE_DAYS):
    """Hide an application's nudges on Today (follow-up, probably no answer, still interested?) for `days` days
    (0 = show them again)."""
    c = companies.resolve(company)
    until = rules.snooze_until(max(1, min(days, 90))) if days > 0 else None
    return companies.update_company(c.id, snooze_until=until).view()


# ================================================================== calendar

@action("calendar.event")
def calendar_event(company: str, step: str):
    """A scheduled step as a calendar event (title, start/end, all_day, location, description), to add it to
    the user's calendar. The app offers Google Calendar and .ics (Apple, Outlook) buttons on the step."""
    c, n = calendar.for_step(company, step)
    return calendar.event(c, n)


@action("calendar.upcoming")
def calendar_upcoming():
    """Upcoming scheduled interviews (open applications), soonest first: company, step, date, time, location."""
    return [{"company_id": c.id, "company": c.name, "step_id": n.id, "step": n.name, "date": n.date, "time": n.time,
             "duration_min": n.duration_min, "location": n.location} for c, n in calendar.upcoming()]


# ============================================================= notifications

@action("push.key")
def push_key():
    """The server's public key, for a browser to subscribe to notifications."""
    return {"public_key": push.public_key()}


@action("push.subscribe")
def push_subscribe(subscription: dict, timezone: str = "UTC", device: str = ""):
    """Register this device for notifications (prep ready, debrief ready, interview and follow-up reminders)."""
    return push.subscribe(subscription, timezone, device)


@action("push.unsubscribe")
def push_unsubscribe(endpoint: str):
    """Stop notifications on one device."""
    return push.unsubscribe(endpoint)


@action("push.devices")
def push_devices():
    """The devices that receive this person's notifications."""
    return push.devices()


@action("push.test")
def push_test():
    """Send a test notification to this person's devices."""
    return {"sent": push.notify("Notifications are on", "You'll get interview reminders and prep/debrief alerts here.",
                                "/#/", tag="test")}


# ===================================================================== costs

@action("usage.report")
def usage_report(days: int = 30):
    """AI costs from the usage the providers reported, at list prices: this month and last month, by provider,
    feature and model, per day, the latest calls, and (owner, several users) per person. Codex preps are $0
    (ChatGPT plan). Estimates: the providers' bills are the reference."""
    return usage.report(max(1, min(days, 365)))


@action("usage.averages")
def usage_averages():
    """Average cost per use of the main AI features, from this person's history (for estimates)."""
    names = ["chat.send", "chat.resolve", "cv.import.parse", "cv.keywords.match_job", "cv.summary.review",
             "cv.variant.draft", "workflow.new_application", "recording.transcribe", "recording.analyze"]
    return {n: a for n in names if (a := usage.average_cost(n))}


# ============================================================== applications

def _app(c) -> dict:
    return {"id": c.id, "name": c.name, "position": c.position, "status": c.status,
            "stage": c.process.stage(), "days_since_update": c.days_since_update()}


@action("application.find")
def application_find(query: str):
    """Find applications by company name and/or position ("nimbus", "Nimbus AI scientist", "applied ai").
    Returns id, name, position, status and stage, best match first. Process tools also accept the name directly."""
    return [_app(c) for c in companies.find_applications(query)]


@action("application.save_job_description")
def application_save_job_description(company: str):
    """Write (or refresh) Job_Description.md in the company's folder from the stored job description.
    Done automatically when the description is set; use this to re-save it on demand."""
    c = companies.save_job_description(company)
    return {"company": c.name, "file": c.job_description_file, "saved_at": c.job_description_saved_at}


@action("cv.summary.review")
def cv_summary_review(company: str, summary: Optional[str] = None, facts: str = ""):
    """Check whether the CV summary fits an application's job description: verdict + score, strengths,
    requirements the summary misses (`missing_but_in_cv`: the CV has evidence; `gaps`: it doesn't), and a
    suggested rewrite using only facts from the CV (or `facts` the user gave). Reviews the application's tailored
    summary if it has one, else the master summary; or pass `summary` to review a draft. Saves nothing: when the
    summary doesn't fit, the rewrite comes back in `proposals` (in the chat: already shown for the user's approval,
    for this application only)."""
    review = summary_service.review_summary(company, summary, facts)
    return {**review, **summary_service.proposal_from_review(review)}


@action("company.set_cv_summary", confirm=True)
def company_set_cv_summary(company: str, summary: str):
    """Use this summary in this application's CVs only (the master CV summary is unchanged).
    An empty summary goes back to the master one. Regenerate the CV afterwards to apply it."""
    c = summary_service.set_summary(company, summary)
    return {"company": c.name, "cv_summary": c.cv_summary or "(master CV summary)"}


@action("company.set_cv_include")
def company_set_cv_include(company: str, bullet_id: str, include: bool = True):
    """Force an achievement (bullet id) into this application's CVs, never cut to fit the page (include=false
    stops forcing it). For this application only: the master CV's pin is unchanged. Regenerate the CV to apply."""
    c = companies.resolve(company)
    master_cv._find_bullet(master_cv.get_master(), bullet_id)  # NotFound for an unknown id
    ids = [i for i in c.cv_include if i != bullet_id] + ([bullet_id] if include else [])
    c = companies.update_company(c.id, cv_include=ids)
    return {"company": c.name, "cv_include": c.cv_include}


@action("company.set_cv_title", confirm=True)
def company_set_cv_title(company: str, title: str):
    """Use this title under the name in this application's CVs instead of the job's title (which CVs use by
    default); the master CV is unchanged. An empty title goes back to the job's title. Regenerate the CV afterwards."""
    c = companies.resolve(company)
    c = companies.update_company(c.id, cv_title=" ".join(title.split())[:80])
    return {"company": c.name, "cv_title": c.cv_title or "(master CV title)"}


@action("analytics.summary")
def analytics_summary():
    """Job search analytics: totals (applied, responses, interviews, technical interviews, offers, closed),
    rates, funnel, at which step closed applications stopped, timing (days searching, to first response,
    to offer, to close), weekly applications, and results by source and work model."""
    return analytics.summary()


# ================================================================= process
# `company`: application id, or company name / position. `step`: step id, or step name.

def _step_changed(c) -> dict:
    """After a step change: a technical interview just scheduled starts its interview prep (see prep.auto_start)."""
    prep.auto_start(c)
    return _process_view(c)


def _process_view(c) -> dict:
    return {**_app(c), "outline": c.process.outline(), "nodes": c.process.nodes, "edges": c.process.edges}


@action("process.get")
def process_get(company: str):
    """An application's process graph: an `outline` (one line per step: state, name, date, next steps, id),
    plus the raw nodes and edges. Every step also leads to the Rejected/Closed node. Status and stage are derived."""
    return _process_view(companies.resolve(company))


@action("process.add_step")
def process_add_step(company: str, name: str, after: Optional[str] = None, before: Optional[str] = None,
                     state: NodeState = "todo", date: Optional[str] = None, notes: str = "",
                     kind: Literal["step", "offer"] = "step", time: Optional[str] = None, location: str = "",
                     duration_min: int = 60):
    """Add a step. With `after` and `before` (two linked steps): inserted between them. With only `after`: right
    after it. With only `before`: right before it. With neither: at the end. state: todo (off), planned
    (scheduled, give a date), done. kind "offer": reaching this step means an offer. For a scheduled interview
    also give `time` ("14:30", local), `duration_min` and `location` (address or video link): it can then be
    added to the user's calendar."""
    c, _ = companies.add_step(company, name, after=after, before=before, state=state, date=date, notes=notes, kind=kind,
                              time=time, location=location, duration_min=duration_min)
    return _step_changed(c)


@action("process.add_branch")
def process_add_branch(company: str, from_step: str, name: str, join: Optional[str] = None,
                       state: NodeState = "todo", date: Optional[str] = None, notes: str = ""):
    """Add an alternative path starting at `from_step` (e.g. a parallel take-home test). With `join`, the branch
    leads back into that step; without it, the branch is a dead end."""
    c, _ = companies.add_step(company, name, branch_from=from_step, join=join, state=state, date=date, notes=notes)
    return _step_changed(c)


@action("process.update_step")
def process_update_step(company: str, step: str, fields: StepPatch):
    """Change a step: name, state (todo/planned/done), date, time ("14:30"), duration_min, location (address or
    video link), notes, kind (step/offer)."""
    c, _ = companies.update_step(company, step, **_patch(fields))
    return _step_changed(c)


@action("process.set_state")
def process_set_state(company: str, step: str, state: NodeState, date: Optional[str] = None):
    """Mark a step todo (off), planned (scheduled) or done (on). Typical: process.set_state(company="Nimbus",
    step="Applied", state="done") once the user says they applied."""
    fields = {"state": state, **({"date": date} if date else {})}
    c, _ = companies.update_step(company, step, **fields)
    return _step_changed(c)


@action("process.close")
def process_close(company: str, reason: str = "", date: Optional[str] = None):
    """The job is closed for this application (rejected, withdrawn, position filled): turns the
    Rejected/Closed node on (red) and the status becomes Rejected."""
    c = companies.resolve(company)
    fields = {"state": "done", "date": date or companies.today(), **({"notes": reason} if reason else {})}
    c, _ = companies.update_step(c.id, c.process.closed.id, **fields)
    return _step_changed(c)


@action("process.reopen")
def process_reopen(company: str):
    """Undo process.close: turns the Rejected/Closed node off again."""
    c = companies.resolve(company)
    c, _ = companies.update_step(c.id, c.process.closed.id, state="todo")
    return _step_changed(c)


@action("process.delete_step", confirm=True)
def process_delete_step(company: str, step: str):
    """Delete a step; the steps before it are reconnected to the steps after it."""
    c, _ = companies.delete_step(company, step)
    return _step_changed(c)


@action("process.reset", confirm=True)
def process_reset(company: str):
    """Replace an application's process with the default one (all steps off)."""
    c, _ = companies.reset_process(company)
    return _step_changed(c)


# ================================================================ master CV

@action("cv.master.get")
def cv_master_get():
    """The whole master CV: profile, experiences with their tagged bullets, education, skills."""
    return master_cv.get_master()


@action("cv.headline.list")
def cv_headline_list():
    """The title variants of the master CV (title + summary, one default). For each application the closest one
    to the job is used automatically; see cv.headline.for_application."""
    return [h.model_dump() for h in headlines.variants()]


@action("cv.headline.add", confirm=True)
def cv_headline_add(title: str, summary: str = "", keywords: list[str] = [], make_default: bool = False):
    """Add a title variant (e.g. "Data Scientist") with its own summary, built only from the CV's facts.
    `keywords`: hint words that point to it (e.g. "machine learning", "experimentation")."""
    return headlines.add(title, summary, keywords, make_default).model_dump()


@action("cv.headline.update", confirm=True)
def cv_headline_update(id: str, title: Optional[str] = None, summary: Optional[str] = None,
                       keywords: Optional[list[str]] = None, make_default: Optional[bool] = None):
    """Change a title variant (its title, summary, hint words), or make it the default."""
    return headlines.update(id, title, summary, keywords, make_default).model_dump()


@action("cv.headline.delete", confirm=True)
def cv_headline_delete(id: str):
    """Delete a title variant (at least one stays)."""
    return [h.model_dump() for h in headlines.delete(id)]


@action("cv.headline.for_application")
def cv_headline_for_application(company: str):
    """What an application's CV shows at the top: the title (`title_from`: the application's own, the job's title,
    or the variant's when the job has none) and the summary (the application's own, or the variant's: pinned,
    closest to the job, or default)."""
    return headlines.effective(company)


@action("company.set_cv_headline")
def company_set_cv_headline(company: str, headline_id: str = ""):
    """Use this title variant for this application's CVs ("" = the closest one, picked automatically)."""
    return headlines.pin(company, headline_id)


@action("profile.get")
def profile_get():
    """The user: name (first and last), greeting name, LinkedIn,
    email, what they look for (target roles, seniority, locations,
    work models, max commute), salary expectation, notice period, start date, interview language, weekly goals."""
    return profile.get()


@action("profile.update")
def profile_update(first_name: Optional[str] = None, last_name: Optional[str] = None, name: Optional[str] = None,
                   preferred_name: Optional[str] = None, linkedin: Optional[str] = None,
                   target_roles: Optional[list[str]] = None, seniority: Optional[str] = None,
                   locations: Optional[list[str]] = None, work_models: Optional[list[WorkModel]] = None,
                   max_commute_min: Optional[int] = None, salary_expectation: Optional[str] = None,
                   notice_period: Optional[str] = None, available_from: Optional[str] = None,
                   interview_language: Optional[str] = None, goals: Optional[WeeklyGoals] = None,
                   study: Optional[StudyHours] = None):
    """Change the user's profile (only the fields given): first and last name and LinkedIn (the master CV
    header's; they name the CV files, first-last-position-company; `name` = a full name, split at the first
    space, prefer first_name and last_name), greeting name,
    what they look for (target roles, seniority, locations, work models, max commute: jobs outside get a
    warning), salary expectation (default for new applications), notice period, available from (YYYY-MM-DD),
    interview prep language, weekly goals (applications, practice answers), and `study`: working hours (never
    used for study), study hours on work days, weekend days (Python weekdays, Monday = 0; default Friday and
    Saturday), whether they're free to study on weekends and when, and the most study per day (minutes)."""
    return profile.update(first_name=first_name, last_name=last_name, name=name, linkedin=linkedin,
                          preferred_name=preferred_name, target_roles=target_roles,
                          seniority=seniority, locations=locations, work_models=work_models,
                          max_commute_min=max_commute_min, salary_expectation=salary_expectation,
                          notice_period=notice_period, available_from=available_from,
                          interview_language=interview_language, goals=goals, study=study)


@action("company.referral")
def company_referral(company: str, referee: Optional[str] = None, asked: bool = True):
    """Referral tracking for an application: set who refers you (`referee`), and/or record that you asked or
    reminded them today (asked=true; Today suggests a reminder 3 days later while you haven't applied).
    asked=false clears it."""
    c = companies.resolve(company)
    fields = {"referee": " ".join(referee.split())} if referee is not None else {}
    if asked and not (fields.get("referee") or c.referee):
        raise ValueError("Add the referee's name first")
    fields["referral_asked_at"] = companies.today() if asked else None
    c = companies.update_company(c.id, **fields)
    if asked:
        companies.log_update(c.id, by="me", note=f"Asked {c.referee} for a referral")
    return companies.get_company(c.id).view()


@action("cv.profile.update", confirm=True)
def cv_profile_update(fields: ProfilePatch):
    """Update the CV header/profile (first_name and last_name: `name` follows them; contacts).
    The title and summary are title variants (cv.headline.*):
    a title/summary given here changes the default variant."""
    changes = _patch(fields)
    title, summary = changes.pop("title", None), changes.pop("summary", None)
    if title is not None or summary is not None:
        d = headlines.default()
        if d:
            headlines.update(d.id, title, summary)
        else:
            headlines.add(title or "Title", summary or "", make_default=True)
    return master_cv.update_profile(**changes) if changes else master_cv.get_master().profile


@action("cv.experience.add")
def cv_experience_add(company: str, role: str = "", location: str = "", start: str = "",
                      end: str = "", position: Optional[int] = None):
    """Add a job/experience to the master CV (bullets are added separately with cv.bullet.add)."""
    return master_cv.add_experience(company, role, location, start, end, position)


@action("cv.experience.update")
def cv_experience_update(id: str, fields: ExperiencePatch):
    """Update an experience's company, role, location or dates."""
    return master_cv.update_experience(id, **_patch(fields))


@action("cv.experience.delete", confirm=True)
def cv_experience_delete(id: str):
    """Delete an experience and all its bullets."""
    master_cv.delete_experience(id)
    return {"deleted": id}


@action("cv.experience.move")
def cv_experience_move(id: str, offset: int):
    """Reorder experiences (-1 = up, +1 = down)."""
    master_cv.move_experience(id, offset)
    return {"moved": id}


@action("cv.bullet.add", confirm=True)
def cv_bullet_add(experience_id: str, text: str, keywords: list[str] = [], pinned: bool = False,
                  priority: int = 0, lead: str = "", group: str = ""):
    """Add a bullet point under an experience. `keywords` tag it for matching job descriptions;
    `pinned` bullets are always included; higher `priority` bullets are kept longer when trimming to one page.
    Optional `lead` is a bold label shown before the text ("Copilot:"); `group` puts the bullet under a
    project sub-heading inside the experience."""
    return master_cv.add_bullet(experience_id, text, keywords, pinned, priority, lead, group)


@action("cv.bullet.update", confirm=True)
def cv_bullet_update(id: str, fields: BulletPatch):
    """Update a bullet's text, keywords, pinned flag or priority."""
    return master_cv.update_bullet(id, **_patch(fields))


@action("cv.variant.add", confirm=True)
def cv_variant_add(bullet_id: str, text: str, lead: Optional[str] = None, keywords: Optional[list[str]] = None):
    """Add another wording to an achievement (same facts, different keywords). A CV uses at most one wording
    per achievement: the one covering most of the job's keywords. Lead defaults to the main wording's;
    keywords default to the known keywords written in the text."""
    return master_cv.add_variant(bullet_id, text, lead, keywords)


@action("cv.variant.update", confirm=True)
def cv_variant_update(id: str, fields: VariantPatch):
    """Update a wording's text, lead or keywords."""
    return master_cv.update_variant(id, **_patch(fields))


@action("cv.variant.delete", confirm=True)
def cv_variant_delete(id: str):
    """Delete one wording of an achievement (the main wording stays)."""
    master_cv.delete_variant(id)
    return {"deleted": id}


@action("cv.variant.draft")
def cv_variant_draft(bullet_id: str, keywords: list[str] = [], count: int = 3, facts: str = ""):
    """Ask the AI for new wordings of an achievement that work in `keywords` where truthful. Nothing is saved:
    show the drafts to the user; save the ones they accept with cv.variant.add. Drafts adding numbers not in
    the original (or in `facts`, extra true details from the user) are rejected automatically."""
    return wording.draft_variants(bullet_id, keywords, count, facts)


@action("cv.bullet.delete", confirm=True)
def cv_bullet_delete(id: str):
    """Delete a bullet point."""
    master_cv.delete_bullet(id)
    return {"deleted": id}


@action("cv.education.set", confirm=True)
def cv_education_set(items: list[Education]):
    """Replace the education list."""
    return master_cv.set_education([i.model_dump() for i in items])


@action("cv.languages.set", confirm=True)
def cv_languages_set(languages: list[str]):
    """Replace the spoken languages list, e.g. ["French (Native)", "English (Fluent)"]."""
    return master_cv.set_languages(languages)


@action("cv.skills.set", confirm=True)
def cv_skills_set(groups: list[SkillGroup]):
    """Replace the skill groups (e.g. {name: 'Languages', items: ['Python', 'SQL']})."""
    return master_cv.set_skills([g.model_dump() for g in groups])


# =================================================================== import

@action("cv.import.parse")
def cv_import_parse(file_path: Optional[str] = None, text: Optional[str] = None):
    """Read an existing CV (.docx, .pdf, .txt: give `file_path`, or paste `text`) and structure it
    with the LLM into a draft master CV (profile, experiences with keyword-tagged bullets, education,
    skills). Nothing is saved: review the draft, then call cv.import.apply."""
    if not (file_path or text):
        raise ValueError("Give file_path or text")
    if file_path:
        path = Path(file_path).expanduser().resolve()
        if users.current() and not path.is_relative_to(users.data_dir().resolve()):  # friends: own files only
            raise ValueError("Upload the file first: only your own uploaded files can be read")
    draft = cv_import.parse_cv(text or cv_import.extract_text(path))
    return {"draft": draft, "stats": {
        "experiences": len(draft.experiences),
        "bullets": sum(len(e.bullets) for e in draft.experiences),
        "education": len(draft.education),
        "skills": sum(len(g.items) for g in draft.skills)}}


@action("cv.import.apply", confirm=True)
def cv_import_apply(draft: MasterCV, mode: ImportMode = "merge"):
    """Save a draft from cv.import.parse into the master CV. `merge` adds what is new and fills empty
    profile fields; `replace` overwrites profile, experiences, education and skills."""
    return cv_import.apply_import(draft, mode)


# ================================================================= keywords

@action("cv.keywords.list")
def cv_keywords_list():
    """All keywords known to the master CV (bullet tags, skills and extra keywords)."""
    return master_cv.keyword_vocabulary()


@action("cv.keywords.match_job")
def cv_keywords_match_job(text: str, use_ai: bool = True, company: Optional[str] = None):
    """Best way to pick CV keywords: match a job description against the master CV. Returns `matches`
    (job requirement -> terms written in your CV that cover it, via exact words, synonym groups and,
    if an OpenAI key is set, AI mapping), `gaps` (requirements nothing in your CV covers) and
    `keywords` (ready to pass to cv.generate).
    Give `company` when `text` is that application's job description: the requirements are kept for its
    match score (cv.match_score)."""
    result = jd_match.match_job_description(text, use_ai)
    if company:
        cv_score.remember(companies.resolve(company).id, text, result)
    return result


@action("cv.match_score")
def cv_match_score(company: str, use_ai: bool = False):
    """Match score of the application's latest generated CV: `score` = % of the job's requirements written in
    that CV (required ones weigh double), `best` = % your master CV covers (the most tailoring can reach), and
    the requirements `covered`, `cut` (in your master CV but left out of this CV, usually to fit one page) and
    `missing` (nothing in your CV covers them: don't claim them). use_ai=true reads the requirements with AI
    (better, costs a call; then kept). Computed automatically each time a CV is generated."""
    return cv_score.for_company(company, use_ai)


@action("cv.keywords.suggest")
def cv_keywords_suggest(word: str):
    """Closest terms written in your CV for a typed keyword (fixes typos and spelling variants)."""
    return jd_match.suggest(word)


@action("cv.keywords.detect")
def cv_keywords_detect(text: str, lead: str = ""):
    """Known keywords that appear in a bullet's text: use them as the bullet's tags."""
    return master_cv.detect_keywords(text, lead)


@action("cv.keywords.missing")
def cv_keywords_missing():
    """Bullet tags that are not written in the bullet's text (they never match: rewrite or remove)."""
    return master_cv.missing_tags()


@action("cv.synonyms.set", confirm=True)
def cv_synonyms_set(groups: list[list[str]]):
    """Replace the synonym groups, e.g. [["LLM", "Large Language Model"], ["RAG", "Retrieval-Augmented Generation"]].
    A job description using any term of a group selects bullets containing any other term of it."""
    return master_cv.set_synonyms(groups)


@action("cv.keywords.set_extra", confirm=True)
def cv_keywords_set_extra(keywords: list[str]):
    """Replace the list of extra keywords to watch for in job descriptions."""
    return master_cv.set_extra_keywords(keywords)


@action("cv.keywords.extract")
def cv_keywords_extract(text: str):
    """Find which known keywords appear in a job description (most frequent first)."""
    return master_cv.extract_keywords(text)


# =============================================================== generation

@action("cv.preview")
def cv_preview(keywords: list[str], keep_unmatched_roles: bool = True, template: Optional[str] = None,
               required: list[str] = [], company: Optional[str] = None, include: list[str] = []):
    """Dry run: every achievement in the wording that best fits these keywords, and which would be cut to
    fit one page (those matching no keyword go first). `required` keywords (must-haves) weigh double. With `company`,
    that application's tailored summary and forced achievements (cv_include) are used. `include`: more achievement
    ids forced in (`forced`, never cut)."""
    c = companies.resolve(company) if company else None
    cv = cv_builder.select_bullets(keywords, keep_unmatched_roles, required,
                                   include=[*(c.cv_include if c else []), *include])
    cv_builder.use_summary(cv, c)
    cv_builder.fit_estimate(cv, template)
    return cv


@action("cv.generate_general")
def cv_generate_general(template: Optional[str] = None):
    """Generate a general CV (Word + PDF), not tailored to any job: e.g. for a headhunter or networking.
    All achievements in their main wording, cut to one page by priority. Saved in the "Master CV" folder."""
    return cv_builder.generate_general(template)


@action("cv.generate")
def cv_generate(keywords: list[str], company_id: Optional[str] = None, company_name: Optional[str] = None,
                template: Optional[str] = None, export_pdf: bool = True, keep_unmatched_roles: bool = True,
                required: list[str] = [], include: list[str] = []):
    """Build a full one-page CV: every achievement in its most relevant wording (`required` keywords weigh
    double), cutting the least relevant only if it doesn't fit.
    Saved as Word + PDF in <output>/<Company>/ and attached to the company. The application's forced achievements
    (company.set_cv_include) and `include` (more achievement ids) are never cut."""
    result = cv_builder.generate_cv(keywords, company_id, company_name, template, export_pdf, keep_unmatched_roles,
                                    required, include=include)
    return {**result, **summary_service.proposal_after_cv(result.get("company_id"), result)}


# ===================================================================== web

@action("job.remember_board")
def job_remember_board(url: str, platform: Literal["greenhouse", "lever", "ashby", "smartrecruiters"], account: str):
    """Remember that a company's site publishes its jobs through a job board account (e.g. orbitex.com uses
    Greenhouse "orbitexlabs"): its job links are then read through the board's public API."""
    jobs.remember(url, platform, account)
    return {"site": jobs.site_of(url), "platform": platform, "account": account}


@action("job.fetch_url")
def job_fetch_url(url: str):
    """Read a job posting from a link and return its text (to then match it or create the company): through the
    job board's official API when possible (Greenhouse, Lever, Ashby, SmartRecruiters), else the page. Some
    sites refuse automated reading or need a login (e.g. LinkedIn): then don't retry; ask the user to send the
    job from their own browser (select the text → Share → Ezri on a phone, the “Send to Ezri” bookmark on a
    computer) or to paste the text."""
    return jobs.read(url)


# ================================================================= linkedin
# From LinkedIn's own data export (no API exists for your network): the user uploads the zip in the app.

@action("linkedin.status")
def linkedin_status():
    """Whether a LinkedIn export was imported: number of contacts, profile, when, and `export_url` (the LinkedIn
    page where the user requests a new export: Connections + Profile, it arrives by email in ~10 minutes, then
    they upload the zip on the Profile page with "Update my LinkedIn contacts")."""
    return linkedin.status()


@action("linkedin.contacts")
def linkedin_contacts(company: str):
    """For an application: your 1st-degree LinkedIn contacts at that company (recruiters first, then those in a
    role close to the job, then the most recent), and LinkedIn `search` links for people there: `second_degree`
    (a contact of yours can introduce you: ask them), `first_degree` (also catches people who joined since the
    export) and `recruiters`. Use it to suggest a referee (then company.referral) and draft the message."""
    return linkedin.for_application(company)


@action("linkedin.find")
def linkedin_find(query: str):
    """Search your LinkedIn contacts by name, company or position words (e.g. "Dana", "Northwind", "recruiter")."""
    return linkedin.find(query)


@action("linkedin.profile")
def linkedin_profile():
    """Your LinkedIn profile from the last import (headline, summary, positions with descriptions, education,
    skills): e.g. to compare its wording with the master CV or suggest a better headline/summary."""
    p = linkedin.get_profile()
    if p is None:
        raise ValueError("No LinkedIn profile imported yet: ask the user to import their LinkedIn data export.")
    return p


@action("linkedin.check")
def linkedin_check():
    """Differences between the LinkedIn profile and the master CV: experiences or schools on one side only,
    dates and job titles that differ, CV skills missing from LinkedIn, headline vs CV titles. severity "fix"
    = recruiters will notice; "info" = may be on purpose. LinkedIn can't be edited from here: suggest the
    exact text to paste on LinkedIn, or (with the user's OK) fix the master CV."""
    return linkedin.check()


# ================================================================ workflows

@action("workflow.new_application")
def workflow_new_application(name: str, job_description: str = "", position: str = "",
                             location: str = "", website: str = "", seniority: str = "",
                             qualities: list[str] = [], contact: Optional[Contact] = None, referee: str = "",
                             keywords: list[str] = [], work_model: WorkModel = "", address: str = "",
                             job_url: str = "", source_type: SourceType = "", source_detail: str = "",
                             salary_expectation: str = "", salary_posted: str = "",
                             generate_cv: bool = True):
    """One-shot for agents: create (or reuse, by name) the company, store the job description,
    match it against the master CV (cv.keywords.match_job, merged with `keywords`), and generate the tailored CV.
    Returns the company, `covered` requirements, `gaps` (requirements the CV doesn't cover) and the CV files.
    Fill work_model, job_url (the posting link), salary_posted and the source when you know them; the job
    description is saved as Job_Description.md next to the CV. Never marks the application as applied.
    When the CV's summary doesn't fit the job, it is reviewed and its rewrite comes back in `proposals` (in the
    chat: already shown to the user for approval)."""
    match = jd_match.match_job_description(job_description) if job_description else None
    merged = list(dict.fromkeys([*keywords, *(match["keywords"] if match else [])]))
    details = dict(position=position, location=location, website=website, seniority=seniority,
                   qualities=qualities, contact=contact, referee=referee,
                   job_description=job_description, keywords=merged, work_model=work_model, address=address,
                   job_url=job_url, source_type=source_type, source_detail=source_detail,
                   salary_expectation=salary_expectation, salary_posted=salary_posted)
    company = companies.find_by_name(name)
    if company:
        given = {k: v for k, v in details.items() if v not in ("", [], None)}
        company = companies.update_company(company.id, **given)
    else:
        company = companies.create_company(name=name, **{**details, "contact": contact or Contact()})
    if match:
        cv_score.remember(company.id, job_description, match)
    result = {"company": company.view(), "keywords": merged}
    if match:
        result["covered"] = [{"requirement": m.jd_term, "cv_terms": m.keywords} for m in match["matches"]]
        result["gaps"] = [{"requirement": g.jd_term, "required": g.must_have} for g in match["gaps"]]
    if generate_cv:
        required = [k for m in (match["matches"] if match else []) if m.must_have for k in m.keywords]
        result["cv"] = cv_builder.generate_cv(merged, company_id=company.id, required=required)
        result.update(summary_service.proposal_after_cv(company.id, result["cv"]))
    return result


# ==================================================================== chat
# The chat assistant uses every action above as a tool (except chat.* itself).

@action("chat.send")
def chat_send(message: str, chat_id: Optional[str] = None):
    """Send a message to the assistant (new chat if no chat_id). Returns the whole chat."""
    return assistant.send(message, chat_id)


@action("chat.list")
def chat_list():
    """Previous conversations, most recent first."""
    return assistant.list_chats()


@action("chat.get")
def chat_get(id: str):
    """One conversation with all its messages and tool events."""
    return assistant.get_chat(id)


@action("chat.resolve")
def chat_resolve(chat_id: str, event_id: str, approve: bool):
    """Approve or reject an action the assistant proposed (deletes/overwrites), then let it continue."""
    return assistant.resolve(chat_id, event_id, approve)


@action("chat.rename")
def chat_rename(id: str, title: str):
    """Rename a conversation (the assistant also renames its own chat to "<Company> · <stage>")."""
    return assistant.rename_chat(id, title)


@action("chat.feedback")
def chat_feedback(chat_id: str, turn_id: str, rating: Optional[Literal["up", "down"]] = None,
                  reasons: Optional[list[str]] = None, comment: str = ""):
    """The user's thumbs up/down on an assistant reply (the reply's turn_id); rating null removes it. A thumbs down
    takes reasons (misunderstood, unfinished, wrong, wrong_change, tone, slow, other) and an optional comment."""
    return assistant.give_feedback(chat_id, turn_id, rating, reasons, comment)


@action("chat.archive")
def chat_archive(id: str, archived: bool = True):
    """Archive a conversation (out of the main list, kept and reopenable), or bring it back with archived=false."""
    return assistant.archive_chat(id, archived)


@action("chat.compact")
def chat_compact(id: str):
    """Compact a long conversation: the assistant continues from a short summary of it (cheaper, faster replies).
    The messages stay visible. Also typed as /compact in the chat."""
    return assistant.compact(id)


@action("chat.delete")
def chat_delete(id: str):
    """Delete a conversation for good."""
    assistant.delete_chat(id)
    return {"deleted": id}
