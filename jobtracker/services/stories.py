"""The story bank: the user's true stories for "tell me about a time…" questions (behavioral questions).

Technical questions have a correct answer, so a prep can write the solution and the study plan can teach it.
Story questions don't: the answer is the user's own past. So a story is written once, by the user (the assistant
only asks questions and words what they said, never adds a fact), in the STAR shape: Situation, Task, Action,
Result, plus the lesson. Each story is tagged with the competencies it can answer (one story often answers
several questions) and linked to the master CV achievements it tells. Preps point each story question to the
stories that answer it, practice grades the telling, and the gaps (common questions with no story yet) show
what to write next.
"""
from __future__ import annotations

from datetime import datetime

from ..models import Story, StoryBook
from ..storage import UserDocument
from . import master_cv

store = UserDocument("stories.json", StoryBook)

# The questions interviewers ask most, as competencies (name, the question as they'd ask it). A bank that covers
# these answers almost any behavioral round; a story usually covers two or three of them.
COMPETENCIES: list[tuple[str, str]] = [
    ("Proud achievement", "Tell me about a project you're most proud of."),
    ("Conflict", "Tell me about a disagreement with a colleague or your manager."),
    ("Failure", "Tell me about a time you failed or made a mistake."),
    ("Influence", "Tell me about a time you convinced people without authority over them."),
    ("Leadership", "Tell me about a time you led a project or people."),
    ("Ambiguity", "Tell me about a time you had to move forward with unclear requirements."),
    ("Pressure", "Tell me about a tight deadline or a crisis."),
    ("Prioritization", "Tell me about a time you had too much to do: what did you drop?"),
    ("Ownership", "Tell me about a time you went beyond your role to fix something."),
    ("Customer focus", "Tell me about a time you put the user or customer first."),
    ("Learning fast", "Tell me about a time you had to learn something new quickly."),
    ("Feedback", "Tell me about difficult feedback you received, and what you did with it."),
    ("Data-driven decision", "Tell me about a decision you made from data, or against intuition."),
    ("Teamwork", "Tell me about a time you helped a teammate or the team succeed."),
]
PARTS = ("situation", "task", "action", "result", "lesson")
FIELDS = ("title", *PARTS, "competencies", "achievements")


def _competency(name: str) -> str:
    """The catalog's spelling of a competency when it is one (any case), else the name as given."""
    name = " ".join(name.split())
    return next((c for c, _ in COMPETENCIES if c.lower() == name.lower()), name)


def _clean_competencies(names: list[str]) -> list[str]:
    out: list[str] = []
    for n in names or []:
        c = _competency(n)
        if c and c.lower() not in {x.lower() for x in out}:
            out.append(c)
    return out


def _achievements() -> dict[str, dict]:
    """Every master CV achievement by id: its text and the experience it belongs to."""
    out = {}
    for e in master_cv.get_master().experiences:
        for b in e.bullets:
            out[b.id] = {"id": b.id, "text": f"{b.lead} {b.text}".strip(), "experience": e.company, "role": e.role}
    return out


def _clean_achievements(ids: list[str]) -> list[str]:
    known = _achievements()
    unknown = [i for i in ids or [] if i not in known]
    if unknown:
        raise ValueError(f"No master CV achievement with id {', '.join(unknown)}: use the ids from cv.master.get.")
    return list(dict.fromkeys(ids or []))


def all_stories() -> list[Story]:
    return store.load().stories


def find(ref: str) -> Story | None:
    """A story by id, exact title, or a unique part of its title (any case)."""
    stories, ref = all_stories(), (ref or "").strip()
    if not ref:
        return None
    hit = next((s for s in stories if s.id == ref or s.title.lower() == ref.lower()), None)
    if hit:
        return hit
    partial = [s for s in stories if ref.lower() in s.title.lower()]
    return partial[0] if len(partial) == 1 else None


def resolve(ref: str) -> Story:
    story = find(ref)
    if story is None:
        raise ValueError(f"No story “{ref}”: story.list gives each story's id and title.")
    return story


def add(title: str, situation: str = "", task: str = "", action: str = "", result: str = "", lesson: str = "",
        competencies: list[str] | None = None, achievements: list[str] | None = None) -> Story:
    title = " ".join((title or "").split())
    if not title:
        raise ValueError("A story needs a title (a short name you'll recognise).")
    if any(s.title.lower() == title.lower() for s in all_stories()):
        raise ValueError(f"A story is already called “{title}”: update it, or choose another title.")
    story = Story(title=title, situation=situation.strip(), task=task.strip(), action=action.strip(),
                  result=result.strip(), lesson=lesson.strip(), competencies=_clean_competencies(competencies or []),
                  achievements=_clean_achievements(achievements or []))
    with store.transaction() as book:
        book.stories.append(story)
    return story


