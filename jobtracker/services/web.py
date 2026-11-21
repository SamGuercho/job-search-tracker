"""Fetch a job posting page and keep its readable text."""
from __future__ import annotations

import ipaddress
import re
import socket
import ssl
import urllib.error
import urllib.parse
import urllib.request
from html.parser import HTMLParser

import certifi  # python.org builds on macOS ship without root certificates

MAX_BYTES = 2_000_000
MAX_CHARS = 20_000
SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article", "ul", "ol"}


class FetchError(ValueError):
    """A page or file that couldn't be fetched; `status` is the HTTP status when the site answered."""

    def __init__(self, message: str, status: int | None = None, retry_after: float | None = None):
        super().__init__(message)
        self.status, self.retry_after = status, retry_after


def check_public(url: str) -> None:
    """Refuse links to this machine or the private network: fetching e.g. http://127.0.0.1:8000/files/...
    from the server would bypass the per-user login (a request without Tailscale identity is the owner)."""
    if not re.match(r"^https?://", url, re.I):
        raise ValueError("Only http(s) links are supported")
    host = urllib.parse.urlsplit(url).hostname or ""
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, None)}
    except OSError as e:
        raise ValueError(f"Could not open {url}: {e}") from e
    for address in addresses:
        ip = ipaddress.ip_address(address.split("%", 1)[0])
        if not ip.is_global or ip in ipaddress.ip_network("100.64.0.0/10"):  # incl. Tailscale addresses
            raise ValueError(f"Not a public web page: {url}")


class _PublicRedirects(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        check_public(newurl)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class _Text(HTMLParser):
    def __init__(self):
        super().__init__()
        self.parts, self.skip = [], 0

    def handle_starttag(self, tag, attrs):
        if tag in SKIP:
            self.skip += 1
        elif tag in BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        if tag in SKIP and self.skip:
            self.skip -= 1

    def handle_data(self, data):
        if not self.skip:
            self.parts.append(data)


def fetch_text(url: str) -> dict:
    data, _, _ = fetch_bytes(url, MAX_BYTES, accept="text/html", truncate=True)
    return text_of(url, data.decode("utf-8", "replace"))


def text_of(url: str, raw: str) -> dict:
    """The readable text and title of an HTML page."""
    parser = _Text()
    parser.feed(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    return {"url": url, "title": title.group(1).strip() if title else "", "text": text[:MAX_CHARS],
            "truncated": len(text) > MAX_CHARS}


def fetch_bytes(url: str, max_bytes: int = MAX_BYTES, accept: str = "*/*", truncate: bool = False) -> tuple[bytes, str, str]:
    """(content, content type, final URL) of a public URL, at most `max_bytes` (same safety checks as pages).
    Bigger: an error, or with `truncate` just the beginning (enough for a page's <head>)."""
    check_public(url)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-search-tracker)", "Accept": accept})
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        opener = urllib.request.build_opener(urllib.request.HTTPSHandler(context=context), _PublicRedirects())
        with opener.open(req, timeout=15) as resp:
            data = resp.read(max_bytes + 1)
            if len(data) > max_bytes:
                if not truncate:
                    raise FetchError(f"Too large: {url}")
                data = data[:max_bytes]
            return data, (resp.headers.get_content_type() or ""), resp.geturl()
    except urllib.error.HTTPError as e:
        retry = e.headers.get("Retry-After", "") if e.headers else ""
        raise FetchError(f"Could not open {url}: HTTP Error {e.code}: {e.reason}", e.code,
                         float(retry) if retry.strip().isdigit() else None) from e
    except OSError as e:
        raise FetchError(f"Could not open {url}: {e}") from e
