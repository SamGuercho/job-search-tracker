"""Reminders sent as notifications, checked every 10 minutes for every person with a registered device:

- an interview the day before (24 h before; 9:00 the day before when it has no time) and 1 hour before;
- once a day from 9:00, the applications that need a follow-up (14+ days without news).

Times are the person's local time (the time zone of their device). Each reminder is sent once (push.json
remembers it). Disabled with JOBTRACKER_REMINDERS=0.
"""
from __future__ import annotations

import os
import threading
import time
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .. import config, users
from ..logs import log
from . import calendar, companies, push

INTERVAL_S = 600
FOLLOW_UP_DAYS = 14
_started = False


def people() -> list[str]:
    """Everyone who may have devices: the owner ("") and each folder in USERS_DIR (named after the login)."""
    out = [""]
    if users.enabled() and config.USERS_DIR.exists():
        out += [p.name for p in sorted(config.USERS_DIR.iterdir()) if (p / "data" / "push.json").exists()]
    return out


def due(now_utc: datetime | None = None) -> list[dict]:
    """The current user's reminders that are due now and not sent yet."""
    book = push.store.load()
    if not book.subscriptions:
        return []
    tz = ZoneInfo(book.subscriptions[-1].timezone)
    now = (now_utc or datetime.now(ZoneInfo("UTC"))).astimezone(tz).replace(tzinfo=None)
    out = []
    for c, n in calendar.upcoming(days_back=0):
        day = datetime.fromisoformat(n.date[:10])
        start = datetime.combine(day.date(), datetime.strptime(n.time, "%H:%M").time()) if n.time else None
        url = f"/#/company/{c.id}"
        if start:
            when = start.strftime("%H:%M")
            if start - timedelta(hours=24) <= now < start - timedelta(hours=2):
                out.append({"key": f"day:{n.id}:{n.date}{n.time}", "title": f"Tomorrow {when}: {n.name}",
                            "body": f"{c.name}. Your prep is on the application page.", "url": url})
            if start - timedelta(hours=1) <= now < start:
                where = f" · {n.location}" if n.location else ""
                out.append({"key": f"hour:{n.id}:{n.date}{n.time}", "title": f"In 1 hour: {n.name}",
                            "body": f"{c.name} at {when}{where}", "url": url})
        elif day - timedelta(hours=15) <= now < day:  # 9:00 the day before
            out.append({"key": f"day:{n.id}:{n.date}", "title": f"Tomorrow: {n.name}",
                        "body": f"{c.name}. Your prep is on the application page.", "url": url})
    if now.hour >= 9:
        stale = [c for c in companies.list_companies()
                 if c.status in ("Applied", "In process") and (c.days_since_update() or 0) >= FOLLOW_UP_DAYS]
        if stale:
            names = ", ".join(c.name for c in stale[:3]) + ("…" if len(stale) > 3 else "")
            title = f"{len(stale)} applications need a follow-up" if len(stale) > 1 else "1 application needs a follow-up"
            out.append({"key": f"followup:{now.date()}", "title": title,
                        "body": f"No news for {FOLLOW_UP_DAYS}+ days: {names}", "url": "/#/"})
    return [r for r in out if r["key"] not in book.sent]


def run_once(now_utc: datetime | None = None) -> int:
    """Send the due reminders of everyone. Returns how many reminders were sent."""
    sent = 0
    for person in people():
        with users.acting_as(person):
            try:
                for r in due(now_utc):
                    if push.notify(r["title"], r["body"], r["url"], tag=r["key"]):
                        sent += 1
                    with push.store.transaction() as book:  # sent or not (no device reachable): don't retry
                        book.sent[r["key"]] = datetime.now().isoformat(timespec="seconds")
                        if len(book.sent) > 500:
                            book.sent = dict(sorted(book.sent.items(), key=lambda kv: kv[1])[-300:])
            except Exception as e:  # one person's problem must not stop the others'
                log.warning("Reminders failed for %s: %s", person or "the owner", e)
    return sent


def start() -> None:
    """Start the background loop (once per process), unless JOBTRACKER_REMINDERS=0."""
    global _started
    if _started or os.environ.get("JOBTRACKER_REMINDERS", "1") == "0":
        return
    _started = True

    def loop():
        while True:
            try:
                run_once()
            except Exception as e:
                log.warning("Reminder check failed: %s", e)
            time.sleep(INTERVAL_S)

    threading.Thread(target=loop, daemon=True, name="reminders").start()
