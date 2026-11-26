"""Reading a job posting from a link, the legitimate ways (never by pretending to be a browser).

1. Job boards' official public APIs, when the link says which board and account (or when this company's site is
   remembered to use one):
   - Greenhouse  boards(-api).greenhouse.io / job-boards(.eu).greenhouse.io / company site with ?gh_jid= (the
                 account is found through Greenhouse's public embed page, without opening the company's site)
   - Lever       jobs(.eu).lever.co/<company>/<id>
   - Ashby       jobs.ashbyhq.com/<org>/<id>
   - SmartRecruiters  jobs.smartrecruiters.com/<company>/<id>-…
2. The page itself: the job data embedded for search engines (schema.org JobPosting, JSON-LD) if present,
   else the readable text. A 429 is retried once if the site says to wait a few seconds.
When a page loads, the job board it embeds (e.g. Greenhouse "for=orbitexlabs") is remembered for its site, so
the next link from that company goes through the API. Pages that refuse robots (or need a login, like LinkedIn)
can't be read: the user sends the text from their own browser (Share → Ezri, or the "Send to Ezri" bookmark).
"""
from __future__ import annotations

import html
import json
import re
import time
import urllib.parse

from pydantic import BaseModel

from .. import config
from ..storage import JsonDocument
from . import web


class Board(BaseModel):
    platform: str     # greenhouse, lever, ashby, smartrecruiters
    account: str


class BoardBook(BaseModel):
    sites: dict[str, Board] = {}   # company site (host) -> its job board account (not personal: shared by everyone)


_board_store: dict = {}


def _boards() -> JsonDocument:
    """One document per path (not one per call), so its lock really serialises concurrent saves."""
    path = config.DATA_DIR / "job_boards.json"
    if path not in _board_store:
        _board_store[path] = JsonDocument(path, BoardBook)
    return _board_store[path]


def site_of(url: str) -> str:
    host = (urllib.parse.urlsplit(url).hostname or "").lower()
    return host.removeprefix("www.")


def remember(url: str, platform: str, account: str) -> None:
    if not (site_of(url) and account and re.fullmatch(r"[A-Za-z0-9_-]{2,80}", account)):
        return
    with _boards().transaction() as book:
        book.sites[site_of(url)] = Board(platform=platform, account=account)


def known_board(url: str) -> Board | None:
    return _boards().load().sites.get(site_of(url))


def learn_from_html(url: str, page: str) -> None:
    """Remember the Greenhouse account a company page embeds (its site then reads jobs through the API)."""
    m = re.search(r"greenhouse\.io/embed/job_(?:board(?:/js)?|app)\?for=([A-Za-z0-9_-]+)", page)
    if m and "greenhouse.io" not in site_of(url):
        remember(url, "greenhouse", m.group(1))


# ------------------------------------------------------------------- HTML -> text

def _text(fragment: str) -> str:
    fragment = html.unescape(fragment or "")           # Greenhouse sends escaped HTML
    fragment = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h\d|tr|ul|ol)>", "\n", fragment)
    fragment = re.sub(r"(?i)<li[^>]*>", "• ", fragment)
    fragment = html.unescape(re.sub(r"<[^>]+>", "", fragment))
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in fragment.splitlines()]
    return "\n".join(line for line in lines if line)


# ------------------------------------------------------------------- LinkedIn page text -> the job only
# A LinkedIn job page read in the user's browser (the "Send to Ezri" bookmark, a paste of the whole page) carries
# the whole site: navigation, "Save", "Over 200 applicants", "Tailor my resume", similar jobs, the company blurb…
# Only the top card (title, company, location, work model), "About the job" and "Requirements added by the job
# poster" describe the job; the rest pollutes the keywords.

_LI_ABOUT = re.compile(r"^(about the job|about this job)$", re.I)
_LI_POSTER = re.compile(r"^(requirements?|qualifications?|skills?) added by the job poster$", re.I)
_LI_END = re.compile(
    r"^(set alert for similar jobs|job search faster with premium|about the company|company photos|"
    r"benefits found in job post|people you can reach out to|meet the hiring team|show more jobs?|similar jobs|"
    r"more jobs|people also viewed|looking for talent\?|interested in working with us in the future\?|"
    r"see how you compare|trending employee content|recent posts|explore premium|get the linkedin app|"
    r"how you match|your job search|jobs you may be interested in|about|accessibility|"
    r"use ai to assess how you fit|am i a good fit for this job\?|show match details|"
    r"premium|exclusive job seeker insights|\S+(?: \S+){0,3} is hiring)$",
    re.I)
