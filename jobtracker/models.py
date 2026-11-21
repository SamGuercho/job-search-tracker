"""Data models shared by storage, services, API and CLI."""
from __future__ import annotations

from datetime import date, datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator

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
    last_update: str = Field(default_factory=today)
    last_update_by: UpdateSide = "me"
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


class MasterCV(BaseModel):
    profile: Profile = Field(default_factory=Profile)
    experiences: list[Experience] = []
    education: list[Education] = []
    skills: list[SkillGroup] = []
    languages: list[str] = []   # e.g. "French (Native)"
    synonyms: list[list[str]] = Field(default_factory=lambda: [list(g) for g in DEFAULT_SYNONYMS])
    extra_keywords: list[str] = []  # keywords you track that no bullet uses yet


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
    at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class Chat(BaseModel):
    id: str = Field(default_factory=new_id)
    title: str = "New chat"
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    previous_response_id: str | None = None   # OpenAI keeps the conversation state server-side
    provider: Literal["openai", "claude"] = "openai"   # "claude" once OpenAI failed (quota, billing…): stays on Claude
    title_locked: bool = False                 # renamed by the user: no automatic titles
    messages: list[ChatMessage] = []


class ChatBook(BaseModel):
    chats: list[Chat] = []
