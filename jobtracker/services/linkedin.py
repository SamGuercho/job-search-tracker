"""LinkedIn, from its own data export (LinkedIn offers no API for your network or profile).

Settings → Data privacy → Get a copy of your data. The zip arrives by email; importing it here keeps only:
  - Connections.csv                      -> your 1st-degree contacts (name, company, position, link)
  - Profile / Positions / Education / Skills.csv -> your profile, to check it against your master CV
Everything else in the zip (messages, invitations…) is never read nor kept, and neither is the zip itself.
Importing again replaces what the new zip contains and keeps the rest.

Uses:
  - contacts_at(company): people you know at an application's company, to ask for a referral
  - search_links(company): LinkedIn searches for people there (2nd degree = someone can introduce you)
  - check(): differences between your LinkedIn profile and your master CV
"""
from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import datetime
from pathlib import Path
from typing import BinaryIO
from urllib.parse import quote

from ..models import (
    LinkedInBook,
    LinkedInContact,
    LinkedInEducation,
    LinkedInPosition,
    LinkedInProfile,
)
from ..storage import UserDocument
from . import companies, master_cv

store = UserDocument("linkedin.json", LinkedInBook)

EXPORT_URL = "https://www.linkedin.com/mypreferences/d/download-my-data"
MAX_CONTACTS_SHOWN = 8

# ------------------------------------------------------------------ parsing


def _rows(text: str, first_column: str) -> list[dict]:
    """CSV rows of an export file. Connections.csv starts with a few lines of notes before its header."""
    lines = text.lstrip("﻿").splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip().strip('"').startswith(first_column)), None)
    if start is None:
        return []
    reader = csv.DictReader(io.StringIO("\n".join(lines[start:])))
    return [{(k or "").strip(): (v or "").strip() for k, v in row.items()} for row in reader]


def norm_date(value: str) -> str:
    """'Mar 2021', '03/2021', '2021-03', '15 Mar 2024', '2021' -> 'YYYY-MM' (or 'YYYY', 'YYYY-MM-DD'); 'Present' -> ''."""
    v = value.strip()
    if not v or v.lower() in ("present", "current", "now", "today"):
        return ""
    for fmt, out in (("%d %b %Y", "%Y-%m-%d"), ("%Y-%m-%d", "%Y-%m-%d"), ("%b %Y", "%Y-%m"), ("%B %Y", "%Y-%m"),
                     ("%m/%Y", "%Y-%m"), ("%Y-%m", "%Y-%m"), ("%Y", "%Y")):
        try:
            return datetime.strptime(v, fmt).strftime(out)
        except ValueError:
            continue
    m = re.search(r"(19|20)\d{2}", v)
    return m.group(0) if m else v


def parse_contacts(text: str) -> list[LinkedInContact]:
    out = []
    for r in _rows(text, "First Name"):
        c = LinkedInContact(first_name=r.get("First Name", ""), last_name=r.get("Last Name", ""), url=r.get("URL", ""),
                            email=r.get("Email Address", ""), company=r.get("Company", ""),
                            position=r.get("Position", ""), connected_on=norm_date(r.get("Connected On", "")))
        if c.name:
            out.append(c)
    return out


def parse_profile(files: dict[str, str]) -> LinkedInProfile | None:
    if not any(n in files for n in ("Profile.csv", "Positions.csv", "Education.csv", "Skills.csv")):
        return None
    profile = (_rows(files.get("Profile.csv", ""), "First Name") or [{}])[0]
    positions = [LinkedInPosition(company=r.get("Company Name", ""), title=r.get("Title", ""),
                                  description=r.get("Description", ""), location=r.get("Location", ""),
                                  start=norm_date(r.get("Started On", "")), end=norm_date(r.get("Finished On", "")))
                 for r in _rows(files.get("Positions.csv", ""), "Company Name") if r.get("Company Name")]
    education = [LinkedInEducation(school=r.get("School Name", ""), degree=r.get("Degree Name", ""),
                                   start=norm_date(r.get("Start Date", "")), end=norm_date(r.get("End Date", "")))
                 for r in _rows(files.get("Education.csv", ""), "School Name") if r.get("School Name")]
    skills = [r["Name"] for r in _rows(files.get("Skills.csv", ""), "Name") if r.get("Name")]
    return LinkedInProfile(headline=profile.get("Headline", ""), summary=profile.get("Summary", ""),
                           positions=positions, education=education, skills=skills)


WANTED = ("Connections.csv", "Profile.csv", "Positions.csv", "Education.csv", "Skills.csv")


