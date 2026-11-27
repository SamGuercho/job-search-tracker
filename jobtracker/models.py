"""Data models shared by storage, services, API and CLI."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field, field_validator, model_validator

from .models_base import new_id, today  # noqa: E402  (re-exported)
from .process import STATUSES, Process, default_process, from_legacy  # noqa: E402, F401  (STATUSES re-exported)

# ---------------------------------------------------------------- companies

UpdateSide = Literal["me", "them"]
WorkModel = Literal["", "On-site", "Hybrid", "Remote"]
SourceType = Literal["", "Job board", "Company website", "Headhunter", "Referral / network", "Other"]
WORK_MODELS = ["On-site", "Hybrid", "Remote"]
SOURCE_TYPES = ["Job board", "Company website", "Headhunter", "Referral / network", "Other"]


class Contact(BaseModel):
    name: str = ""
    role: str = ""
    email: str = ""
    phone: str = ""
    linkedin: str = ""


class Requirement(BaseModel):
    """One requirement of a job description and the terms written in your CV that cover it."""
    term: str
    keywords: list[str] = []   # empty = nothing in your master CV covers it
    required: bool = False


class JobMatch(BaseModel):
    """The job description's requirements, kept so scoring a CV again costs nothing."""
    text_hash: str              # of the job description they were read from
    ai_used: bool = False
    version: int = 0            # how they were matched (cv_score.MATCH_VERSION): older ones are read again
    requirements: list[Requirement] = []


class MatchScore(BaseModel):
    """How well a generated CV covers the job's requirements (required ones weigh double)."""
    score: int                  # % covered by this CV
    best: int                   # % your master CV could cover (the most a tailored CV can reach)
    covered: list[Requirement] = []   # written in this CV with the job's word (or a form of it)
    reword: list[Requirement] = []    # in this CV with other words only: an ATS looking for the job's word misses it
    cut: list[Requirement] = []       # in your master CV, not in this CV (left out to fit one page)
    missing: list[Requirement] = []   # nothing in your master CV covers it
    ai_used: bool = False
    checked_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class GeneratedDocument(BaseModel):
    kind: str = "cv"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    keywords: list[str] = []
    docx: str | None = None
    pdf: str | None = None
    match: MatchScore | None = None


NewsChannel = Literal["email", "call", "in_person", "linkedin", "other"]


class NewsReport(BaseModel):
    """How the user heard of news they entered themselves (asked after a step change)."""
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    step: str = ""               # the step they changed
    step_type: str = ""
    channel: NewsChannel
    mail: str = ""               # for "email": found (Ezri had it), missed, or "" (no mailbox connected)