_LI_NOISE = re.compile(
    r"^(save|saved|share|apply|easy apply|applied|show more options|tailor my resume|show more|show less|"
    r"see more|see less|… ?more|\.\.\. ?more|… ?see more|more|less|promoted|promoted by hirer|"
    r"responses managed off linkedin|actively reviewing applicants|reposted.*|save .* at .*|"
    r"message|follow|following|report this job|retry premium.*|try premium.*|"
    r"\d+ notifications?|home|my network|jobs|messaging|notifications|me|for business|"
    r"skip to (main content|search)|\d+[+]? applicants|over \d+ (applicants|people clicked apply))$",
    re.I)
_LI_WORK = re.compile(r"^(on-site|onsite|hybrid|remote|full-time|part-time|contract|temporary|internship|"
                      r"freelance|volunteer|entry level|mid-senior level|associate|director|executive)$", re.I)
_LI_META = re.compile(r"\b(ago|applicants|clicked apply|people|reposted)\b", re.I)


def is_linkedin(text: str, url: str = "") -> bool:
    """A LinkedIn job page (by its link, or by the page's own headings and buttons)."""
    if "linkedin.com" in site_of(url or ""):
        return True
    lines = {line.strip().lower() for line in text.splitlines()}
    marks = sum(bool(s) for s in (
        lines & {"about the job"},
        any(_LI_POSTER.match(line) for line in lines),
        lines & {"easy apply", "tailor my resume", "show more options", "set alert for similar jobs", "about the company",
                 "promoted by hirer", "responses managed off linkedin"},
        any(re.search(r"\b(applicants|people clicked apply)\b", line) for line in lines)))
    return marks >= 2


def _section(lines: list[str], start: int) -> list[str]:
    """The lines after a heading, up to the next LinkedIn section; buttons and "… more" dropped."""
    out = []
    for line in lines[start + 1:]:
        if _LI_END.match(line) or _LI_POSTER.match(line) or _LI_ABOUT.match(line):
            break
        if not _LI_NOISE.match(line):
            out.append(line)
    return out


def clean_linkedin(text: str, title: str = "") -> str:
    """The job alone from a LinkedIn page's text: top card, "About the job", "Requirements added by the job
    poster". Text that isn't a LinkedIn page (no "About the job" heading) comes back as it was."""
    lines = [re.sub(r"[ \t ]+", " ", line).strip() for line in (text or "").splitlines()]
    lines = [line for line in lines if line]
    about = next((i for i, line in enumerate(lines) if _LI_ABOUT.match(line)), None)
    if about is None:
        return text
    body = _section(lines, about)
    poster = next((i for i, line in enumerate(lines) if _LI_POSTER.match(line)), None)
    asked = _section(lines, poster) if poster is not None else []

    # Top card: document title "Retail Data Analyst | Edikted | LinkedIn" names the job and the company;
    # the meta line "Los Angeles, CA · 1 week ago · Over 200 applicants" starts with the location.
    parts = [p.strip() for p in re.split(r"\s+\|\s+", re.sub(r"^\(\d+\)\s*", "", title or "")) if p.strip()]
    parts = [p for p in parts if p.lower() != "linkedin"]
    job_title, company = (parts + ["", ""])[:2]
    head = lines[:about]
    location = ""
    for i, line in reversed(list(enumerate(head))):  # the last one: a search page lists other jobs above
        if " · " in line and _LI_META.search(line):
            location = line.split(" · ")[0].strip()
            # a paste has no page title: the top card is "<company>", buttons, "<job title>", then this line
            before = [x for x in head[:i] if not _LI_NOISE.match(x) and not _LI_WORK.match(x)]
            job_title = job_title or (before[-1] if before else "")
            company = company or (before[-2] if len(before) > 1 else "")
            break
    work = list(dict.fromkeys(w for line in head for w in re.split(r"\s+·\s+|\s{2,}", line) if _LI_WORK.match(w.strip())))
    top = [job_title, " · ".join(p for p in (company, location, *work) if p)]
    out = "\n".join(p for p in top if p)
    out += ("\n\n" if out else "") + "About the job\n" + "\n".join(body)
    if asked:
        out += "\n\nRequirements added by the job poster\n" + "\n".join(asked)
    return out.strip()


