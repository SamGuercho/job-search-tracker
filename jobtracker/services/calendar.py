"""Interviews in the user's calendar: one event per scheduled step (date, and time if known).

- step_ics(): a one-event .ics file. Phones open it in their calendar app ("Add to calendar").
- feed_ics(): every upcoming interview, as a calendar to subscribe to from a device on the tailnet (Apple
  Calendar, Outlook…): it updates when steps change. Google Calendar can't subscribe (its servers can't reach
  a private Tailscale address): use its per-event link instead (built in the browser from event()).

Times are "floating" (no time zone): the event happens at 14:30 wherever the phone is, which is what the user
typed. Each event has a stable UID, so adding it again updates it instead of duplicating it.
"""
from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

from ..models import Company
from ..process import Node
from . import companies

PRODID = "-//Ezri//Interviews//EN"


def event(company: Company, node: Node, app_url: str = "") -> dict:
    """The calendar event of a step: title, start/end (or all-day), where, and a description with the details."""
    if not node.date:
        raise ValueError(f"“{node.name}” has no date yet: set one on the step first")
    day = date.fromisoformat(node.date[:10])
    if node.time:
        start = datetime.combine(day, datetime.strptime(node.time, "%H:%M").time())
        end = start + timedelta(minutes=node.duration_min or 60)
    else:
        start, end = day, day + timedelta(days=1)
    lines = [f"{company.position} at {company.name}" if company.position else company.name]
    if company.contact.name:
        lines.append(f"Contact: {company.contact.name}" + (f" ({company.contact.role})" if company.contact.role else ""))
    if node.notes.strip():
        lines += ["", node.notes.strip()]
    if company.job_url:
        lines += ["", f"Job posting: {company.job_url}"]
    if app_url:
        lines += ["", f"Application, prep and notes: {app_url.rstrip('/')}/#/company/{company.id}"]
    return {
        "uid": f"{node.id}@job-search-tracker",
        "title": f"{node.name} – {company.name}",
        "all_day": not node.time,
        "start": start.isoformat(timespec="minutes") if node.time else day.isoformat(),
        "end": end.isoformat(timespec="minutes") if node.time else end.isoformat(),
        "location": node.location or company.address or company.location,
        "description": "\n".join(lines),
    }


def for_step(company_ref: str, step: str) -> tuple[Company, Node]:
    c = companies.resolve(company_ref)
    node = next((n for n in c.process.nodes if n.id == step or n.name.lower() == step.lower()), None)
    if node is None:
        raise companies.NotFound(f"No step {step!r} in {c.name}")
    return c, node


def upcoming(days_back: int = 7) -> list[tuple[Company, Node]]:
    """Scheduled steps with a date, from a week ago on, of applications still open."""
    since = (date.today() - timedelta(days=days_back)).isoformat()
    out = []
    for c in companies.list_companies():
        if c.status == "Rejected":
            continue
        for n in c.process.nodes:
            if n.kind != "closed" and n.state == "planned" and n.date and n.date[:10] >= since:
                out.append((c, n))
    return sorted(out, key=lambda cn: (cn[1].date, cn[1].time or ""))


# ------------------------------------------------------------------- iCalendar

def _escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


def _fold(line: str) -> str:
    """Lines longer than 75 octets continue on the next line after a space (RFC 5545)."""
    out, current = [], b""
    for ch in line:
        encoded = ch.encode()
        if len(current) + len(encoded) > (75 if not out else 74):
            out.append(current.decode())
            current = b""
        current += encoded
    out.append(current.decode())
    return "\r\n ".join(out)


def _when(value: str, all_day: bool) -> str:
    if all_day:
        return f";VALUE=DATE:{value.replace('-', '')}"
    return ":" + datetime.fromisoformat(value).strftime("%Y%m%dT%H%M%S")


def _vevent(e: dict) -> list[str]:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VEVENT", f"UID:{e['uid']}", f"DTSTAMP:{stamp}", f"DTSTART{_when(e['start'], e['all_day'])}",
             f"DTEND{_when(e['end'], e['all_day'])}", f"SUMMARY:{_escape(e['title'])}",
             f"DESCRIPTION:{_escape(e['description'])}"]
    if e["location"]:
        lines.append(f"LOCATION:{_escape(e['location'])}")
    # reminders: the day before, and 1 hour before (all-day events: 9:00 the day before)
    for trigger in e.get("alarms") or (["-P1D", "-PT1H"] if not e["all_day"] else ["-PT15H"]):
        lines += ["BEGIN:VALARM", "ACTION:DISPLAY", f"DESCRIPTION:{_escape(e['title'])}", f"TRIGGER:{trigger}", "END:VALARM"]
    return lines + ["END:VEVENT"]


def ics(events: list[dict], name: str = "") -> str:
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", f"PRODID:{PRODID}", "CALSCALE:GREGORIAN", "METHOD:PUBLISH"]
    if name:
        lines += [f"X-WR-CALNAME:{_escape(name)}", "REFRESH-INTERVAL;VALUE=DURATION:PT1H", "X-PUBLISHED-TTL:PT1H"]
    for e in events:
        lines += _vevent(e)
    lines.append("END:VCALENDAR")
    return "\r\n".join(_fold(line) for line in lines) + "\r\n"


def step_ics(company_ref: str, step: str, app_url: str = "") -> tuple[str, str]:
    """(file name, .ics text) for one step."""
    c, n = for_step(company_ref, step)
    e = event(c, n, app_url)
    return f"{e['title']}.ics", ics([e])


def feed_ics(app_url: str = "") -> str:
    """Upcoming interviews and the study sessions you accepted (services/learning.py)."""
    from . import learning  # (imports calendar)
    return ics([event(c, n, app_url) for c, n in upcoming()] + learning.events(app_url), name="Job interviews")
