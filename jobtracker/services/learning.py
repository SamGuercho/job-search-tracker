"""Courses: learning a topic for an interview as a teach session, then spaced checks, planned in your free time.

A course is one topic of an application's prep (its exercise) with an estimated time. Accepting a course means
accepting to schedule it: propose() places its sessions in the free slots between now and the interview, the
user accepts them (accept()), and they appear on Today and in the calendar feed.

The teach/check cycle:
  teach    learn the notion, then do the exercise (graded)
  check 1  1-2 days after the teach
  check 2  3-4 days after check 1
  check 3  only when check 2 scores below 7, or drops 2 points or more below check 1
A check asks new questions (never the exercise again) that must cover the rubric points the previous session
missed. Checks may fall after the interview when there is no room before (before is recommended).

New notions: a practice answer on a topic never practised that scores below 7, or the first score of 7+ after
lower ones, makes a *suggested* course: the practice counts as the teach, and checks are offered.

Free slots: your study hours (Profile), minus working hours, interviews (with a margin) and sessions already
planned, at most `max_per_day_min` of study a day. Weekend days are the profile's (Friday and Saturday by
default). A session that was missed moves itself to the next free slot (reflow()).
"""
from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime, time, timedelta

from ..models import Course, LearningBook, PracticeAttempt, StudyHours, StudySession
from ..storage import UserDocument
from . import calendar, companies, practice, prep, profile

store = UserDocument("learning.json", LearningBook)

GOOD = practice.GOOD          # 7/10
CHECK_MIN = 15                # a check session
STEP_MIN = 15                 # sessions start on the quarter hour
INTERVIEW_MARGIN = timedelta(minutes=30)   # kept free around an interview
LAST_CHECK_BEFORE = timedelta(hours=2)     # nothing in the 2 hours before an interview
GAPS = {1: (1, 2), 2: (3, 4), 3: (3, 4)}   # check n: days after the previous session (earliest, latest preferred)
DROP = 2                      # check 2 this much below check 1 -> a 3rd check
SEARCH_DAYS = 30              # how far ahead slots are searched
GRACE = timedelta(minutes=30)  # a session not done this long after its end is missed
DIFFICULTY_MIN = {"easy": 30, "medium": 45, "hard": 60}


# ------------------------------------------------------------------ helpers

def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    return datetime.fromisoformat(value if "T" in value else f"{value}T08:00")


def _fmt(d: datetime) -> str:
    return d.strftime("%Y-%m-%dT%H:%M")


def _hm(day: date, hhmm: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hhmm))


def _round_up(d: datetime) -> datetime:
    d = d.replace(second=0, microsecond=0)
    extra = (-d.minute) % STEP_MIN
    return d + timedelta(minutes=extra)


def _end(s: StudySession) -> datetime | None:
    start = _dt(s.start)
    return start + timedelta(minutes=s.duration_min) if start else None


def topic_key(topic: str, exercise: str = "") -> str:
    return " ".join((topic or exercise).lower().split())


def estimate(exercise) -> int:
    """Minutes of the teach session: the prep's estimate, else from the difficulty."""
    minutes = getattr(exercise, "estimated_min", 0) or DIFFICULTY_MIN.get(getattr(exercise, "difficulty", ""), 45)
    if (getattr(exercise, "kind", "") or "").lower().startswith("behav"):
        minutes = min(minutes, 30)
    return max(STEP_MIN, min(int(minutes), 120))


def hours_set(h: StudyHours) -> bool:
    return bool(h.study_start and h.study_end) or h.weekend_free


def next_interview(company_id: str) -> tuple[str | None, str]:
    """("YYYY-MM-DDTHH:MM" or date, step name) of the application's next scheduled interview."""
    for c, n in calendar.upcoming(days_back=0):
        if c.id == company_id:
            return (f"{n.date[:10]}T{n.time}" if n.time else n.date[:10]), n.name
    return None, ""


# ------------------------------------------------------------------ planner

