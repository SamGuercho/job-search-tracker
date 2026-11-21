"""Company logos, found on the company's own website (no third-party logo service: it would learn which
companies the user looks at). No AI involved.

find(): the home page is read once and every logo it declares is a candidate, best first:
  1. the phone icon (apple-touch-icon): usually a clean, square, sharp logo
  2. declared icons with a size of 96 px or more, then the icons of the web app manifest
  3. an <img> named "logo" (often a wide wordmark: kept only if nothing square is found)
  4. other declared icons (an SVG icon scales well), then /apple-touch-icon.png and /favicon.ico
The first good candidates are downloaded (6 at most), checked to really be images, and the best kept as
<company folder>/logo.png (or logo.svg). An SVG is only ever shown as an image, and served with a policy that
forbids scripts (api.py), so a malicious file can't run code.

When a website is set or changed, the search runs in the background (JOBTRACKER_LOGOS=0 turns that off).
A logo the user uploaded is never replaced automatically.
"""
from __future__ import annotations

import io
import json
import os
import re
import threading
import urllib.parse
from datetime import datetime
from html.parser import HTMLParser

from .. import users
from ..logs import log
from . import companies, files, web

MAX_IMAGE = 1_500_000
MAX_DOWNLOADS = 6
GOOD_SIZE = 96      # a square logo at least this big is good enough: stop looking
KEEP = 256          # saved size (longest side)


class _Candidates(HTMLParser):
    def __init__(self):
        super().__init__()
        self.links, self.images, self.manifest = [], [], None

    def handle_starttag(self, tag, attrs):
        a = {k.lower(): (v or "") for k, v in attrs}
        if tag == "link" and a.get("href"):
            rel = a.get("rel", "").lower()
            if "manifest" in rel:
                self.manifest = a["href"]
            elif "icon" in rel:
                self.links.append({"href": a["href"], "rel": rel, "sizes": a.get("sizes", ""), "type": a.get("type", "")})
        elif tag == "img":
            src = a.get("src") or a.get("data-src") or ""
            hint = " ".join([src, a.get("alt", ""), a.get("class", ""), a.get("id", "")]).lower()
            if src and "logo" in hint and not src.startswith("data:"):
                self.images.append(src)


def _size(sizes: str) -> int:
    found = [int(w) for w, _ in re.findall(r"(\d+)x(\d+)", sizes or "")]
    return max(found, default=0)


def candidates(page_url: str, html: str) -> list[tuple[int, str]]:
    """(priority, absolute URL), best first."""
    p = _Candidates()
    p.feed(html)
    out = []
    for link in p.links:
        size, rel, href = _size(link["sizes"]), link["rel"], link["href"]
        svg = "svg" in link["type"] or href.lower().split("?")[0].endswith(".svg")
        if "apple-touch-icon" in rel:
            out.append((2000 + size, href))  # always first: a square logo made for home screens
        elif size >= GOOD_SIZE:
            out.append((800 + size, href))
        else:
            out.append((400 + (150 if svg else 0) + size, href))
    if p.manifest:
        try:
            data, _, final = web.fetch_bytes(urllib.parse.urljoin(page_url, p.manifest), 200_000)
            for icon in json.loads(data.decode("utf-8", "replace")).get("icons", []):
                if icon.get("src"):
                    out.append((700 + _size(icon.get("sizes", "")), urllib.parse.urljoin(final, icon["src"])))
        except Exception:
            pass
    out += [(600, src) for src in p.images[:3]]
    root = "{0.scheme}://{0.netloc}".format(urllib.parse.urlsplit(page_url))
    out += [(300, f"{root}/apple-touch-icon.png"), (200, f"{root}/favicon.ico")]
    seen, ordered = set(), []
    for priority, href in sorted(out, key=lambda c: -c[0]):
        url = urllib.parse.urljoin(page_url, href.strip())
        if url.startswith(("http://", "https://")) and url not in seen:
            seen.add(url)
            ordered.append((priority, url))
    return ordered


# ----------------------------------------------------------------- images

