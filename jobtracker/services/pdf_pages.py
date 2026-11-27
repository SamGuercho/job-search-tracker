"""A PDF's pages as images, for the in-app file viewer on iPhone.

An app opened from the iPhone home screen has no browser bar: a PDF opened in its window has no way back, and a
PDF placed inside a page shows only its first page. So the viewer shows each page as an image the server renders
(PDFium, through pypdfium2). PDFium isn't thread-safe: one render at a time.
"""
from __future__ import annotations

import io
import threading
from functools import lru_cache
from pathlib import Path

import pypdfium2 as pdfium

MAX_WIDTH = 1600   # pixels: sharp on a phone's screen (about 1200 wide), small enough to send quickly
_lock = threading.Lock()


def count(path: Path) -> int:
    with _lock:
        doc = pdfium.PdfDocument(str(path))
        try:
            return len(doc)
        finally:
            doc.close()


def render(path: Path, page: int, width: int = 1200) -> bytes:
    """Page `page` (from 1) as a PNG `width` pixels wide. ValueError when there is no such page."""
    return _render(str(path), path.stat().st_mtime_ns, page, max(200, min(width, MAX_WIDTH)))


@lru_cache(maxsize=24)  # a CV opened again (or scrolled back to) isn't rendered twice; a changed file is (mtime)
def _render(path: str, _mtime: int, page: int, width: int) -> bytes:
    with _lock:
        doc = pdfium.PdfDocument(path)
        try:
            if not 1 <= page <= len(doc):
                raise ValueError(f"No page {page}: the PDF has {len(doc)}.")
            p = doc[page - 1]
            image = p.render(scale=width / p.get_width()).to_pil()
            p.close()
        finally:
            doc.close()
    out = io.BytesIO()
    image.save(out, "PNG", optimize=True)
    return out.getvalue()
