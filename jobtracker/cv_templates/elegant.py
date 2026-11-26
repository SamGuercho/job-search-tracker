"""Elegant serif template: STIX Two Text by default (or the font chosen on the master CV, see fonts.py), name
and position in small capitals, "•"-separated contact line, then the sections in the master CV's order (default
Summary → Skills (inline) → Professional Experience → Projects → Education → Languages), "Role, Company" headers
with right-aligned dates, black rules under section titles."""
from __future__ import annotations

import math
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..models import DEFAULT_SECTIONS
from ..textstyle import plain_dashes
from .common import bottom_border, dates, grouped, hyperlink, url
from .fonts import DEFAULT_FONT, relative_width, width_pt

FONT = DEFAULT_FONT   # STIX Two Text: ships with macOS, installed in the server image; another can be chosen
INK = RGBColor(0x1A, 0x1A, 0x1A)
LINK = "1155CC"
BODY_PT, HEAD_PT, NAME_PT, TITLE_PT = 10, 11, 20, 15
LINE_SPACING = 1.0   # the original uses 0.9, which clips letters like g/p at paragraph ends in PDF renderers

PAGE_W, PAGE_H = Cm(21.0), Cm(29.7)
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = Cm(0.76), Cm(0.6), Cm(0.5)
USABLE_H_PT = (29.7 - 0.6 - 0.5) / 2.54 * 72
LINE_PT = BODY_PT * 1.17 * LINE_SPACING
USABLE_W_PT = (21.0 - 2 * 0.76) / 2.54 * 72
DATES_GAP_PT = 6              # the least air between a header's text and its right-aligned dates
CHARS_PER_LINE = 128          # full width at 10pt STIX Two Text (other fonts: scaled by their relative width)
BULLET_CHARS_PER_LINE = 120


def _width(font: str, text: str, bold: bool = False, italic: bool = False) -> float:
    """Width in points: measured with the font itself, else estimated from characters (bold letters wider, italic
    ones narrower)."""
    measured = width_pt(text, font, BODY_PT, bold=bold, italic=italic)
    if measured is not None:
        return measured
    return len(text) * (1.1 if bold else 0.95 if italic else 1.0) * relative_width(font) * USABLE_W_PT / CHARS_PER_LINE


def _fits(font: str, when: str, *runs: tuple[str, bool, bool]) -> bool:
    """Do these (text, bold, italic) runs and the right-aligned dates share one line, with DATES_GAP_PT of air?"""
    width = sum(_width(font, text, bold, italic) for text, bold, italic in runs)
    return width + (_width(font, when) + DATES_GAP_PT if when else 0) <= USABLE_W_PT


def entry_layout(title: str, org: str, when: str, font: str = FONT) -> str:
    """How an entry's header is laid out, so the text runs almost up to the dates and they never wrap alone:
    "one"         **Title**, *Organisation* .......... dates
    "title_dates" **Title** .......................... dates  /  *Organisation*
    "org_dates"   **Title** (too long for the dates)  /  *Organisation* ............ dates"""
    if not org or _fits(font, when, (title, True, False), (", ", False, False), (org, False, True)):
        return "one"
    return "title_dates" if _fits(font, when, (title, True, False)) else "org_dates"


def _education_parts(ed) -> tuple[str, str, list[str]]:
    """An education entry's header title, its organisation part, and the detail lines printed under it. Details
    "on the same line" (details_inline) join the organisation part ("School (City) • GPA 92 • Thesis: …")."""
    title = ed.degree or ed.school
    org = (ed.school + (f" ({ed.location})" if ed.location else "")) if ed.degree else ed.location
    details = ed.detail_lines() if hasattr(ed, "detail_lines") else [d for d in ed.details.splitlines() if d.strip()]
    if getattr(ed, "details_inline", False) and details:
        return title, " • ".join([org, *details] if org else details), []
    return title, org, details


def entry_fits(title: str, org: str, when: str, font: str = FONT) -> bool:
    """Do "**Title**, *Organisation*" and the right-aligned dates share one line?"""
    return entry_layout(title, org, when, font) == "one"