def clean_posting(text: str, url: str = "", title: str = "") -> str:
    """A job description as sent from the user's browser or pasted, without the site around it (LinkedIn)."""
    return clean_linkedin(text, title) if is_linkedin(text, url) else text


def _result(url: str, title: str, company: str, location: str, text: str, via: str) -> dict:
    head = [title, f"Company: {company}" if company else "", f"Location: {location}" if location else ""]
    body = "\n".join(p for p in head if p) + "\n\n" + text.strip()
    return {"url": url, "title": title, "company": company, "location": location, "via": via,
            "text": body[:web.MAX_CHARS], "truncated": len(body) > web.MAX_CHARS}


def _json(url: str, max_bytes: int = 3_000_000) -> dict | list:
    data, _, _ = web.fetch_bytes(url, max_bytes, accept="application/json")
    return json.loads(data.decode("utf-8", "replace"))


# ---------------------------------------------------------------------- boards

def board_link(url: str) -> tuple[str, str, str] | None:
    """(platform, account, job id) when the link itself points to a job board posting."""
    u = urllib.parse.urlsplit(url)
    host, path, query = (u.hostname or "").lower(), u.path, urllib.parse.parse_qs(u.query)
    if host.endswith("greenhouse.io"):
        if "token" in query and "for" in query:
            return "greenhouse", query["for"][0], query["token"][0]
        m = re.match(r"^/([A-Za-z0-9_-]+)/jobs/(\d+)", path)
        if m:
            return "greenhouse", m.group(1), m.group(2)
    if host in ("jobs.lever.co", "jobs.eu.lever.co"):
        m = re.match(r"^/([^/]+)/([0-9a-f-]{36})", path)
        if m:
            return ("lever-eu" if ".eu." in host else "lever"), m.group(1), m.group(2)
    if host == "jobs.ashbyhq.com":
        m = re.match(r"^/([^/]+)/([0-9a-f-]{36})", path)
        if m:
            return "ashby", m.group(1), m.group(2)
    if host in ("jobs.smartrecruiters.com", "careers.smartrecruiters.com"):
        m = re.match(r"^/([^/]+)/(\d+)", path)
        if m:
            return "smartrecruiters", m.group(1), m.group(2)
    if "gh_jid" in query:  # a Greenhouse job shown on the company's own site
        board = known_board(url)
        if board and board.platform == "greenhouse":
            return "greenhouse", board.account, query["gh_jid"][0]
    return None


def discover_greenhouse(url: str) -> str | None:
    """The Greenhouse account of a company-site link with ?gh_jid=: Greenhouse's public embed page, given only the
    job number, redirects to ...?for=<account>&token=<id>. Remembered for the site. None if the job is unknown."""
    job_id = urllib.parse.parse_qs(urllib.parse.urlsplit(url).query).get("gh_jid", [""])[0]
    if not job_id.isdigit():
        return None
    try:
        page, _, final = web.fetch_bytes(f"https://boards.greenhouse.io/embed/job_app?token={job_id}", 500_000,
                                         accept="text/html", truncate=True)
    except ValueError:
        return None
    account = urllib.parse.parse_qs(urllib.parse.urlsplit(final).query).get("for", [""])[0]
    if not account:
        m = re.search(rb"[?&]for=([A-Za-z0-9_-]+)", page)
        account = m.group(1).decode() if m else ""
    if account:
        remember(url, "greenhouse", account)
    return account or None


