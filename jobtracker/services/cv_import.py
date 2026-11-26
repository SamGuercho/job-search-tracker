"""Import an existing CV (.docx / .pdf / text) into the master CV.

  1. extract_text(path)      plain text from the file
  2. parse_cv(text)          LLM -> draft MasterCV (nothing saved yet)
  3. apply_import(draft, mode) merge into / replace the master CV
"""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from docx import Document
from pydantic import BaseModel
from pypdf import PdfReader

from ..models import Bullet, Education, Experience, MasterCV, Profile, Project, SkillGroup
from . import llm, master_cv

SUPPORTED = {".docx", ".pdf", ".txt", ".md"}
ImportMode = Literal["merge", "replace"]


# ----------------------------------------------------------- 1. text extraction

def extract_text(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".docx":
        doc = Document(str(path))
        lines = [_docx_line(p) for p in doc.paragraphs]
        for table in doc.tables:  # many CV templates lay out content in tables
            for row in table.rows:
                lines.append(" | ".join(dict.fromkeys(c.text.strip() for c in row.cells if c.text.strip())))
        text = "\n".join(lines)
    elif suffix == ".pdf":
        text = "\n".join(page.extract_text() or "" for page in PdfReader(str(path)).pages)
    elif suffix in {".txt", ".md"}:
        text = path.read_text(encoding="utf-8", errors="replace")
    else:
        raise ValueError(f"Unsupported file type {suffix!r}: use {', '.join(sorted(SUPPORTED))}")
    text = "\n".join(line.rstrip() for line in text.splitlines() if line.strip())
    if not text:
        raise ValueError("No text found in the file (a scanned PDF needs OCR first).")
    return text


def _docx_line(paragraph) -> str:
    """Paragraph text keeping what plain text loses: list bullets ("• "), a bold
    label at the start ("**Copilot:** ...") and link targets ("LinkedIn <url>")."""
    runs = [r for r in paragraph.runs if r.text]
    text = paragraph.text
    lead = ""
    for r in runs:
        if not r.bold:
            break
        lead += r.text
    if lead.strip() and len(lead) < len(text) and text.startswith(lead):
        text = f"**{lead.strip()}** {text[len(lead):].lstrip()}"
    for link in getattr(paragraph, "hyperlinks", []):
        if link.url and link.url not in text:
            text += f" <{link.url}>"
    is_list = "List" in paragraph.style.name or paragraph._p.pPr is not None and paragraph._p.pPr.numPr is not None
    return f"• {text}" if is_list and text.strip() else text


# ------------------------------------------------- 2. LLM extraction schema
# Kept separate from the storage models: every field required and no ids, as
# structured outputs need, and so the model can't invent internal fields.

class _Bullet(BaseModel):
    group: str
    lead: str
    text: str
    keywords: list[str]


class _Experience(BaseModel):
    company: str
    role: str
    location: str
    start: str
    end: str
    bullets: list[_Bullet]


class _Project(BaseModel):
    name: str
    role: str
    link: str
    start: str
    end: str
    bullets: list[_Bullet]


class _Education(BaseModel):
    school: str
    degree: str
    location: str
    start: str
    end: str
    details: str


class _Skills(BaseModel):
    name: str
    items: list[str]


class _Profile(BaseModel):
    name: str
    title: str
    email: str
    phone: str
    location: str
    linkedin: str
    github: str
    website: str
    summary: str


class _ParsedCV(BaseModel):
    profile: _Profile
    experiences: list[_Experience]
    projects: list[_Project]
    education: list[_Education]
    skills: list[_Skills]
    languages: list[str]


INSTRUCTIONS = """You convert a CV/resume into structured data.
Rules:
- Copy the candidate's wording for bullet points verbatim (fix only broken line wraps). Never invent facts, numbers or dates.
- Use "" for any field that is not in the CV. Keep dates as written (e.g. "2021", "Mar 2021", "Present").
- Experiences in the order they appear (usually most recent first). Each achievement/responsibility line is one bullet.
- Lines starting with "• " are bullets (drop the "• "). Text wrapped in **double asterisks** at the start of a bullet
  is a bold label: put it in `lead` without the asterisks (e.g. "Copilot:") and the rest in `text`. Never drop a label.
  If there is no label, lead is "".
- A URL in <angle brackets> is the target of the link text before it (e.g. "LinkedIn <https://...>" -> profile.linkedin is the URL).
  A GitHub profile URL goes in profile.github; a personal website or portfolio in profile.website.
- If bullets sit under a project sub-heading inside a job, put that heading in each bullet's `group`; else group "".
- A separate "Projects" section (personal, academic, freelance, open-source, side projects: work NOT done for an
  employer) goes in `projects`, never in `experiences`: `name` = the project's name, `role` = the role or context
  if written (e.g. "Master's thesis", "Maintainer"), `link` = its URL if any, dates as written, and its lines as
  bullets (same rules as experience bullets). Projects listed inside a job stay in that experience (as `group`).
- For each bullet, list as keywords the important terms that are LITERALLY WRITTEN in that bullet (label or text):
  tools, technologies, methods, domains, skills (e.g. "SQL", "LangGraph", "LLM-as-a-Judge", "GDPR").
  Copy them as they appear (a plural may be singularised). Never add a concept that is not written in the bullet:
  keywords are used to pick bullets whose text contains the words a job description asks for.
  Prefer the spelling of the existing vocabulary below when the same term appears.
- Group skills as in the CV (e.g. "Programming", "Tools"); if ungrouped, use one group named "Skills".
- Education `details` (thesis, GPA, honours, relevant courses): one item per line, separated by a newline.
- Spoken languages go in `languages` (e.g. "French (Native)"), not in skills.
- Put a profile/summary paragraph in profile.summary if there is one.

Existing keyword vocabulary: {vocabulary}"""


def _bullet(b: _Bullet) -> Bullet:
    """Keep only keywords really written in the bullet, plus known keywords the model missed."""
    bullet = Bullet(**b.model_dump())
    words = [k for k in b.keywords if master_cv.in_text(k, bullet)]
    words += master_cv.detect_keywords(bullet.text, bullet.lead)
    bullet.keywords = master_cv._clean_keywords(words)
    return bullet


def parse_cv(text: str) -> MasterCV:
    """Ask the LLM to structure a CV's text. Returns a draft; nothing is saved."""
    vocabulary = ", ".join(master_cv.keyword_vocabulary()) or "(none yet)"
    parsed = llm.structured(INSTRUCTIONS.format(vocabulary=vocabulary), text, _ParsedCV)
    return MasterCV(
        profile=Profile(**parsed.profile.model_dump()),
        experiences=[Experience(**e.model_dump(exclude={"bullets"}), bullets=[_bullet(b) for b in e.bullets])
                     for e in parsed.experiences],
        projects=[Project(**p.model_dump(exclude={"bullets"}), bullets=[_bullet(b) for b in p.bullets])
                  for p in parsed.projects],
        education=[Education(**e.model_dump()) for e in parsed.education],
        skills=[SkillGroup(**s.model_dump()) for s in parsed.skills],
        languages=parsed.languages,
    )


# ------------------------------------------------------------------- 3. apply

def apply_import(draft: MasterCV, mode: ImportMode = "merge") -> MasterCV:
    """`replace` overwrites the master CV (extra keywords, synonyms and section order are kept).
    `merge` fills empty profile fields, adds experiences/projects/education not already
    present (same company+role / project name / school+degree; new bullets are appended to
    existing ones) and merges skill groups by name."""
    with master_cv.store.transaction() as cv:
        if mode == "replace":
            cv.profile, cv.experiences, cv.projects = draft.profile, draft.experiences, draft.projects
            cv.education, cv.skills, cv.languages = draft.education, draft.skills, draft.languages
            return cv

        for field, value in draft.profile.model_dump().items():
            if value and not getattr(cv.profile, field):
                setattr(cv.profile, field, value)

        key = lambda *parts: tuple(p.strip().lower() for p in parts)
        def merge(have: list, new: list, ident) -> None:
            by_key = {ident(o): o for o in have}
            for o in new:
                existing = by_key.get(ident(o))
                if existing is None:
                    have.append(o)
                    continue
                known = {b.text.strip().lower() for b in existing.bullets}
                existing.bullets += [b for b in o.bullets if b.text.strip().lower() not in known]

        merge(cv.experiences, draft.experiences, lambda e: key(e.company, e.role))
        merge(cv.projects, draft.projects, lambda p: key(p.name))

        schools = {key(e.school, e.degree) for e in cv.education}
        cv.education += [e for e in draft.education if key(e.school, e.degree) not in schools]

        have_langs = {lang.lower() for lang in cv.languages}
        cv.languages += [lang for lang in draft.languages if lang.lower() not in have_langs]

        groups = {g.name.strip().lower(): g for g in cv.skills}
        for g in draft.skills:
            existing = groups.get(g.name.strip().lower())
            if existing is None:
                cv.skills.append(g)
            else:
                have = {i.lower() for i in existing.items}
                existing.items += [i for i in g.items if i.lower() not in have]
    return cv