def windows(day: date, h: StudyHours) -> list[tuple[datetime, datetime]]:
    """When you can study that day: the study hours (weekend ones on weekend days), minus working hours."""
    if day.weekday() in h.weekend_days:
        if not h.weekend_free:
            return []
        start, end, work = h.weekend_start, h.weekend_end, None
    else:
        start, end = h.study_start, h.study_end
        work = (h.work_start, h.work_end) if h.work_start and h.work_end else None
    if not (start and end) or end <= start:
        return []
    out = [(_hm(day, start), _hm(day, end))]
    if work and work[1] > work[0]:
        out = _subtract(out, [(_hm(day, work[0]), _hm(day, work[1]))])
    return out


def _subtract(spans, busy):
    out = []
    for s, e in spans:
        pieces = [(s, e)]
        for bs, be in busy:
            nxt = []
            for ps, pe in pieces:
                if be <= ps or bs >= pe:
                    nxt.append((ps, pe))
                    continue
                if bs > ps:
                    nxt.append((ps, bs))
                if be < pe:
                    nxt.append((be, pe))
            pieces = nxt
        out += pieces
    return out


class Planner:
    """Free slots for new sessions, given what is already planned (sessions of accepted courses, interviews)."""

    def __init__(self, hours: StudyHours, now: datetime | None = None, ignore: set[str] = frozenset()):
        self.h = hours
        self.now = _round_up(now or datetime.now())
        self.busy: list[tuple[datetime, datetime]] = []
        self.used: dict[date, int] = defaultdict(int)
        for c in store.load().courses:
            if c.state not in ("scheduled", "suggested"):
                continue
            for s in c.sessions:
                if s.state == "planned" and s.start and s.id not in ignore:
                    self.book(_dt(s.start), s.duration_min)
        for _c, n in calendar.upcoming(days_back=0):
            if n.time:
                start = _hm(date.fromisoformat(n.date[:10]), n.time)
                end = start + timedelta(minutes=n.duration_min or 60)
                self.busy.append((start - INTERVIEW_MARGIN, end + INTERVIEW_MARGIN))

    def book(self, start: datetime, minutes: int) -> None:
        self.busy.append((start, start + timedelta(minutes=minutes)))
        self.used[start.date()] += minutes

    def find(self, minutes: int, earliest: datetime, latest: datetime | None = None) -> datetime | None:
        """The first free slot of `minutes` starting at or after `earliest` and ending by `latest`."""
        earliest = max(_round_up(earliest), self.now)
        last_day = (latest or earliest + timedelta(days=SEARCH_DAYS)).date()
        day = earliest.date()
        while day <= last_day:
            if self.used[day] + minutes <= self.h.max_per_day_min:
                for s, e in _subtract(windows(day, self.h), self.busy):
                    start = _round_up(max(s, earliest))
                    stop = start + timedelta(minutes=minutes)
                    if stop <= e and (latest is None or stop <= latest):
                        return start
            day += timedelta(days=1)
        return None

    def take(self, minutes: int, earliest: datetime, latest: datetime | None = None) -> datetime | None:
        start = self.find(minutes, earliest, latest)
        if start:
            self.book(start, minutes)
        return start


def _deadlines(due: str | None) -> tuple[datetime | None, datetime | None]:
    """(latest end of a teach session, latest end of a check) before the interview."""
    d = _dt(due)
    if not d:
        return None, None
    teach = datetime.combine(d.date() - timedelta(days=1), time(23, 59))  # the day before at the latest
    return teach, d - LAST_CHECK_BEFORE


def _place_check(planner: Planner, s: StudySession, after: datetime, check_deadline: datetime | None) -> None:
    """Check n: 1-2 (or 3-4) days after the previous session, else the first slot after that before the
    interview, else the first slot after the interview."""
    lo, hi = GAPS.get(s.number, (3, 4))
    first_day = datetime.combine(after.date() + timedelta(days=lo), time(0))
    preferred_end = datetime.combine(after.date() + timedelta(days=hi), time(23, 59))
    if check_deadline:
        preferred_end = min(preferred_end, check_deadline)
    start = (planner.take(s.duration_min, first_day, preferred_end)
             or (check_deadline and planner.take(s.duration_min, first_day, check_deadline))
             or planner.take(s.duration_min, first_day))
    s.start = _fmt(start) if start else None


