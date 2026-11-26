"""Elegant serif template: STIX Two Text, name and position in small
capitals, "•"-separated contact line, then the sections in the master CV's order (default Summary → Skills (inline)
→ Professional Experience → Projects → Education → Languages), "Role, Company" headers with right-aligned dates,
black rules under section titles."""
from __future__ import annotations

import math
from pathlib import Path

from docx import Document
from docx.enum.text import WD_ALIGN_PARAGRAPH, WD_TAB_ALIGNMENT
from docx.oxml.ns import qn
from docx.shared import Cm, Pt, RGBColor

from ..models import DEFAULT_SECTIONS
from ..textstyle import plain_dashes
from .common import bottom_border, dates, grouped, hyperlink, link_label, url

FONT = "STIX Two Text"   # ships with macOS; Word/LibreOffice fall back to a serif elsewhere
INK = RGBColor(0x1A, 0x1A, 0x1A)
LINK = "1155CC"
BODY_PT, HEAD_PT, NAME_PT, TITLE_PT = 10, 11, 20, 15
LINE_SPACING = 1.0   # the original uses 0.9, which clips letters like g/p at paragraph ends in PDF renderers

PAGE_W, PAGE_H = Cm(21.0), Cm(29.7)
MARGIN_X, MARGIN_TOP, MARGIN_BOTTOM = Cm(0.76), Cm(0.6), Cm(0.5)
USABLE_H_PT = (29.7 - 0.6 - 0.5) / 2.54 * 72
LINE_PT = BODY_PT * 1.17 * LINE_SPACING
CHARS_PER_LINE = 128          # full width at 10pt STIX Two Text
BULLET_CHARS_PER_LINE = 120


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
        normal.font.name = FONT
        rfonts = normal.element.rPr.rFonts
        for attr in ("w:eastAsia", "w:hAnsi", "w:cs"):
            rfonts.set(qn(attr), FONT)
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
                org = ed.school + (f" ({ed.location})" if ed.location else "")
                self._entry(doc, ed.degree or ed.school, org if ed.degree else ed.location,
                            dates(ed.start, ed.end), before=0 if i == 0 else 2)
                if ed.details:
                    self._run(self._para(doc), ed.details)

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
        """**Title**, *Organisation* • link  ................  dates"""
        para = self._para(doc, before=before, keep=True)
        para.paragraph_format.tab_stops.add_tab_stop(self.width, WD_TAB_ALIGNMENT.RIGHT)
        self._run(para, title, bold=True)
        if org:
            self._run(para, ", ")
            self._run(para, org, italic=True)
        if link:
            self._run(para, " • ")
            hyperlink(para, url(link), link_label(link), color=LINK)
        if when:
            self._run(para, f"\t{when}")

    # ----------------------------------------------------------- estimation

    def estimate_pages(self, cv) -> float:
        lines = lambda text, cpl: max(1, math.ceil(len(text) / cpl))
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
        for entries in (cv.experiences, getattr(cv, "projects", [])):
            if entries:
                h += heading + sum((0 if i == 0 else 6) + LINE_PT + bullets(e.bullets) for i, e in enumerate(entries))
        if cv.education:
            h += heading + sum(LINE_PT + 2 + (lines(ed.details, CHARS_PER_LINE) * LINE_PT if ed.details else 0)
                               for ed in cv.education)
        if cv.languages:
            h += heading + LINE_PT
        return h / USABLE_H_PT