def read_export(name: str, data: BinaryIO) -> dict[str, str]:
    """The files we use from an export zip (wherever they are in it), or a lone Connections.csv."""
    if name.lower().endswith(".csv"):
        base = Path(name).name
        return {base if base in WANTED else "Connections.csv": data.read().decode("utf-8-sig", "replace")}
    try:
        with zipfile.ZipFile(data) as z:
            files = {}
            for info in z.infolist():
                base = Path(info.filename).name
                if base in WANTED and base not in files:
                    files[base] = z.read(info).decode("utf-8-sig", "replace")
            return files
    except zipfile.BadZipFile as e:
        raise ValueError("Not a zip file: upload the zip LinkedIn sent you (or its Connections.csv).") from e


def import_export(name: str, data: BinaryIO) -> dict:
    """Import a LinkedIn export: replaces the contacts and/or profile it contains, keeps the rest."""
    files = read_export(name, data)
    contacts = parse_contacts(files["Connections.csv"]) if "Connections.csv" in files else None
    profile = parse_profile(files)
    if contacts is None and profile is None:
        raise ValueError("No Connections.csv or profile files in it: is it LinkedIn's data export?")
    now = datetime.now().isoformat(timespec="seconds")
    with store.transaction() as book:
        if contacts is not None:
            book.contacts, book.contacts_imported_at = contacts, now
        if profile is not None:
            book.profile, book.profile_imported_at = profile, now
    return {**status(), "imported": sorted(files)}


def status() -> dict:
    book = store.load()
    return {"contacts": len(book.contacts), "contacts_imported_at": book.contacts_imported_at,
            "profile": book.profile is not None, "profile_imported_at": book.profile_imported_at,
            "export_url": EXPORT_URL}


def get_profile() -> LinkedInProfile | None:
    return store.load().profile


# ----------------------------------------------------------------- contacts

LEGAL = {"inc", "incorporated", "ltd", "limited", "llc", "llp", "plc", "gmbh", "ag", "sa", "sas", "sarl", "bv", "nv",
         "corp", "corporation", "co", "company", "group", "holdings", "the", "israel", "il"}


def norm_company(name: str) -> str:
    words = re.sub(r"[^\w\s]", " ", name.lower()).split()
    kept = [w for w in words if w not in LEGAL]
    return " ".join(kept or words)


def same_company(a: str, b: str) -> bool:
    """'Northwind' ~ 'Northwind Labs Ltd.', 'Meta' !~ 'Metal Works'."""
    x, y = norm_company(a), norm_company(b)
    if not x or not y:
        return False
    return x == y or x.startswith(y + " ") or y.startswith(x + " ")


RECRUITING = re.compile(r"recruit|talent|\bhr\b|people partner|human resources|sourc", re.I)


FILLER = {"senior", "junior", "lead", "head", "the", "and"}


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z]+", text.lower()) if len(w) > 2} - FILLER


def contacts_at(company_name: str, position: str = "") -> list[dict]:
    """Your contacts who worked at that company when you exported: recruiters first, then those in a role close
    to the job's, then the most recently connected."""
    wanted = _words(position)
    out = []
    for c in store.load().contacts:
        if not same_company(c.company, company_name):
            continue
        recruiter = bool(RECRUITING.search(c.position))
        close = bool(wanted & _words(c.position))
        out.append({"name": c.name, "position": c.position, "company": c.company, "url": c.url, "email": c.email,
                    "connected_on": c.connected_on, "recruiter": recruiter, "close_role": close})
    out.sort(key=lambda c: c["name"])
    out.sort(key=lambda c: c["connected_on"], reverse=True)
    return sorted(out, key=lambda c: (not c["recruiter"], not c["close_role"]))


def search_links(company_name: str, position: str = "") -> dict:
    """LinkedIn people searches for that company: your 1st degree (also those who joined since your export),
    your 2nd degree (a contact of yours can introduce you), and its recruiters."""
    base = "https://www.linkedin.com/search/results/people/?keywords="
    network = lambda degrees: "&network=" + quote(str(degrees).replace("'", '"').replace(" ", ""))
    return {"first_degree": base + quote(company_name) + network(["F"]),
            "second_degree": base + quote(company_name) + network(["S"]),
            "recruiters": base + quote(f"{company_name} recruiter")}


def for_application(ref: str) -> dict:
    c = companies.resolve(ref)
    book = store.load()
    found = contacts_at(c.name, c.position)
    return {"company": c.name, "company_id": c.id, "contacts": found, "imported": book.contacts_imported_at is not None,
            "contacts_imported_at": book.contacts_imported_at, "search": search_links(c.name, c.position)}


def find(query: str, limit: int = 20) -> list[dict]:
    """Contacts whose name, company or position contains the words of `query`."""
    words = query.lower().split()
    hits = [c for c in store.load().contacts
            if all(w in f"{c.name} {c.company} {c.position}".lower() for w in words)]
    return [{"name": c.name, "position": c.position, "company": c.company, "url": c.url,
             "connected_on": c.connected_on} for c in hits[:limit]]