def plan_course(course: Course, planner: Planner, from_index: int = 0) -> None:
    """Place the course's planned sessions from `from_index` on, in order, each after the previous one."""
    teach_deadline, check_deadline = _deadlines(course.due)
    prev_end = None
    for s in course.sessions[:from_index]:
        if s.state == "done" and s.done_at:
            prev_end = _dt(s.done_at)
        elif s.state == "planned" and s.start:
            prev_end = _end(s)
    for s in course.sessions[from_index:]:
        if s.state != "planned":
            prev_end = _dt(s.done_at) if s.done_at else prev_end
            continue
        if s.kind == "teach":
            start = (planner.take(s.duration_min, planner.now, teach_deadline)
                     or (check_deadline and planner.take(s.duration_min, planner.now, check_deadline)))
            s.start = _fmt(start) if start else None
        else:
            if prev_end is None:
                s.start = None
                continue
            _place_check(planner, s, prev_end, check_deadline)
        prev_end = _end(s)


def _new_sessions(teach_min: int) -> list[StudySession]:
    return [StudySession(kind="teach", duration_min=teach_min),
            StudySession(kind="check", number=1, duration_min=CHECK_MIN),
            StudySession(kind="check", number=2, duration_min=CHECK_MIN)]


# --------------------------------------------------------------- views

def _label(s: StudySession) -> str:
    return "Learn" if s.kind == "teach" else f"Check {s.number}"


def session_view(c: Course, s: StudySession) -> dict:
    due = _dt(c.due)
    start = _dt(s.start)
    upcoming = next((x for x in c.sessions if x.state == "planned"), None)
    return {"id": s.id, "course_id": c.id, "kind": s.kind, "number": s.number, "label": _label(s), "topic": c.topic,
            "capability": c.capability, "exercise": c.exercise, "company": c.company, "company_id": c.company_id,
            "start": s.start, "duration_min": s.duration_min, "state": s.state, "score": s.score,
            "missed": s.missed, "moved": s.moved, "is_next": upcoming is not None and upcoming.id == s.id,
            "after_interview": bool(start and due and start >= due), "course_state": c.state}


def course_view(c: Course) -> dict:
    sessions = [session_view(c, s) for s in c.sessions]
    return {"id": c.id, "topic": c.topic, "capability": c.capability, "exercise": c.exercise, "company": c.company,
            "company_id": c.company_id, "estimated_min": c.estimated_min, "due": c.due, "due_step": c.due_step,
            "state": c.state, "reason": c.reason, "outcome": c.outcome, "sessions": sessions,
            "unplaced": sum(1 for s in c.sessions if s.state == "planned" and not s.start),
            "minutes": sum(s.duration_min for s in c.sessions if s.state == "planned")}


def _find_session(book: LearningBook, session_id: str) -> tuple[Course, int]:
    for c in book.courses:
        for i, s in enumerate(c.sessions):
            if s.id == session_id:
                return c, i
    raise companies.NotFound(f"No study session {session_id!r}")


def _find_course(book: LearningBook, course_id: str) -> Course:
    c = next((c for c in book.courses if c.id == course_id), None)
    if c is None:
        raise companies.NotFound(f"No course {course_id!r}")
    return c


# ------------------------------------------------------------------ propose / accept

def _need_hours() -> StudyHours:
    h = profile.settings().study
    if not hours_set(h):
        raise ValueError("Set your working and study hours on the Profile page first: sessions are planned in them.")
    return h