def from_board(platform: str, account: str, job_id: str, url: str) -> dict:
    a, j = urllib.parse.quote(account), urllib.parse.quote(job_id)
    if platform == "greenhouse":
        d = _json(f"https://boards-api.greenhouse.io/v1/boards/{a}/jobs/{j}")
        return _result(d.get("absolute_url") or url, d.get("title", ""), d.get("company_name", ""),
                       (d.get("location") or {}).get("name", ""), _text(d.get("content", "")), "Greenhouse API")
    if platform in ("lever", "lever-eu"):
        api = "api.eu.lever.co" if platform == "lever-eu" else "api.lever.co"
        d = _json(f"https://{api}/v0/postings/{a}/{j}")
        parts = [d.get("descriptionPlain", "")]
        for section in d.get("lists", []):
            parts += [section.get("text", ""), _text(section.get("content", ""))]
        parts.append(d.get("additionalPlain", ""))
        location = (d.get("categories") or {}).get("location", "")
        return _result(d.get("hostedUrl") or url, d.get("text", ""), account, location,
                       "\n\n".join(p for p in parts if p), "Lever API")
    if platform == "ashby":
        # Ashby has no single-job endpoint: the whole board comes at once (big companies: several MB)
        jobs = _json(f"https://api.ashbyhq.com/posting-api/job-board/{a}", 30_000_000).get("jobs", [])
        d = next((x for x in jobs if x.get("id") == job_id), None)
        if d is None:
            raise ValueError("This job is no longer listed on the company's Ashby board")
        return _result(d.get("jobUrl") or url, d.get("title", "").strip(), account, d.get("location", ""),
                       d.get("descriptionPlain") or _text(d.get("descriptionHtml", "")), "Ashby API")
    if platform == "smartrecruiters":
        d = _json(f"https://api.smartrecruiters.com/v1/companies/{a}/postings/{j}")
        sections = (d.get("jobAd") or {}).get("sections", {})
        parts = [f"{s.get('title', '')}\n{_text(s.get('text', ''))}" for s in sections.values() if s.get("text")]
        return _result(d.get("postingUrl") or url, d.get("name", "").strip(), (d.get("company") or {}).get("name", account),
                       (d.get("location") or {}).get("fullLocation", ""), "\n\n".join(parts), "SmartRecruiters API")
    raise ValueError(f"Unknown job board {platform}")


# ----------------------------------------------------------------------- page

def job_posting_ld(page: str) -> dict | None:
    """The schema.org JobPosting embedded in a page (JSON-LD), if any."""
    for block in re.findall(r'<script[^>]+application/ld\+json[^>]*>(.*?)</script>', page, re.S | re.I):
        try:
            data = json.loads(block.strip())
        except ValueError:
            continue
        items = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for item in items:
            kind = item.get("@type") if isinstance(item, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return item
    return None


def _ld_location(ld: dict) -> str:
    loc = ld.get("jobLocation")
    loc = loc[0] if isinstance(loc, list) and loc else loc
    addr = (loc or {}).get("address", {}) if isinstance(loc, dict) else {}
    if isinstance(addr, str):
        return addr
    country = addr.get("addressCountry")
    return ", ".join(p for p in (addr.get("addressLocality"), country if isinstance(country, str) else "") if p)


def _page(url: str) -> str:
    """The page's HTML; a 429 is retried once when the site asks to wait a few seconds."""
    for attempt in range(2):
        try:
            data, _, _ = web.fetch_bytes(url, web.MAX_BYTES, accept="text/html", truncate=True)
            return data.decode("utf-8", "replace")
        except web.FetchError as e:
            wait = e.retry_after
            if e.status == 429 and attempt == 0 and wait is not None and wait <= 8:
                time.sleep(max(wait, 1))
                continue
            raise
    raise AssertionError("unreachable")


def read(url: str) -> dict:
    """The job posting at `url`, as text, the best legitimate way available (see the module's docstring)."""
    url = url.strip()
    board = board_link(url)
    if board is None and "gh_jid=" in url and discover_greenhouse(url):  # a Greenhouse job on the company's site
        board = board_link(url)
    if board:
        try:
            return from_board(*board, url)
        except ValueError as e:
            if "404" in str(e):
                raise ValueError("This job is no longer published (the job board says it doesn't exist): it may be "
                                 "closed. If you still have its text, paste it.") from e
            raise
    try:
        page = _page(url)
    except web.FetchError as e:
        if e.status in (401, 403, 429, 999):
            raise ValueError(f"{site_of(url)} refuses automated reading (HTTP {e.status}). Send the job from your own "
                             "browser instead: select the text → Share → Ezri on your phone, the “Send to Ezri” bookmark "
                             "on a computer, or paste the text here.") from e
        raise ValueError(str(e)) from e
    learn_from_html(url, page)
    if "gh_jid" in url and board_link(url):  # the page just taught us its Greenhouse account
        try:
            return from_board(*board_link(url), url)
        except ValueError:
            pass
    ld = job_posting_ld(page)
    if ld and ld.get("description"):
        org = ld.get("hiringOrganization") or {}
        return _result(url, html.unescape(str(ld.get("title", ""))), org.get("name", "") if isinstance(org, dict) else "",
                       _ld_location(ld), _text(str(ld["description"])), "job data embedded in the page")
    result = web.text_of(url, page)
    if len(result["text"]) < 300:
        raise ValueError(f"{site_of(url)} shows its job with JavaScript, which Ezri can't run. Send it from your own "
                         "browser (Share → Ezri, or the “Send to Ezri” bookmark), or paste the text.")
    return {**result, "via": "page text"}
