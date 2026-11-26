"""CV fonts: which ones can be chosen, and how wide a text is in them.

A font is offered only when it is installed where the CV is turned into a PDF (this machine, or the server's image):
otherwise Word/LibreOffice would silently use another one. Widths are measured with the real font files (Pillow), so
the template knows whether a line fits; without the files, callers fall back to character counts.
"""
from __future__ import annotations

import subprocess
from functools import lru_cache
from pathlib import Path

DEFAULT_FONT = "STIX Two Text"
# Fonts that suit a CV, in the order offered; only installed ones are listed (see available()).
CHOICES = [
    ("STIX Two Text", "serif"), ("Georgia", "serif"), ("Charter", "serif"), ("Palatino", "serif"),
    ("Baskerville", "serif"), ("EB Garamond", "serif"), ("Garamond", "serif"), ("Cambria", "serif"),
    ("Times New Roman", "serif"), ("Helvetica Neue", "sans-serif"), ("Avenir Next", "sans-serif"),
    ("Calibri", "sans-serif"), ("Lato", "sans-serif"), ("Arial", "sans-serif"),
]
_FONT_DIRS = [Path("/System/Library/Fonts"), Path("/Library/Fonts"), Path.home() / "Library/Fonts",
              Path("/usr/share/fonts"), Path("/usr/local/share/fonts"), Path.home() / ".fonts",
              Path("C:/Windows/Fonts")]
_EXTENSIONS = {".ttf", ".otf", ".ttc"}


def _style_key(style: str) -> str | None:
    s = style.lower().replace("oblique", "italic")
    words = set(s.replace("-", " ").split())
    if words & {"semibold", "medium", "light", "black", "heavy", "thin", "condensed", "narrow", "demi", "extrabold"}:
        return None
    bold, italic = "bold" in words, "italic" in words
    if not (bold or italic) and not words <= {"regular", "roman", "book", "normal", "plain", ""}:
        return None
    return ("bold" if bold else "") + ("italic" if italic else "") or "regular"


@lru_cache(maxsize=1)
def _faces() -> dict[str, dict[str, tuple[str, int, str]]]:
    """family (lower case) -> {regular|bold|italic|bolditalic: (file, face index, style name)}."""
    out: dict[str, dict[str, tuple[str, int, str]]] = {}

    def add(family: str, style: str, path: str, index: int) -> None:
        key = _style_key(style)
        if key:
            out.setdefault(family.strip().lower(), {}).setdefault(key, (path, index & 0xFFFF, style))

    try:
        listing = subprocess.run(["fc-list", "-f", "%{file}|%{index}|%{family}|%{style}\n"], capture_output=True,
                                 text=True, timeout=10).stdout
        for line in listing.splitlines():
            path, index, families, styles = (line.split("|") + ["", "", "", ""])[:4]
            if families and styles:
                add(families.split(",")[0], styles.split(",")[0], path, int(index or 0))
    except (OSError, subprocess.SubprocessError, ValueError):
        pass
    if not out:  # no fontconfig: read the font files themselves
        from PIL import ImageFont
        for folder in _FONT_DIRS:
            for f in (folder.rglob("*") if folder.is_dir() else []):
                if f.suffix.lower() not in _EXTENSIONS:
                    continue
                for index in range(16):
                    try:
                        family, style = ImageFont.truetype(str(f), 10, index=index).getname()
                    except OSError:
                        break
                    if family and style:
                        add(family, style, str(f), index)
    return out


def installed(font: str) -> bool:
    return "regular" in _faces().get(font.lower(), {})


def available() -> list[dict]:
    """The fonts that can be chosen here: [{name, kind}] (the default one always)."""
    return [{"name": name, "kind": kind} for name, kind in CHOICES if name == DEFAULT_FONT or installed(name)]


@lru_cache(maxsize=64)
def _font(font: str, key: str):
    from PIL import ImageFont
    styles = _faces().get(font.lower(), {})
    face = styles.get(key) or styles.get("bold" if "bold" in key else "regular")
    if face is None:
        return None
    path, index, style = face
    try:
        f = ImageFont.truetype(path, 1000, index=index)
    except OSError:
        return None
    try:  # a variable font (STIX Two Text): pick the named weight
        if f.get_variation_names():
            f.set_variation_by_name(style)
    except (OSError, ValueError):
        pass
    return f


def width_pt(text: str, font: str, size_pt: float, bold: bool = False, italic: bool = False) -> float | None:
    """Width of `text` in points, or None when the font files aren't here."""
    f = _font(font, ("bold" if bold else "") + ("italic" if italic else "") or "regular")
    return None if f is None else f.getlength(text) * size_pt / 1000


_SAMPLE = "Built a churn prediction model in Python that identified 80% of at-risk accounts, saving 5 days a month."


@lru_cache(maxsize=32)
def relative_width(font: str) -> float:
    """How much wider `font` sets text than the default font (1.0 = as wide); 1.0 when unknown."""
    a, b = width_pt(_SAMPLE, font, 10), width_pt(_SAMPLE, DEFAULT_FONT, 10)
    return a / b if a and b else 1.0