def propose(company: str, exercises: list[str] | None = None) -> dict:
    """Propose slots for an application's prep: one course per exercise (the topic to learn), each a teach
    session then checks, between now and the interview. Nothing is booked until accept()."""
    h = _need_hours()
    c = companies.resolve(company)
    p = prep.get(c.id)
    if not p or not p.result or not p.result.exercises:
        raise ValueError(f"{c.name} has no interview prep yet: prepare it first, then schedule it.")
    due, step = next_interview(c.id)
    best: dict[str, int] = {}
    for a in practice.store.load().attempts:
        if a.company_id == c.id:
            best[a.exercise] = max(best.get(a.exercise, 0), a.score)
    with store.transaction() as book:
        book.courses = [x for x in book.courses if not (x.company_id == c.id and x.state == "proposed")]
        taken = {x.exercise for x in book.courses if x.company_id == c.id and x.state != "dropped"}
    wanted = {e.strip().lower() for e in exercises} if exercises else None
    planner = Planner(h)
    proposed, skipped = [], []
    for e in p.result.exercises:
        if wanted is not None and e.title.lower() not in wanted and e.topic.lower() not in wanted:
            continue
        if e.title in taken:
            skipped.append({"exercise": e.title, "why": "already scheduled"})
            continue
        if best.get(e.title, 0) >= GOOD:
            skipped.append({"exercise": e.title, "why": f"already scored {best[e.title]}/10"})
            continue
        minutes = estimate(e)
        course = Course(company_id=c.id, company=c.name, topic=e.topic or e.title, capability=e.capability,
                        exercise=e.title, estimated_min=minutes, due=due, due_step=step, reason="prep",
                        sessions=_new_sessions(minutes))
        plan_course(course, planner)
        proposed.append(course)
    with store.transaction() as book:
        book.courses += proposed
    views = [course_view(x) for x in proposed]
    unplaced = sum(v["unplaced"] for v in views)
    after = sum(1 for v in views for s in v["sessions"] if s["after_interview"])
    note = ""
    if unplaced:
        note = (f"{unplaced} session(s) don't fit before {'the interview' if due else 'the next 30 days'}: add study "
                f"time{'' if h.weekend_free else ' (weekends are off)'} or raise the daily maximum on your Profile.")
    elif after:
        note = f"{after} check(s) fall after the interview: there is no room before it (before is better)."
    return {"company": c.name, "company_id": c.id, "due": due, "due_step": step, "courses": views,
            "skipped": skipped, "minutes": sum(v["minutes"] for v in views), "unplaced": unplaced, "note": note}


def accept(company: str | None = None, courses: list[str] | None = None) -> dict:
    """Accept proposed courses (all of an application's, or the given ids) and suggested checks (by id):
    their sessions are booked. Proposed courses of that application not accepted are dropped."""
    h = _need_hours()
    company_id = companies.resolve(company).id if company else None
    with store.transaction() as book:
        chosen = [x for x in book.courses if x.state in ("proposed", "suggested") and
                  ((courses and x.id in courses) or (not courses and company_id and x.company_id == company_id
                                                     and x.state == "proposed"))]
        if not chosen:
            raise ValueError("Nothing to accept: propose slots first.")
        ids = {x.id for x in chosen}
        planner = Planner(h, ignore={s.id for x in chosen for s in x.sessions})
        stale = [x for x in chosen if x.state == "suggested" or any(
            s.state == "planned" and (not s.start or _dt(s.start) < planner.now) for s in x.sessions)]
        stale_ids = {x.id for x in stale}
        for x in chosen:  # slots still valid are kept (proposed together, they don't overlap)
            if x.id not in stale_ids:
                for s in x.sessions:
                    if s.state == "planned" and s.start:
                        planner.book(_dt(s.start), s.duration_min)
        for x in stale:
            plan_course(x, planner)
        for x in chosen:
            x.state = "scheduled"
        companies_done = {x.company_id for x in chosen if x.reason == "prep"}
        book.courses = [x for x in book.courses
                        if not (x.state == "proposed" and x.company_id in companies_done and x.id not in ids)]
    return {"accepted": [course_view(x) for x in chosen]}


def decline(company: str | None = None, courses: list[str] | None = None) -> dict:
    """Drop proposed or suggested courses (an application's proposals, or the given ids), or stop scheduled
    ones (by id): their planned sessions are freed."""
    company_id = companies.resolve(company).id if company else None
    with store.transaction() as book:
        n = 0
        for x in book.courses:
            if (courses and x.id in courses) or (not courses and company_id and x.company_id == company_id
                                                 and x.state == "proposed"):
                if x.state in ("proposed", "suggested", "scheduled"):
                    x.state = "dropped"
                    n += 1
        book.courses = [x for x in book.courses if not (x.state == "dropped" and x.reason == "prep"
                                                        and not any(s.state == "done" for s in x.sessions))]
    return {"dropped": n}


# --------------------------------------------------------------- completing sessions

