"""Fetch a job posting page and keep its readable text."""
from __future__ import annotations

import re
import ssl
import urllib.request
from html.parser import HTMLParser

import certifi  # python.org builds on macOS ship without root certificates

MAX_BYTES = 2_000_000
MAX_CHARS = 20_000
SKIP = {"script", "style", "noscript", "svg", "head", "nav", "footer", "form"}
BLOCK = {"p", "div", "li", "br", "h1", "h2", "h3", "h4", "tr", "section", "article", "ul", "ol"}


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
    if not re.match(r"^https?://", url):
        raise ValueError("Only http(s) links are supported")
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (job-search-tracker)"})
    try:
        context = ssl.create_default_context(cafile=certifi.where())
        with urllib.request.urlopen(req, timeout=20, context=context) as resp:
            raw = resp.read(MAX_BYTES).decode(resp.headers.get_content_charset() or "utf-8", errors="replace")
    except OSError as e:
        raise ValueError(f"Could not open {url}: {e}") from e
    parser = _Text()
    parser.feed(raw)
    text = re.sub(r"[ \t\r\f\v]+", " ", "".join(parser.parts))
    text = "\n".join(line.strip() for line in text.splitlines() if line.strip())
    title = re.search(r"<title[^>]*>(.*?)</title>", raw, re.S | re.I)
    return {"url": url, "title": title.group(1).strip() if title else "", "text": text[:MAX_CHARS],
            "truncated": len(text) > MAX_CHARS}