def _is_svg(data: bytes, content_type: str) -> bool:
    head = data[:500].lstrip().lower()
    xml_svg = head.startswith(b"<?xml") and b"<svg" in data[:2000].lower()
    return "svg" in content_type or head.startswith(b"<svg") or xml_svg


def load(data: bytes, content_type: str = "") -> tuple[str, bytes, int, float]:
    """Check an image and normalise it: (".png" or ".svg", bytes to save, smallest side in px, aspect ratio).
    Raster images (PNG, JPEG, ICO, WebP, GIF…) become a PNG of at most KEEP px; SVG is kept as is."""
    if _is_svg(data, content_type):
        if b"<svg" not in data.lower():
            raise ValueError("Not an image")
        return ".svg", data, 512, 1.0   # vector: any size is sharp
    from PIL import Image

    try:
        img = Image.open(io.BytesIO(data))
        if getattr(img, "format", "") == "ICO":  # several sizes inside: take the largest
            img.size = max(img.info.get("sizes", [img.size]))
        img.load()
    except Exception as e:
        raise ValueError("Not an image") from e
    img = img.convert("RGBA")
    w, h = img.size
    if min(w, h) < 16:
        raise ValueError("Too small")
    img.thumbnail((KEEP, KEEP))
    out = io.BytesIO()
    img.save(out, format="PNG", optimize=True)
    return ".png", out.getvalue(), min(w, h), max(w, h) / min(w, h)


# ------------------------------------------------------------------- search

def find(website: str) -> dict:
    """The best logo of a website: {"ext", "data", "source", "size"}. Raises ValueError if none is usable."""
    url = website.strip()
    if not re.match(r"^https?://", url, re.I):
        url = "https://" + url
    html, _, final = web.fetch_bytes(url, 1_000_000, accept="text/html", truncate=True)  # icons are in <head>
    refresh = re.search(rb"""<meta[^>]+http-equiv=["']?refresh[^>]+url=([^"'>\s]+)""", html[:5000], re.I)
    if refresh:  # a redirect page: follow it once (JavaScript redirects aren't: often parked domains)
        html, _, final = web.fetch_bytes(urllib.parse.urljoin(final, refresh.group(1).decode()), 1_000_000,
                                         accept="text/html", truncate=True)
    best, tried = None, 0
    for priority, candidate in candidates(final, html.decode("utf-8", "replace")):
        if tried >= MAX_DOWNLOADS:
            break
        tried += 1
        try:
            data, ctype, _ = web.fetch_bytes(candidate, MAX_IMAGE, accept="image/*")
            ext, content, size, aspect = load(data, ctype)
        except Exception:
            continue
        square = aspect <= 2.0
        score = (square, size if size < 512 else 512, priority)
        if best is None or score > best[0]:
            best = (score, {"ext": ext, "data": content, "source": candidate, "size": size})
        if square and size >= GOOD_SIZE:
            break
    if best is None:
        raise ValueError(f"No usable logo found on {website}")
    return best[1]


def _save(company, ext: str, data: bytes, source: str):
    folder = files.output_folder(company.name)
    folder.mkdir(parents=True, exist_ok=True)
    for old in folder.glob("logo.*"):
        old.unlink()
    path = folder / f"logo{ext}"
    path.write_bytes(data)
    return companies.update_company(company.id, logo_file=files.relative(path), logo_source=source,
                                    logo_checked_at=datetime.now().isoformat(timespec="seconds"))


# ------------------------------------------------------------ website guess

JOB_SITES = ("linkedin.", "indeed.", "glassdoor.", "greenhouse.io", "lever.co", "workday", "myworkdayjobs", "comeet.",
             "smartrecruiters.", "ashbyhq.", "bamboohr.", "jobvite.", "breezy.hr", "recruitee.", "teamtailor.", "welcome",
             "drushim.", "alljobs.", "jobmaster.", "wellfound.", "angel.co", "otta.", "builtin.", "google.")
MAIL_SITES = ("gmail.", "outlook.", "hotmail.", "yahoo.", "icloud.", "proton", "live.", "walla.", "aol.")
TLDS = (".com", ".io", ".ai", ".co", ".co.il", ".tech", ".dev")


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]", "", text.lower())