def _after_done(c: Course, i: int, planner_hours: StudyHours | None) -> str:
    """After session i is done: decide on a 3rd check, finish the course, or move later checks that are now
    too close. Returns what happens next, in a sentence."""
    s = c.sessions[i]
    checks = {x.number: x for x in c.sessions if x.kind == "check"}
    if s.kind == "check" and s.number == 2:
        first = checks.get(1)
        drop = first is not None and first.score is not None and s.score is not None and first.score - s.score >= DROP
        if (s.score is not None and s.score < GOOD) or drop:
            if 3 not in checks:
                c.sessions.append(StudySession(kind="check", number=3, duration_min=CHECK_MIN))
            why = f"scored {s.score}/10" if s.score is not None and s.score < GOOD else \
                f"dropped from {first.score} to {s.score}"
            msg = f"Check 2 {why}: a 3rd check is planned"
        else:
            c.state, c.outcome = "done", "learned"
            for x in c.sessions:
                if x.state == "planned":
                    x.state = "skipped"
            return f"{c.topic}: learned ✓ (two good checks)."
    elif s.kind == "check" and s.number >= 3:
        c.state, c.outcome = "done", "learned" if (s.score or 0) >= GOOD else "needs_work"
        return f"{c.topic}: learned ✓" if c.outcome == "learned" else \
            f"{c.topic}: still shaky after 3 checks: learn it again (a new teach session) when you can."
    else:
        msg = ""
    # later planned sessions too close to this one, or not placed: place them again
    nxt = next((j for j in range(i + 1, len(c.sessions)) if c.sessions[j].state == "planned"), None)
    if nxt is None:
        return msg or f"{c.topic}: nothing else planned."
    s2 = c.sessions[nxt]
    done = _dt(s.done_at) or datetime.now()
    lo = GAPS.get(s2.number, (3, 4))[0]
    if planner_hours and c.state in ("scheduled", "suggested") and \
            (not s2.start or _dt(s2.start).date() < done.date() + timedelta(days=lo)):
        plan_course(c, Planner(planner_hours, ignore={x.id for x in c.sessions}), nxt)
    s2 = c.sessions[nxt]
    when = _dt(s2.start)
    nxt_txt = f"{_label(s2)} on {when:%a %d %b at %H:%M}" if when else f"{_label(s2)}: no free slot found"
    return f"{msg + '. ' if msg else ''}Next: {nxt_txt}."


def complete(session: str, score: int, missed: list[str] | None = None, attempt_id: str = "") -> dict:
    """Mark a study session done with its score /10 and the rubric points it missed (the next check covers
    them). Decides a 3rd check after check 2, and moves later checks if needed."""
    h = profile.settings().study
    with store.transaction() as book:
        c, i = _find_session(book, session)
        s = c.sessions[i]
        s.state, s.score = "done", max(0, min(int(score), 10))
        s.missed = [m for m in (missed or []) if m.strip()]
        s.done_at = _fmt(datetime.now())
        s.attempt_id = attempt_id
        if c.state == "proposed":
            c.state = "scheduled"
        nxt = _after_done(c, i, h if hours_set(h) else None)
        view = course_view(c)
    return {"course": view, "next": nxt}


def _match(book: LearningBook, a: PracticeAttempt) -> tuple[Course, int] | None:
    """The planned session a practice answer completes: the teach of the course on that exercise, or a check
    of the course on that topic (an answer to another question than the exercise)."""
    key = topic_key(a.topic, a.exercise)
    for c in book.courses:
        if c.state not in ("scheduled", "suggested") or c.company_id != a.company_id:
            continue
        i = next((j for j, s in enumerate(c.sessions) if s.state == "planned"), None)
        if i is None:
            continue
        s = c.sessions[i]
        if s.kind == "teach" and a.exercise == c.exercise:
            return c, i
        if s.kind == "check" and a.exercise != c.exercise and key == topic_key(c.topic, c.exercise):
            return c, i
    return None


def on_attempt(a: PracticeAttempt, session: str = "") -> dict | None:
    """After a graded practice answer: complete the study session it belongs to, or suggest checks when it
    shows a new notion. What happened, for the assistant to tell the user (None: nothing)."""
    if session:
        return complete(session, a.score, a.missed, a.id)
    found = _match(store.load(), a)
    if found:
        c, i = found
        return complete(c.sessions[i].id, a.score, a.missed, a.id)
    return suggest(a)


