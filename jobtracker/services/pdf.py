"""DOCX -> PDF conversion and page counting.

Tries LibreOffice first (cross-platform, headless), then Microsoft Word via
docx2pdf (macOS/Windows). If neither is available, PDF export is skipped and
the one-page rule relies on the template's height estimate.

Set JOBTRACKER_PDF_ENGINE=libreoffice|word|none to force a choice. An engine
that fails is skipped for the rest of the process, so one stuck Word dialog
doesn't slow down every generation (restart the server to retry it).
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from pypdf import PdfReader

TIMEOUT_S = 90
_failed: set[str] = set()


class ConversionError(RuntimeError):
    pass


def _soffice() -> str | None:
    for candidate in ("soffice", "libreoffice", "/Applications/LibreOffice.app/Contents/MacOS/soffice"):
        path = shutil.which(candidate) or (candidate if Path(candidate).exists() else None)
        if path:
            return path
    return None


def _word_available() -> bool:
    if sys.platform == "darwin":
        return Path("/Applications/Microsoft Word.app").exists()
    return sys.platform == "win32"


def available_engine() -> str | None:
    forced = os.environ.get("JOBTRACKER_PDF_ENGINE")
    if forced:
        return None if forced == "none" or forced in _failed else forced
    if _soffice() and "libreoffice" not in _failed:
        return "libreoffice"
    if _word_available() and "word" not in _failed:
        return "word"
    return None


def convert(docx_path: Path, pdf_path: Path) -> Path:
    engine = available_engine()
    if engine == "libreoffice":
        cmd = [_soffice(), "--headless", "--convert-to", "pdf", "--outdir", str(pdf_path.parent), str(docx_path)]
        produced = pdf_path.parent / (docx_path.stem + ".pdf")
    elif engine == "word":
        # Run in a subprocess so a stuck Word dialog can't hang the server.
        cmd = [sys.executable, "-c", "import sys, docx2pdf; docx2pdf.convert(sys.argv[1], sys.argv[2])",
               str(docx_path), str(pdf_path)]
        produced = pdf_path
    else:
        raise ConversionError("No PDF engine found (install LibreOffice or Microsoft Word).")

    produced.unlink(missing_ok=True)  # never mistake a stale file for a success
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired as e:
        _failed.add(engine)
        raise ConversionError(f"PDF conversion with {engine} timed out (check for a permission dialog)") from e
    if not produced.exists():
        _failed.add(engine)
        raise ConversionError(f"PDF conversion with {engine} failed: {result.stderr.strip() or result.stdout.strip()}")
    if produced != pdf_path:
        produced.replace(pdf_path)
    return pdf_path


def page_count(pdf_path: Path) -> int:
    return len(PdfReader(str(pdf_path)).pages)