def counts_by_company(apps) -> dict[str, int]:
    """{company id: how many contacts work there}, for lists (Today)."""
    contacts = store.load().contacts
    if not contacts:
        return {}
    return {a.id: n for a in apps if (n := sum(same_company(c.company, a.name) for c in contacts))}


# -------------------------------------------------- profile vs master CV


def _same_date(a: str, b: str) -> bool:
    """Equal at the precision both have ('2021' == '2021-03')."""
    if not a or not b:
        return a == b
    n = min(len(a), len(b), 7)
    return a[:n] == b[:n]


def _show(d: str) -> str:
    return d or "present"


def check() -> dict:
    """Differences between your LinkedIn profile (last import) and your master CV, as a list of issues:
    {kind, severity ("fix" = recruiters will notice, "info" = may be on purpose), message, cv, linkedin}."""
    book = store.load()
    if book.profile is None:
        raise ValueError("No LinkedIn profile imported yet: import your LinkedIn data export first.")
    li, cv = book.profile, master_cv.get_master()
    issues: list[dict] = []
    add = lambda kind, severity, message, cv_value="", li_value="": issues.append(
        {"kind": kind, "severity": severity, "message": message, "cv": cv_value, "linkedin": li_value})

    # Experiences: matched by company, then the closest dates
    unmatched = list(li.positions)
    for e in cv.experiences:
        candidates = [p for p in unmatched if same_company(p.company, e.company)]
        if not candidates:
            add("experience", "fix", f"{e.role} at {e.company} is on your CV, not on LinkedIn.", f"{e.role}, {e.company}")
            continue
        start, end = norm_date(e.start), norm_date(e.end)
        p = next((p for p in candidates if _same_date(p.start, start)), candidates[0])
        unmatched.remove(p)
        where = f"at {e.company}"
        if not (_same_date(p.start, start) and _same_date(p.end, end)):
            add("dates", "fix", f"Dates {where} differ.",
                f"{e.start or '?'} – {_show(e.end)}", f"{p.start or '?'} – {_show(p.end)}")
        if e.role and p.title and _words(e.role) != _words(p.title):
            add("title", "fix", f"Job title {where} differs.", e.role, p.title)
    for p in unmatched:
        add("experience", "info",
            f"{p.title or 'A role'} at {p.company} is on LinkedIn, not on your CV (fine if left out on purpose).",
            "", f"{p.title}, {p.company}")

    # Education
    for ed in cv.education:
        match = next((x for x in li.education if same_company(x.school, ed.school)), None)
        if not match:
            add("education", "fix", f"{ed.school} is on your CV, not on LinkedIn.", f"{ed.degree}, {ed.school}".strip(", "))
        elif not (_same_date(match.start, norm_date(ed.start)) and _same_date(match.end, norm_date(ed.end))):
            add("dates", "fix", f"Dates at {ed.school} differ.", f"{ed.start or '?'} – {_show(ed.end)}",
                f"{match.start or '?'} – {_show(match.end)}")
    for x in li.education:
        if not any(same_company(x.school, ed.school) for ed in cv.education):
            add("education", "info", f"{x.school} is on LinkedIn, not on your CV.", "",
                f"{x.degree}, {x.school}".strip(", "))

    # Skills: a CV skill missing on LinkedIn hides you from recruiters' searches
    li_skills = [s.lower() for s in li.skills]
    found_in_li = lambda skill: any(master_cv.keyword_pattern(skill).search(s) for s in li_skills)
    language = re.compile(r"\((native|fluent|intermediate|basic|professional)", re.I)
    cv_skills = [i for g in cv.skills for i in g.items if not language.search(i)]
    missing = [s for s in cv_skills if not found_in_li(s)]
    if li.skills and missing:
        add("skills", "fix",
            f"{len(missing)} skill(s) of your CV are not in your LinkedIn skills: recruiters search by skill.",
            ", ".join(missing))
    vocabulary = master_cv.keyword_vocabulary()
    extra = [s for s in li.skills if not any(master_cv.keyword_pattern(k).search(s) for k in vocabulary)]
    if extra:
        add("skills", "info", f"{len(extra)} LinkedIn skill(s) appear nowhere in your CV.", "", ", ".join(extra))

    # Headline: should say the same thing as your CV title(s)
    titles = [h.title for h in cv.headlines if h.title] or [cv.profile.title]
    if li.headline and titles and not any(_words(t) & _words(li.headline) for t in titles if t):
        add("headline", "info", "Your LinkedIn headline shares no words with your CV titles.",
            " / ".join(titles), li.headline)

    issues.sort(key=lambda i: i["severity"] != "fix")
    return {"issues": issues, "fix": sum(i["severity"] == "fix" for i in issues),
            "profile_imported_at": book.profile_imported_at, "headline": li.headline}