class Company(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    position: str = ""
    status: str = "Not applied"   # derived from `process`, never set by hand
    contact: Contact = Field(default_factory=Contact)
    referee: str = ""
    location: str = ""
    address: str = ""             # optional exact address, for the map
    work_model: WorkModel = ""
    website: str = ""
    other_names: list[str] = []   # how emails may name the company too ("Zafran Security", its name in Hebrew)
    email_domains: list[str] = [] # domains its emails come from, besides its website's ("zafran-sec.com")
    news_log: list[NewsReport] = []  # how the user heard of the news they entered (email, call…)
    logo_file: str | None = None  # the company's logo in its folder (relative to the output folder), see services/logos.py
    logo_source: str = ""         # where it came from: the image URL, or "uploaded" (then never replaced automatically)
    logo_checked_at: str | None = None
    job_url: str = ""             # link to the job posting
    source_type: SourceType = ""  # where the application comes from
    source_detail: str = ""       # e.g. "LinkedIn", "Dana from TalentCo", "John (ex-colleague)"
    salary_expectation: str = ""  # what you ask for, free text ("38k ILS/month")
    salary_posted: str = ""       # range published by the company, if any
    seniority: str = ""
    qualities: list[str] = []
    notes: str = ""
    job_description: str = ""
    job_description_file: str | None = None   # saved copy in the company folder (relative to the output folder)
    job_description_saved_at: str | None = None
    keywords: list[str] = []
    job_match: JobMatch | None = None   # requirements read from job_description (see services/cv_score.py)
    cv_summary: str = ""          # tailored CV summary for this application ("" = master CV summary)
    cv_title: str = ""            # the title under your name on this application's CV ("" = its title variant's)
    cv_headline: str = ""         # the title variant pinned for this application ("" = the closest, automatically)
    cv_include: list[str] = []    # achievement ids always in this application's CVs (like pinned, for it only)
    last_update: str = Field(default_factory=today)
    last_update_by: UpdateSide = "me"
    referral_asked_at: str | None = None   # when you last asked the referee to refer you (Today reminds you after 3 days)
    snooze_until: str | None = None   # Today's nudges (follow-up, no answer, still interested?) hidden until this date
    process: Process = Field(default_factory=default_process)
    documents: list[GeneratedDocument] = []
    created_at: str = Field(default_factory=today)

    @model_validator(mode="before")
    @classmethod
    def _migrate_steps(cls, data):
        """Saved data from before the process graph had a flat `steps` list."""
        if isinstance(data, dict) and "process" not in data and "steps" in data:
            data = dict(data)
            data["process"] = from_legacy(data.pop("steps") or [], data.get("status", ""))
        return data

    @model_validator(mode="after")
    def _sync_status(self):
        self.status = self.process.status()
        return self

    def days_since_update(self) -> int | None:
        try:
            return (date.today() - date.fromisoformat(self.last_update)).days
        except ValueError:
            return None

    def view(self) -> dict:
        """Serialised form returned to clients, with computed fields."""
        data = self.model_dump(mode="json")
        data["days_since_update"] = self.days_since_update()
        data["stage"] = self.process.stage()
        return data


class CompanyBook(BaseModel):
    companies: list[Company] = []


# ---------------------------------------------------------------- master CV

class Variant(BaseModel):
    """Another wording of the same achievement (same facts, different keywords)."""
    id: str = Field(default_factory=new_id)
    text: str
    lead: str = ""
    keywords: list[str] = []


class Bullet(BaseModel):
    """One achievement. `text`/`lead`/`keywords` are its main wording; `variants`
    are alternative wordings. A CV uses at most one wording per achievement."""
    id: str = Field(default_factory=new_id)
    text: str
    lead: str = ""         # optional bold label before the text, e.g. "Copilot:"
    group: str = ""        # optional project/sub-heading inside the experience
    keywords: list[str] = []
    pinned: bool = False   # always included, never removed to fit the page
    priority: int = 0      # higher = kept longer when trimming to one page
    variants: list[Variant] = []

    def wordings(self) -> list[Bullet | Variant]:
        return [self, *self.variants]


class Experience(BaseModel):
    id: str = Field(default_factory=new_id)
    company: str
    role: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    bullets: list[Bullet] = []


class Project(BaseModel):
    """Important work done outside a company (personal, academic, freelance, open source): an experience
    without an employer. Its bullets are achievements like an experience's (keywords, wordings, pinned)."""
    id: str = Field(default_factory=new_id)
    name: str
    role: str = ""         # optional role or context, e.g. "Master's thesis", "Open-source maintainer"
    link: str = ""         # optional URL (repository, demo, paper)
    start: str = ""
    end: str = ""
    bullets: list[Bullet] = []


class Education(BaseModel):
    id: str = Field(default_factory=new_id)
    school: str
    degree: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    details: str = ""            # one item per line (thesis, GPA, honours…): each printed on its own line
    details_inline: bool = False  # True: printed on the header line instead, "Degree, School • details   dates"

    def detail_lines(self) -> list[str]:
        return [line.strip() for line in self.details.splitlines() if line.strip()]


class SkillGroup(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    items: list[str] = []


class WeeklyGoals(BaseModel):
    applications: int = 0        # applications sent per week (0 = no goal)
    practice_answers: int = 0    # practice answers per week


HHMM = r"^(([01]\d|2[0-3]):[0-5]\d)?$"  # "19:30", or "" for not set


class StudyHours(BaseModel):
    """When study sessions can be planned (services/learning.py). Times are local, "HH:MM"."""
    work_start: str = Field("", pattern=HHMM)   # working hours on work days ("" = not working): never used for study
    work_end: str = Field("", pattern=HHMM)
    weekend_days: list[int] = [4, 5]            # Python weekdays (Monday = 0): Friday and Saturday
    study_start: str = Field("", pattern=HHMM)  # when you can study on work days ("" = not set)
    study_end: str = Field("", pattern=HHMM)
    weekend_free: bool = False                  # you can also study on weekend days
    weekend_start: str = Field("10:00", pattern=HHMM)
    weekend_end: str = Field("14:00", pattern=HHMM)
    max_per_day_min: int = 90                   # at most this much study a day


class UserProfile(BaseModel):
    """Settings about the person (services/profile.py); name and LinkedIn stay on the master CV profile."""
    preferred_name: str = ""     # how Today greets you ("" = the first word of your name)
    # What you're looking for: jobs outside it get a warning (rules.fit)
    target_roles: list[str] = []     # "Data Scientist", "AI Engineer"
    seniority: str = ""              # "Senior", "Staff"… (for the assistant: not a filter)
    locations: list[str] = []        # cities / areas you'd work in ("Tel Aviv", "Herzliya")
    work_models: list[WorkModel] = []  # On-site / Hybrid / Remote you accept ([] = any)
    max_commute_min: int | None = None
    # Practical
    salary_expectation: str = ""     # default for new applications ("38k ILS/month")
    notice_period: str = ""          # "1 month", "immediately"
    available_from: str = ""         # earliest start date (YYYY-MM-DD)
    interview_language: str = "English"  # default language of interview preps
    goals: WeeklyGoals = Field(default_factory=WeeklyGoals)
    study: StudyHours = Field(default_factory=StudyHours)


# ----------------------------------------------------------------- LinkedIn
# From LinkedIn's own data export (services/linkedin.py): your 1st-degree connections and your profile.

class LinkedInContact(BaseModel):
    first_name: str = ""
    last_name: str = ""
    url: str = ""
    email: str = ""           # usually empty: people choose whether to share it
    company: str = ""         # where they worked when you downloaded the export
    position: str = ""
    connected_on: str = ""    # YYYY-MM-DD

    @property
    def name(self) -> str:
        return f"{self.first_name} {self.last_name}".strip()


class LinkedInPosition(BaseModel):
    company: str
    title: str = ""
    description: str = ""
    location: str = ""
    start: str = ""           # YYYY-MM or YYYY
    end: str = ""             # "" = current


class LinkedInEducation(BaseModel):
    school: str
    degree: str = ""
    start: str = ""
    end: str = ""


class LinkedInProfile(BaseModel):
    headline: str = ""
    summary: str = ""
    positions: list[LinkedInPosition] = []
    education: list[LinkedInEducation] = []
    skills: list[str] = []


class LinkedInBook(BaseModel):
    contacts: list[LinkedInContact] = []
    contacts_imported_at: str | None = None
    profile: LinkedInProfile | None = None
    profile_imported_at: str | None = None


class Profile(BaseModel):
    name: str = ""          # the CV header: always "first_name last_name" once either is set
    first_name: str = ""
    last_name: str = ""     # first and last name name the CV files (services/cv_builder.cv_file_stem)
    title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    github: str = ""        # printed after LinkedIn on the CV
    website: str = ""       # personal website
    summary: str = ""

    @model_validator(mode="after")
    def _split_name(self):
        """A header from before first/last name: its first word is the first name, the rest the last name."""
        if not (self.first_name or self.last_name) and self.name.split():
            first, *rest = self.name.split()
            self.first_name, self.last_name = first, " ".join(rest)
        if self.first_name or self.last_name:
            self.name = " ".join(f"{self.first_name} {self.last_name}".split())
        return self


# Terms that mean the same thing. A job description saying any of them selects
# bullets that contain any other. Editable per user (Master CV page).
DEFAULT_SYNONYMS = [
    ["LLM", "Large Language Model", "GenAI", "Generative AI"],
    ["RAG", "Retrieval-Augmented Generation", "Retrieval Augmented Generation"],
    ["NLP", "Natural Language Processing"],
    ["Machine Learning", "ML"],
    ["Deep Learning", "DL", "Neural Network"],
    ["Agents", "AI Agents", "Agentic", "Agentic Workflows", "Multi-Agent"],
    ["LLM Evaluation", "LLM-as-a-Judge", "Evals"],
    ["CI/CD", "Continuous Integration", "GitLab CI", "GitHub Actions"],
    ["Vector Stores", "Vector Database", "Vector DB", "Pinecone", "Azure AI Search"],
    ["Prompt Engineering", "Prompting"],
    ["Fine-Tuning", "LLM Fine-Tuning", "Finetuning"],
    ["NER", "Named Entity Recognition", "Entity Extraction"],
    ["Time Series", "Forecasting"],
    ["Observability", "Monitoring", "OpenTelemetry"],
    ["Cybersecurity", "Cyber Security", "Information Security", "InfoSec", "Security"],
    ["PAM", "Privileged Access Management"],
    ["Leadership", "Led", "Managed teams", "Team Lead"],
    ["Production", "Productionized", "Shipped to production", "Deployed"],
    ["Python", "Python3"],
    ["Kubernetes", "K8s"],
]


class Headline(BaseModel):
    """A title variant: the title under your name and its summary (see services/headlines.py)."""
    id: str = Field(default_factory=new_id)
    title: str
    summary: str = ""
    keywords: list[str] = []   # optional hint words: a job mentioning them points to this variant
    default: bool = False      # used when no variant fits a job better


# The CV's sections below the header (name, title, contacts), in their default order. Templates render them in
# the master CV's `sections` order; a section with nothing in it is skipped.
CV_SECTIONS = {"summary": "Summary", "skills": "Skills", "experience": "Professional Experience",
               "projects": "Projects", "education": "Education", "languages": "Languages"}
DEFAULT_SECTIONS = list(CV_SECTIONS)


def normalize_sections(sections: list[str]) -> list[str]:
    """Known sections once each, in the given order; a missing one goes back right after the section that
    precedes it by default (so a section added later, like projects, lands under experience)."""
    out = list(dict.fromkeys(s for s in sections if s in CV_SECTIONS))
    for i, s in enumerate(DEFAULT_SECTIONS):
        if s not in out:
            before = next((b for b in reversed(DEFAULT_SECTIONS[:i]) if b in out), None)
            out.insert(out.index(before) + 1 if before else 0, s)
    return out


class MasterCV(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    headlines: list[Headline] = []   # title variants; profile.title/summary mirror the default one
    experiences: list[Experience] = []
    projects: list[Project] = []     # work outside a company: personal, academic, freelance, open source
    sections: list[str] = Field(default_factory=lambda: list(DEFAULT_SECTIONS))  # order on the CV page
    font: str = "STIX Two Text"      # the CVs' font (cv_templates/fonts.py: only installed ones can be chosen)
    education: list[Education] = []
    skills: list[SkillGroup] = []
    languages: list[str] = []   # e.g. "French (Native)"
    synonyms: list[list[str]] = Field(default_factory=lambda: [list(g) for g in DEFAULT_SYNONYMS])
    extra_keywords: list[str] = []  # keywords you track that no bullet uses yet

    @model_validator(mode="after")
    def _first_headline(self):
        """CVs from before title variants: their title and summary become the first (default) variant."""
        if not self.headlines and (self.profile.title or self.profile.summary):
            # a fixed id: the same variant on every read, even before anything saves it
            self.headlines = [Headline(id="main", title=self.profile.title or "Title", summary=self.profile.summary,
                                       default=True)]
        return self

    @field_validator("sections")
    @classmethod
    def _known_sections(cls, v: list[str]) -> list[str]:
        return normalize_sections(v)

    def bullet_owners(self) -> list[Experience | Project]:
        """Everything that holds achievements: experiences, then projects."""
        return [*self.experiences, *self.projects]


# --------------------------------------------------------------------- chats

ToolStatus = Literal["done", "error", "pending", "approved", "rejected"]


class ToolEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    action: str
    args: dict = {}
    status: ToolStatus = "done"
    result: object = None   # kept only when small (for links in the UI)
    error: str = ""
    turn_id: str = ""       # telemetry: the turn that requested it (links an approval to its request)


class Feedback(BaseModel):
    """The user's 👍/👎 on a reply; reasons are ids of telemetry.flags.USER_REASONS."""
    rating: Literal["up", "down"]
    reasons: list[str] = []
    comment: str = ""
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool", "notice"]   # notice: shown to the user, never sent to a model
    text: str = ""
    tool: ToolEvent | None = None
    cost_usd: float | None = None   # assistant replies: what the AI calls behind it cost
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    turn_id: str = ""               # the last reply of a turn: its telemetry turn (what 👍/👎 points at)
    feedback: Feedback | None = None


COMPACT_AT_TOKENS = 60_000    # what one AI call reads: tools and instructions (~20k) + the conversation
COMPACT_AT_MESSAGES = 120     # messages and tool events since the last compaction (when no token count is known)


class Chat(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str = "New chat"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    previous_response_id: str | None = None   # OpenAI keeps the conversation state server-side
    provider: Literal["openai", "claude"] = "openai"   # "claude" once OpenAI failed (quota, billing…): stays on Claude
    title_locked: bool = False                 # renamed by the user: no automatic titles
    archived: bool = False                     # hidden from the main list (still searchable, can be reopened)
    messages: list[ChatMessage] = []
    # Compacting (services/assistant.py): the model continues from `summary` and the messages from `history_from`
    # on; the older messages stay here for the user to read. context_tokens: the last AI call's input size.
    summary: str = ""
    history_from: int = 0
    context_tokens: int = 0
    turn_count: int = 0   # telemetry: turns so far (a user message or an approval each); the next turn's index

    @computed_field
    @property
    def compact_suggested(self) -> bool:
        """Long enough that each message re-sends a lot: the chat suggests compacting it."""
        return self.context_tokens >= COMPACT_AT_TOKENS or len(self.messages) - self.history_from >= COMPACT_AT_MESSAGES


class ChatBook(BaseModel):
    chats: list[Chat] = []


# ------------------------------------------------------------ interview prep
# Written by a strong model (Codex on the server, or any MCP client via prep.save), stored per user in
# preps.json, one per application. The PrepResult part is exactly what the model must return (strict schema:
# no defaults, every field required), so it can be validated before it is saved.

class RubricPoint(BaseModel):
    point: str           # what a good answer must contain
    points: int          # weight; an exercise's points add up to 10


class Exercise(BaseModel):
    title: str
    # technical: has a correct answer (reference solution, learn/check sessions). story: a "tell me about a time…"
    # question answered with one of the user's true stories (stories.json): no model answer, never scheduled.
    category: Literal["technical", "story"] = "technical"
    kind: str            # coding, SQL, system design, case study, behavioral…
    difficulty: Literal["easy", "medium", "hard"]
    capability: str = ""  # the broad skill it trains ("SQL", "Statistics"…), for progress over time ("" = older preps)
    topic: str           # the precise subject ("window functions")
    why: str             # why this company would ask it (from the research)
    prompt: str          # the exercise as the interviewer would give it
    solution: str        # reference solution
    rubric: list[RubricPoint]
    strong_answer: str   # what a strong answer looks like, in a few lines
    common_mistakes: list[str]
    estimated_min: int = 0  # minutes a focused session needs to learn the notion and do the exercise (0 = older preps)
    competency: str = ""    # story questions: what it tests ("Conflict", "Failure"…)
    stories: list[str] = []  # story questions: titles of the user's stories that answer it ([] = none fits yet)

    @model_validator(mode="before")
    @classmethod
    def _older_behavioral_is_story(cls, data):
        """Preps written before the split: a behavioral exercise is a story question."""
        if isinstance(data, dict) and "category" not in data and \
                str(data.get("kind", "")).lower().startswith(("behav", "story")):
            data = {**data, "category": "story"}
        return data


class PrepResult(BaseModel):
    company_summary: str        # what the company does, products, customers, tech, recent news (markdown)
    interview_process: str      # likely stages and format, and what each one tests (markdown)
    focus_topics: list[str]     # what to revise, most likely first
    candidate_angle: str        # how the user's experience maps to the role: strengths to push, gaps to prepare
    exercises: list[Exercise]
    questions_to_ask: list[str]
    sources: list[str]          # URLs the research used


PrepStatus = Literal["running", "done", "error"]


class InterviewPrep(BaseModel):
    id: str = Field(default_factory=new_id)
    company_id: str
    status: PrepStatus = "running"
    requested_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    finished_at: str | None = None
    by: str = ""                # who wrote it: "codex gpt-6.2-astra (high)", or the MCP client
    error: str = ""
    result: PrepResult | None = None
    file: str | None = None     # Interview_Prep.md in the company folder (relative to the output folder)


class PrepBook(BaseModel):
    preps: list[InterviewPrep] = []   # every version, oldest first; the latest done one is shown


# What the user practised: one attempt = one answer to one exercise, graded with its rubric (by the assistant).
class PracticeAttempt(BaseModel):
    id: str = Field(default_factory=new_id)
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    company_id: str = ""
    company: str = ""
    exercise: str               # exercise title
    kind: str = ""
    capability: str = ""        # broad skill, see services/practice.py CAPABILITIES
    topic: str = ""
    difficulty: str = ""
    score: int                  # out of 10
    missed: list[str] = []      # rubric points the answer missed
    feedback: str = ""
    source: Literal["practice", "interview"] = "practice"  # interview: graded from a real interview's recording
    recording_id: str = ""
    story_id: str = ""          # the story the user told (story questions)


class PracticeBook(BaseModel):
    attempts: list[PracticeAttempt] = []
    targets: dict[str, int] = {}   # capability -> target score /10 the user aims for


# ------------------------------------------------------------------- stories
# The story bank: the user's true stories for "tell me about a time…" questions, written once in the STAR shape
# and reused in every prep, practice and mock interview (services/stories.py). Only the user's own facts.

class Story(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str                   # short name the user recognises: "Elasticsearch migration at Cybersixgill"
    situation: str = ""          # the context, in one or two sentences
    task: str = ""               # what was at stake, the user's responsibility
    action: str = ""             # what the user did themselves ("I…")
    result: str = ""             # the outcome, with numbers when possible
    lesson: str = ""             # what they learned, or would do differently
    competencies: list[str] = []  # the questions it answers: "Conflict", "Failure", "Leadership"…
    achievements: list[str] = []  # master CV achievement (bullet) ids it is the story of
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    updated_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class StoryBook(BaseModel):
    stories: list[Story] = []


# ---------------------------------------------------------------------- mail
# The user's mailbox, read (Gmail or Outlook, read-only) for news about their applications (services/mail.py): each email
# about a tracked application becomes a proposed update the user applies or dismisses.

MailKind = Literal["application_received", "referred", "rejection", "interview_invite", "next_step", "offer",
                   "recruiter_message", "other", "unrelated"]


class MailAccount(BaseModel):
    provider: Literal["gmail", "outlook"] = "gmail"   # which mailbox (Outlook: personal Microsoft accounts)
    email: str = ""
    refresh_token: str = ""      # the provider's long-lived token (read-only mail access); "" = not connected
    connected_at: str | None = None
    last_check: str | None = None
    last_error: str = ""
    checked_until: int = 0       # epoch seconds: mail received before this was already looked at


class MailEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    message_id: str              # the mailbox's id (Gmail's, or Outlook's immutable id)
    received_at: str = ""        # "YYYY-MM-DDTHH:MM:SS" local time
    sender: str = ""
    subject: str = ""
    company_id: str = ""
    company: str = ""
    kind: MailKind = "other"
    summary: str = ""            # one line: what the email says
    step_name: str = ""          # interview invites: the step it is ("Technical interview")
    person: str = ""             # referrals: who referred the user
    date: str = ""               # "YYYY-MM-DD" of the interview, when the email gives it
    time: str = ""               # "HH:MM", the user's local time
    duration_min: int = 0
    location: str = ""           # address or video link
    found_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: Literal["proposed", "applied", "dismissed"] = "proposed"
    done_at: str | None = None
    result: str = ""             # what applying it changed


class MailBook(BaseModel):
    account: MailAccount = MailAccount()
    events: list[MailEvent] = []
    seen: list[str] = []         # email ids already looked at (the newest few thousand)
    # what each looked-at email became: skipped (names no application), unrelated / other (the model's answer),
    # proposed, or fixed (a miss the user repaired); to tell why an email was missed
    outcomes: dict[str, str] = {}


# ------------------------------------------------------------------ learning
# Courses: a topic to learn for an application, as a teach session then spaced checks (services/learning.py).

class StudySession(BaseModel):
    id: str = Field(default_factory=new_id)
    kind: Literal["teach", "check"]
    number: int = 0              # check 1, 2 or 3 (0 for the teach)
    start: str | None = None     # "YYYY-MM-DDTHH:MM" local time (None = not placed: no free slot)
    duration_min: int = 15
    state: Literal["planned", "done", "skipped"] = "planned"
    score: int | None = None     # /10, from the graded answer
    missed: list[str] = []       # rubric points that got nothing: the next check must cover them
    done_at: str | None = None
    attempt_id: str = ""         # the practice attempt that completed it
    moved: int = 0               # times it was moved (missed, or by the user)


CourseState = Literal["proposed", "suggested", "scheduled", "done", "dropped"]


class Course(BaseModel):
    id: str = Field(default_factory=new_id)
    company_id: str = ""
    company: str = ""
    topic: str
    capability: str = ""
    exercise: str = ""           # prep exercise title the teach session uses ("" = none)
    estimated_min: int = 45      # the teach session
    due: str | None = None       # the interview it prepares for, "YYYY-MM-DDTHH:MM" (or a date)
    due_step: str = ""
    # proposed: slots offered, not accepted yet · suggested: a new notion seen in practice, checks offered ·
    # scheduled: accepted · done: cycle finished · dropped
    state: CourseState = "proposed"
    reason: str = ""             # "prep", "new notion", "first high score"
    outcome: Literal["", "learned", "needs_work"] = ""
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    sessions: list[StudySession] = []


class LearningBook(BaseModel):
    courses: list[Course] = []


# ------------------------------------------------------------------- usage
# One AI call (or Codex run, or audio transcription) and what it cost, from the usage the provider reported.

class UsageEntry(BaseModel):
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    provider: str                # openai | anthropic | codex
    model: str
    feature: str = ""            # the user-facing action (chat.send, recording.analyze, prep.start…)
    input_tokens: int = 0        # not counting cached / cache-write tokens
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    output_tokens: int = 0       # includes reasoning tokens
    seconds: float = 0           # audio length (transcription) or run time (Codex)
    cost_usd: float | None = None  # None: no price known for this model (see prices.py)
    covered_by_plan: bool = False  # Codex on the ChatGPT plan: no per-use cost


class UsageBook(BaseModel):
    entries: list[UsageEntry] = []


# ---------------------------------------------------------------- recordings
# A recorded interview: transcribed (with speaker labels when available), then a debrief graded by the cheap model.

class TranscriptSegment(BaseModel):
    speaker: str = ""        # "A", "B"… from the transcription; "" when it gives no speaker labels
    start: float = 0
    end: float = 0
    text: str


class DebriefQuestion(BaseModel):
    question: str            # what the interviewer asked (rephrased if needed)
    answer_summary: str      # what the candidate answered, in short
    kind: str                # technical, behavioral, HR, case…
    capability: str          # broad skill (SQL, Behavioral…)
    topic: str
    score: int               # /10 for the candidate's answer
    missed: list[str]        # what a strong answer would have added
    feedback: str


class Debrief(BaseModel):
    summary: str             # how the interview went (markdown)
    about_the_role: str      # what they said about the company, team, role, process (markdown)
    next_steps: str
    questions: list[DebriefQuestion]
    strengths: list[str]
    to_improve: list[str]
    thank_you_email: str     # a short thank-you email to send


RecordingStatus = Literal["uploaded", "transcribing", "choose_speaker", "analyzing", "done", "error"]


class Recording(BaseModel):
    id: str = Field(default_factory=new_id)
    company_id: str
    step: str = ""           # process step id it belongs to
    step_name: str = ""
    file: str                # the audio, relative to the output folder
    original_name: str = ""
    uploaded_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    duration_s: float = 0
    status: RecordingStatus = "uploaded"
    error: str = ""
    model: str = ""          # transcription model used
    diarized: bool = False   # segments carry speaker labels
    segments: list[TranscriptSegment] = []
    me: str = ""             # which speaker label is the user
    language: str = ""       # language of the debrief ("" = the interview's)
    debrief: Debrief | None = None
    debrief_file: str | None = None
    estimate_usd: dict[str, float | None] = {}  # before: {"transcription": …, "analysis": …}
    cost_usd: dict[str, float] = {}             # after, from the usage the providers reported


class RecordingBook(BaseModel):
    recordings: list[Recording] = []