def _root(url: str) -> str:
    parts = urllib.parse.urlsplit(url if "://" in url else "https://" + url)
    return f"{parts.scheme or 'https'}://{parts.hostname}" if parts.hostname else ""


def _belongs_to(company_name: str, url: str) -> bool:
    """The page at `url` is the company's own site: a real page whose title (or site name) contains the company's
    name, and not a parked domain (parked pages are tiny, or titled with the bare domain)."""
    try:
        html, _, final = web.fetch_bytes(url, 400_000, accept="text/html", truncate=True)
    except Exception:
        return False
    text = html.decode("utf-8", "replace")
    if len(text) < 2_000 or re.search(r"(domain|this site) (is )?for sale|parked|lander", text[:5_000], re.I):
        return False
    titles = re.findall(r"<title[^>]*>(.*?)</title>", text, re.I | re.S)
    titles += re.findall(r'<meta[^>]+(?:og:site_name|application-name)[^>]+content=["\']([^"\']+)', text, re.I)
    name, host = _norm(company_name), _norm(urllib.parse.urlsplit(final).hostname or "")
    return bool(name) and any(name in _norm(t) and _norm(t) not in (host, "www" + host) for t in titles)


def guess_website(company) -> str | None:
    """The company's own website when none is saved: the job posting's site (if it isn't a job board), the contact's
    email domain (if it isn't a mailbox provider), then <name>.com/.io/.ai/… Each is kept only if verified."""
    tried = []
    if company.job_url and not any(s in company.job_url.lower() for s in JOB_SITES):
        tried.append(_root(company.job_url))
    email = (company.contact.email or "").lower()
    if "@" in email and not any(s in email.split("@")[1] for s in MAIL_SITES):
        tried.append(_root(email.split("@")[1]))
    slug = _norm(company.name)
    if slug:
        tried += [f"https://www.{slug}{tld}" for tld in TLDS]
    for url in dict.fromkeys(u for u in tried if u):
        if _belongs_to(company.name, url):
            return url
    return None


def refresh(ref: str) -> dict:
    """Look for the logo on the company's website now (replaces the current one, even an uploaded one). With no
    website saved, the website is guessed first (and saved when verified)."""
    c = companies.resolve(ref)
    guessed = None
    if not c.website:
        guessed = guess_website(c)
        if not guessed:
            raise ValueError(f"{c.name} has no website and I couldn't find it: add it in the application's details")
        c = companies.update_company(c.id, fetch_logo=False, website=guessed)
    found = find(c.website)
    c = _save(c, found["ext"], found["data"], found["source"])
    return {"logo_file": c.logo_file, "source": found["source"], "size": found["size"],
            **({"website_found": guessed} if guessed else {})}


def upload(ref: str, filename: str, content: bytes) -> dict:
    """Use the user's own image as the logo (never replaced automatically afterwards)."""
    c = companies.resolve(ref)
    if len(content) > 5_000_000:
        raise ValueError("Image too large (max 5 MB)")
    ext, data, _, _ = load(content, "image/svg+xml" if filename.lower().endswith(".svg") else "")
    c = _save(c, ext, data, "uploaded")
    return {"logo_file": c.logo_file, "source": "uploaded"}


def remove(ref: str) -> dict:
    """Show the initials instead (the website isn't searched again automatically)."""
    c = companies.resolve(ref)
    if c.logo_file:
        (users.output_dir() / c.logo_file).unlink(missing_ok=True)
    companies.update_company(c.id, logo_file=None, logo_source="removed",
                             logo_checked_at=datetime.now().isoformat(timespec="seconds"))
    return {"logo_file": None}


def schedule(company_id: str) -> None:
    """Search in the background after a website was set (not if the user uploaded or removed the logo)."""
    if os.environ.get("JOBTRACKER_LOGOS", "1") == "0":
        return
    user = users.current()

    def run():
        with users.acting_as(user):
            try:
                c = companies.get_company(company_id)
                if c.website and c.logo_source not in ("uploaded", "removed"):
                    refresh(company_id)
            except Exception as e:  # a logo is a nice-to-have: never fail loudly
                log.info("No logo for %s: %s", company_id, e)
    threading.Thread(target=run, daemon=True, name=f"logo-{company_id}").start()