def update(ref: str, **changes) -> Story:
    """Change the fields given (None = leave as is)."""
    target = resolve(ref)
    changes = {k: v for k, v in changes.items() if k in FIELDS and v is not None}
    if "title" in changes:
        changes["title"] = " ".join(changes["title"].split())
        if not changes["title"]:
            raise ValueError("A story needs a title.")
        clash = next((s for s in all_stories() if s.id != target.id and s.title.lower() == changes["title"].lower()), None)
        if clash:
            raise ValueError(f"A story is already called “{clash.title}”.")
    for part in PARTS:
        if part in changes:
            changes[part] = changes[part].strip()
    if "competencies" in changes:
        changes["competencies"] = _clean_competencies(changes["competencies"])
    if "achievements" in changes:
        changes["achievements"] = _clean_achievements(changes["achievements"])
    with store.transaction() as book:
        for i, s in enumerate(book.stories):
            if s.id == target.id:
                now = datetime.now().isoformat(timespec="seconds")
                book.stories[i] = s.model_copy(update={**changes, "updated_at": now})
                return book.stories[i]
    raise ValueError("The story was deleted meanwhile.")


def delete(ref: str) -> dict:
    target = resolve(ref)
    with store.transaction() as book:
        book.stories = [s for s in book.stories if s.id != target.id]
    return {"deleted": target.title}


# ------------------------------------------------------------------- views

def missing_parts(s: Story) -> list[str]:
    """STAR parts still empty (the lesson is optional)."""
    return [p for p in PARTS[:4] if not getattr(s, p).strip()]


def _attempts_by_story() -> dict[str, list]:
    from . import practice  # (practice imports this module)
    out: dict[str, list] = {}
    for a in sorted(practice.store.load().attempts, key=lambda a: a.at):
        if a.story_id:
            out.setdefault(a.story_id, []).append(a)
    return out


def view(s: Story, achievements: dict | None = None, attempts: list | None = None) -> dict:
    achievements = _achievements() if achievements is None else achievements
    attempts = attempts or []
    return {**s.model_dump(mode="json"),
            "missing": missing_parts(s),
            "achievement_texts": [achievements[i] for i in s.achievements if i in achievements],
            "practice": {"count": len(attempts), "last_score": attempts[-1].score if attempts else None,
                         "last_missed": attempts[-1].missed if attempts else [],
                         "last_at": attempts[-1].at if attempts else None}}


def coverage(stories: list[Story] | None = None) -> list[dict]:
    """Each common competency, the question it stands for, and the stories that answer it (none = a gap)."""
    stories = all_stories() if stories is None else stories
    rows = [{"competency": c, "question": q, "stories": [s.title for s in stories
                                                          if c.lower() in {x.lower() for x in s.competencies}]}
            for c, q in COMPETENCIES]
    known = {c.lower() for c, _ in COMPETENCIES}
    extra = sorted({x for s in stories for x in s.competencies if x.lower() not in known})
    rows += [{"competency": c, "question": "", "stories": [s.title for s in stories if c in s.competencies]}
             for c in extra]
    return rows


def overview() -> dict:
    """The whole bank: stories (newest change first) with what is missing and their practice, and the coverage."""
    stories = sorted(all_stories(), key=lambda s: s.updated_at, reverse=True)
    achievements, attempts = _achievements(), _attempts_by_story()
    cover = coverage(stories)
    return {"stories": [view(s, achievements, attempts.get(s.id)) for s in stories],
            "coverage": cover,
            "gaps": [r["competency"] for r in cover if r["question"] and not r["stories"]],
            "competencies": [c for c, _ in COMPETENCIES]}


def summary_for_prep() -> str:
    """The bank as text for a prep's brief, so story questions can point to the stories that answer them."""
    stories = all_stories()
    if not stories:
        return ""
    lines = []
    for s in stories:
        lines.append(f"- “{s.title}” (answers: {', '.join(s.competencies) or 'not tagged'})")
        for part in PARTS:
            text = getattr(s, part).strip()
            if text:
                lines.append(f"  {part.capitalize()}: {text[:600]}")
    return "\n".join(lines)


def is_story_attempt(a) -> bool:
    """A practice answer to a story question: never a notion to learn with spaced checks."""
    return bool(a.story_id) or (a.kind or "").lower().startswith(("behav", "story"))