def suggest(a: PracticeAttempt) -> dict | None:
    """A course of checks when the answer shows a new notion: first answer on the topic below 7, or the first
    7+ after lower scores (it may be luck)."""
    key = topic_key(a.topic, a.exercise)
    if not key:
        return None
    before = [x for x in practice.store.load().attempts if x.id != a.id and topic_key(x.topic, x.exercise) == key]
    if not before and a.score < GOOD:
        reason = "new notion"
    elif before and a.score >= GOOD and all(x.score < GOOD for x in before):
        reason = "first high score"
    else:
        return None
    with store.transaction() as book:
        if any(topic_key(c.topic, c.exercise) == key and (c.state in ("proposed", "suggested", "scheduled")
                                                          or c.outcome == "learned") for c in book.courses):
            return None
        due, step = next_interview(a.company_id) if a.company_id else (None, "")
        teach = StudySession(kind="teach", state="done", score=a.score, missed=a.missed, attempt_id=a.id,
                             done_at=_fmt(datetime.now()), duration_min=0)
        course = Course(company_id=a.company_id, company=a.company, topic=a.topic or a.exercise,
                        capability=a.capability, exercise=a.exercise, estimated_min=0, due=due, due_step=step,
                        state="suggested", reason=reason,
                        sessions=[teach, StudySession(kind="check", number=1, duration_min=CHECK_MIN),
                                  StudySession(kind="check", number=2, duration_min=CHECK_MIN)])
        book.courses.append(course)
    why = "new for you" if reason == "new notion" else "your first good score on it"
    return {"suggested": course_view(course),
            "next": f"{course.topic} is {why}: offer to schedule 2 checks (learning.accept with course {course.id})."}


# ------------------------------------------------------------------ moving

def move(session: str, start: str | None = None) -> dict:
    """Move a planned session: to `start` ("YYYY-MM-DDTHH:MM") if given, else to the next free slot. Later
    checks of the course move too when they would come too soon after it."""
    h = _need_hours()
    with store.transaction() as book:
        c, i = _find_session(book, session)
        s = c.sessions[i]
        if s.state != "planned":
            raise ValueError("Only a planned session can move.")
        if start:
            when = _dt(start)
            if when is None:
                raise ValueError("Give the new start as YYYY-MM-DDTHH:MM")
            s.start = _fmt(when)
            follow = i + 1
        else:
            planner = Planner(h, ignore={x.id for x in c.sessions[i:]})
            old = _dt(s.start)
            planner.now = max(planner.now, _round_up(old + timedelta(minutes=s.duration_min))) if old else planner.now
            follow = i
            s.start = None
            plan_course(c, planner, i)
        s.moved += 1
        nxt = next((j for j in range(i + 1, len(c.sessions)) if c.sessions[j].state == "planned"), None)
        if nxt is not None and follow > i:
            n = c.sessions[nxt]
            if not n.start or _dt(n.start).date() < _dt(s.start).date() + timedelta(days=GAPS.get(n.number, (3, 4))[0]):
                plan_course(c, Planner(h, ignore={x.id for x in c.sessions[nxt:]}), nxt)
        return {"course": course_view(c), "session": session_view(c, s)}


def reflow(now: datetime | None = None) -> list[dict]:
    """Sessions whose time passed without being done move to the next free slot (with the checks after them).
    Returns the moved ones."""
    h = profile.settings().study
    now = now or datetime.now()
    book = store.load()
    late = [(c.id, i) for c in book.courses if c.state == "scheduled"
            for i in [next((j for j, s in enumerate(c.sessions) if s.state == "planned"), None)]
            if i is not None and c.sessions[i].start and _end(c.sessions[i]) + GRACE < now]
    if not late or not hours_set(h):
        return []
    moved = []
    with store.transaction() as book:
        for course_id, i in late:
            c = _find_course(book, course_id)
            for s in c.sessions[i:]:
                if s.state == "planned":
                    s.start = None
            plan_course(c, Planner(h, now=now, ignore={x.id for x in c.sessions[i:]}), i)
            c.sessions[i].moved += 1
            moved.append(session_view(c, c.sessions[i]))
    return moved


# ------------------------------------------------------------------ reading

def courses(company: str | None = None, include_done: bool = True) -> list[dict]:
    """Courses (all, or one application's): the next session soonest first, finished ones last."""
    company_id = companies.resolve(company).id if company else None
    out = [course_view(c) for c in store.load().courses
           if c.state != "dropped" and (company_id is None or c.company_id == company_id)
           and (include_done or c.state != "done")]
    first = lambda v: min((x["start"] or "9" for x in v["sessions"] if x["state"] == "planned"), default="9")
    return sorted(out, key=lambda v: (v["state"] == "done", first(v)))