class ElegantTemplate:
    name = "elegant"
    label = "Elegant (serif, small caps, inline skills)"

    def render(self, cv, path: Path) -> None:
        doc = Document()
        section = doc.sections[0]
        section.page_width, section.page_height = PAGE_W, PAGE_H
        section.left_margin = section.right_margin = MARGIN_X
        section.top_margin, section.bottom_margin = MARGIN_TOP, MARGIN_BOTTOM
        self.width = PAGE_W - 2 * MARGIN_X

        normal = doc.styles["Normal"]
        self.font = getattr(cv, "font", "") or FONT
        normal.font.name = self.font
        rfonts = normal.element.rPr.rFonts
        for attr in ("w:eastAsia", "w:hAnsi", "w:cs"):
            rfonts.set(qn(attr), self.font)
        normal.font.size = Pt(BODY_PT)
        normal.font.color.rgb = INK
        fmt = normal.paragraph_format
        fmt.space_before = fmt.space_after = Pt(0)
        fmt.line_spacing = LINE_SPACING

        p = cv.profile
        self._run(self._para(doc, center=True), p.name, size=NAME_PT, bold=True, caps=True)
        if p.title:
            self._run(self._para(doc, center=True), p.title, size=TITLE_PT, caps=True)
        contact = self._para(doc, center=True, after=2)
        items = [x for x in (p.phone, p.email, p.location) if x]
        links = [(label, link) for label, link in (("LinkedIn", p.linkedin), ("GitHub", getattr(p, "github", "")),
                                                    ("Website", p.website)) if link]
        for i, text in enumerate(items):
            self._run(contact, (" • " if i else "") + text)
        for i, (label, link) in enumerate(links):
            if items or i:
                self._run(contact, " • ")
            hyperlink(contact, url(link), label, color=LINK)

        self._first = True
        for name in getattr(cv, "sections", None) or DEFAULT_SECTIONS:
            getattr(self, f"_section_{name}")(doc, cv)

        path.parent.mkdir(parents=True, exist_ok=True)
        doc.save(str(path))

    # ------------------------------------------------------------ sections
    # One method per section name (models.CV_SECTIONS); each adds nothing when its section is empty.

    def _section_summary(self, doc, cv):
        if cv.profile.summary:
            self._heading(doc, "Summary")
            self._run(self._para(doc, justify=True), cv.profile.summary)

    def _section_skills(self, doc, cv):
        if any(g.items for g in cv.skills):
            self._heading(doc, "Skills")
            self._run(self._para(doc, justify=True),
                      " • ".join(f"{g.name}: {', '.join(g.items)}" for g in cv.skills if g.items))

    def _section_experience(self, doc, cv):
        if cv.experiences:
            self._heading(doc, "Professional Experience")
            for i, exp in enumerate(cv.experiences):
                org = exp.company + (f" ({exp.location})" if exp.location else "")
                self._entry(doc, exp.role or exp.company, org if exp.role else exp.location,
                            dates(exp.start, exp.end), before=0 if i == 0 else 6)
                self._bullets(doc, exp.bullets)

    def _section_projects(self, doc, cv):
        projects = getattr(cv, "projects", [])
        if projects:
            self._heading(doc, "Projects")
            for i, pr in enumerate(projects):
                self._entry(doc, pr.name, pr.role, dates(pr.start, pr.end), before=0 if i == 0 else 6, link=pr.link)
                self._bullets(doc, pr.bullets)

    def _section_education(self, doc, cv):
        if cv.education:
            self._heading(doc, "Education")
            for i, ed in enumerate(cv.education):
                title, org, below = _education_parts(ed)
                self._entry(doc, title, org, dates(ed.start, ed.end), before=0 if i == 0 else 2)
                for line in below:  # one detail per line
                    self._run(self._para(doc), line)

    def _section_languages(self, doc, cv):
        if cv.languages:
            self._heading(doc, "Languages")
            self._run(self._para(doc), " • ".join(cv.languages))

    def _bullets(self, doc, bullets):
        for group, items in grouped(bullets):
            if group:
                self._run(self._para(doc, before=2, keep=True), group, bold=True, italic=True)
            for b in items:
                bp = doc.add_paragraph(style="List Bullet")
                f = bp.paragraph_format
                f.left_indent, f.first_line_indent = Cm(0.635), Cm(-0.4)  # text at the bullet's built-in tab
                f.line_spacing = LINE_SPACING
                bp.alignment = WD_ALIGN_PARAGRAPH.JUSTIFY
                if b.lead:
                    self._run(bp, b.lead.rstrip() + " ", bold=True)
                self._run(bp, b.text)

    # ------------------------------------------------------------- helpers

    def _para(self, doc, center=False, justify=False, before=0, after=0, keep=False):
        para = doc.add_paragraph()
        para.alignment = WD_ALIGN_PARAGRAPH.CENTER if center else WD_ALIGN_PARAGRAPH.JUSTIFY if justify else None
        f = para.paragraph_format
        f.space_before, f.space_after, f.keep_with_next = Pt(before), Pt(after), keep
        return para

    def _run(self, para, text, size=None, bold=False, italic=False, caps=False):
        run = para.add_run(plain_dashes(text))  # never a long dash in a CV, whoever wrote the text
        if size:
            run.font.size = Pt(size)
        run.bold, run.italic = bold or None, italic or None
        if caps:
            run.font.small_caps = True
        return run

    def _heading(self, doc, text):
        """A section title; the first one on the page sits right under the contact line."""
        para = self._para(doc, before=0 if self._first else 10, after=2, keep=True)
        self._first = False
        self._run(para, text.upper(), size=HEAD_PT, bold=True)
        bottom_border(para, "000000", space=1)

    def _entry(self, doc, title, org, when, before=0, link=""):
        """**Title**, *Organisation*  ................  dates   (a `link` makes the title itself clickable).
        Too long for one line, the organisation goes on its own line, and the dates go on whichever line has room
        for them (entry_layout): never pushed alone to a line of their own with a gap."""
        layout = entry_layout(title, org, when, self.font)
        when = when.replace(" ", "\u00a0")  # "2014 - 2016" never breaks inside
        para = self._para(doc, before=before, keep=True)
        para.paragraph_format.tab_stops.add_tab_stop(self.width, WD_TAB_ALIGNMENT.RIGHT)
        if link:
            hyperlink(para, url(link), plain_dashes(title), color=LINK, bold=True)
        else:
            self._run(para, title, bold=True)
        if org and layout == "one":
            self._run(para, ", ")
            self._run(para, org, italic=True)
        if when and layout != "org_dates":
            self._run(para, f"\t{when}")
        if org and layout != "one":
            line = self._para(doc, keep=True)
            line.paragraph_format.tab_stops.add_tab_stop(self.width, WD_TAB_ALIGNMENT.RIGHT)
            self._run(line, org, italic=True)
            if when and layout == "org_dates":
                self._run(line, f"\t{when}")

    # ----------------------------------------------------------- estimation

    def estimate_pages(self, cv) -> float:
        font = getattr(cv, "font", "") or FONT
        scale = relative_width(font)   # a wider font fits fewer characters per line
        lines = lambda text, cpl: max(1, math.ceil(len(text) * scale / cpl))
        p = cv.profile
        h = NAME_PT * 1.1 + (TITLE_PT * 1.1 if p.title else 0) + LINE_PT + 2
        heading = 10 + HEAD_PT * 1.1 + 2 + 2
        bullets = lambda items: sum(((2 + LINE_PT) if group else 0)
                                    + sum(lines(f"{b.lead} {b.text}", BULLET_CHARS_PER_LINE) * LINE_PT for b in bs)
                                    for group, bs in grouped(items))
        if p.summary:
            h += heading + lines(p.summary, CHARS_PER_LINE) * LINE_PT
        if any(g.items for g in cv.skills):
            h += heading + lines(" • ".join(f"{g.name}: {', '.join(g.items)}" for g in cv.skills), CHARS_PER_LINE) * LINE_PT
        head = lambda title, org, when: LINE_PT * (1 if entry_fits(title, org, when, font) else 2)
        exp_head = lambda e: head(e.role or e.company, e.company if e.role else e.location, dates(e.start, e.end))
        pr_head = lambda pr: head(pr.name, pr.role, dates(pr.start, pr.end))
        for entries, entry_head in ((cv.experiences, exp_head), (getattr(cv, "projects", []), pr_head)):
            if entries:
                h += heading + sum((0 if i == 0 else 6) + entry_head(e) + bullets(e.bullets) for i, e in enumerate(entries))
        if cv.education:
            h += heading + sum(head(*_education_parts(ed)[:2], dates(ed.start, ed.end)) + 2
                               + sum(lines(line, CHARS_PER_LINE) * LINE_PT for line in _education_parts(ed)[2])
                               for ed in cv.education)
        if cv.languages:
            h += heading + LINE_PT
        return h / USABLE_H_PT
