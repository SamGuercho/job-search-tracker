"""Data models shared by storage, services, API and CLI."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, computed_field, model_validator

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


class GeneratedDocument(BaseModel):
    kind: str = "cv"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    keywords: list[str] = []
    docx: str | None = None
    pdf: str | None = None


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
    cv_summary: str = ""          # tailored CV summary for this application ("" = master CV summary)
    cv_title: str = ""            # the title under your name on this application's CV ("" = its title variant's)
    cv_headline: str = ""         # the title variant pinned for this application ("" = the closest, automatically)
    last_update: str = Field(default_factory=today)
    last_update_by: UpdateSide = "me"
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


class Education(BaseModel):
    id: str = Field(default_factory=new_id)
    school: str
    degree: str = ""
    location: str = ""
    start: str = ""
    end: str = ""
    details: str = ""


class SkillGroup(BaseModel):
    id: str = Field(default_factory=new_id)
    name: str
    items: list[str] = []


class Profile(BaseModel):
    name: str = ""
    title: str = ""
    email: str = ""
    phone: str = ""
    location: str = ""
    linkedin: str = ""
    website: str = ""
    summary: str = ""


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


class MasterCV(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    headlines: list[Headline] = []   # title variants; profile.title/summary mirror the default one
    experiences: list[Experience] = []
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


# --------------------------------------------------------------------- chats

ToolStatus = Literal["done", "error", "pending", "approved", "rejected"]


class ToolEvent(BaseModel):
    id: str = Field(default_factory=new_id)
    action: str
    args: dict = {}
    status: ToolStatus = "done"
    result: object = None   # kept only when small (for links in the UI)
    error: str = ""


class ChatMessage(BaseModel):
    role: Literal["user", "assistant", "tool", "notice"]   # notice: shown to the user, never sent to a model
    text: str = ""
    tool: ToolEvent | None = None
    cost_usd: float | None = None   # assistant replies: what the AI calls behind it cost
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


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


class PracticeBook(BaseModel):
    attempts: list[PracticeAttempt] = []
    targets: dict[str, int] = {}   # capability -> target score /10 the user aims for


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
