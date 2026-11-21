"""Reading a job posting from a link, the legitimate ways (never by pretending to be a browser).

1. Job boards' official public APIs, when the link says which board and account (or when this company's site is
   remembered to use one):
   - Greenhouse  boards(-api).greenhouse.io / job-boards(.eu).greenhouse.io / company site with ?gh_jid=
   - Lever       jobs(.eu).lever.co/<company>/<id>
   - Ashby       jobs.ashbyhq.com/<org>/<id>
   - SmartRecruiters  jobs.smartrecruiters.com/<company>/<id>-…
2. The page itself: the job data embedded for search engines (schema.org JobPosting, JSON-LD) if present,
   else the readable text. A 429 is retried once if the site says to wait a few seconds.
When a page loads, the job board it embeds (e.g. Greenhouse "for=sentinellabs") is remembered for its site, so
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


def _boards() -> JsonDocument:
    return JsonDocument(config.DATA_DIR / "job_boards.json", BoardBook)


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