def agenda(days: int = 7) -> dict:
    """Planned sessions from now to `days` ahead (missed ones are moved first), and suggested checks."""
    moved = reflow()
    now = datetime.now()
    until = now + timedelta(days=days)
    sessions = []
    for c in store.load().courses:
        if c.state != "scheduled":
            continue
        for s in c.sessions:
            start = _dt(s.start)
            if s.state == "planned" and start and start + timedelta(minutes=s.duration_min) >= now - GRACE \
                    and start <= until:
                sessions.append(session_view(c, s))
    sessions.sort(key=lambda s: s["start"])
    today = now.date().isoformat()
    return {"today": [s for s in sessions if s["start"][:10] == today],
            "upcoming": [s for s in sessions if s["start"][:10] > today],
            "suggested": [course_view(c) for c in store.load().courses if c.state == "suggested"],
            "moved": moved,
            "hours_set": hours_set(profile.settings().study)}


def session_brief(session: str) -> dict:
    """What to do in a study session: the topic, the exercise (teach) or the points to cover (check), and how
    to run it. The assistant follows `instructions`."""
    book = store.load()
    c, i = _find_session(book, session)
    s = c.sessions[i]
    exercise = None
    p = prep.get(c.company_id) if c.company_id else None
    if p and p.result:
        exercise = next((e for e in p.result.exercises if e.title == c.exercise), None)
    previous = next((x for x in reversed(c.sessions[:i]) if x.state == "done"), None)
    focus = previous.missed if previous and previous.missed else []
    record = (f"practice.record(company=\"{c.company_id}\", exercise=\"{{title}}\", score, missed, feedback, "
              f"topic=\"{c.topic}\", capability=\"{c.capability}\", session=\"{s.id}\")")
    if s.kind == "teach":
        instructions = (
            f"Teach session on “{c.topic}” ({s.duration_min} min) for the {c.company} interview. 1) Teach the notion "
            "step by step, concise, with a concrete example, asking the user one or two quick questions to check "
            "they follow. 2) Give the exercise prompt below (never the solution first) and wait for their answer. "
            "3) Grade it strictly with the rubric (score /10, points hit and missed). 4) Log it with "
            + record.replace("{title}", c.exercise or c.topic)
            + ". The app then plans check 1 and tells you when: tell the user.")
    else:
        must = "; ".join(focus) if focus else "the topic's key points (the rubric below)"
        instructions = (
            f"Check {s.number} on “{c.topic}” ({s.duration_min} min). Ask 2 or 3 NEW short questions, never the "
            f"exercise “{c.exercise}” itself, that together require each of these points the user missed last time: "
            f"{must}. One question at a time. Grade /10 with at least half the points on those, and list the ones "
            "still missed. Log it with " + record.replace("{title}", f"Check {s.number} · {c.topic}")
            + ". The app then decides if a 3rd check is needed: tell the user what comes next.")
    return {"session": session_view(c, s), "course": course_view(c), "focus": focus,
            "previous": {"label": _label(previous), "score": previous.score} if previous else None,
            "exercise": exercise.model_dump(mode="json") if exercise else None, "instructions": instructions}


# ------------------------------------------------------------------ calendar

def events(app_url: str = "") -> list[dict]:
    """Planned sessions of accepted courses as calendar events (for the calendar feed)."""
    out = []
    for c in store.load().courses:
        if c.state != "scheduled":
            continue
        for s in c.sessions:
            start = _dt(s.start)
            if s.state != "planned" or not start or start < datetime.now() - timedelta(days=7):
                continue
            title = f"📚 Learn: {c.topic}" if s.kind == "teach" else f"✅ Check {s.number}: {c.topic}"
            out.append({"uid": f"study-{s.id}@ezri", "title": f"{title} ({c.company})" if c.company else title,
                        "start": _fmt(start), "end": _fmt(start + timedelta(minutes=s.duration_min)),
                        "all_day": False, "location": "", "alarms": ["-PT10M"],
                        "description": f"Study session for {c.company or 'your interviews'}"
                                       + (f" ({c.due_step})" if c.due_step else "")
                                       + (f". Start it from Today: {app_url.rstrip('/')}/#/today" if app_url else ".")})
    return out
