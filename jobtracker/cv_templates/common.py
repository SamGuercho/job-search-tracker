"""Helpers shared by the Word templates."""
from __future__ import annotations

from docx.opc.constants import RELATIONSHIP_TYPE
from docx.oxml import OxmlElement
from docx.oxml.ns import qn


def dates(start: str, end: str) -> str:
    if start and start == end:  # "2016 – 2016" -> "2016"
        return start
    return " – ".join(p for p in (start, end) if p)


def grouped(bullets):
    """[(group, [bullets])] keeping first-appearance order; "" = no sub-heading."""
    groups: dict[str, list] = {}
    for b in bullets:
        groups.setdefault(b.group, []).append(b)
    return list(groups.items())


def bottom_border(paragraph, color: str = "1F3A5F", space: int = 1) -> None:
    borders = OxmlElement("w:pBdr")
    bottom = OxmlElement("w:bottom")
    for k, v in {"w:val": "single", "w:sz": "6", "w:space": str(space), "w:color": color}.items():
        bottom.set(qn(k), v)
    borders.append(bottom)
    paragraph._p.get_or_add_pPr().append(borders)


def hyperlink(paragraph, url: str, text: str, color: str = "0563C1"):
    """Append a clickable, underlined link run to a paragraph."""
    r_id = paragraph.part.relate_to(url, RELATIONSHIP_TYPE.HYPERLINK, is_external=True)
    link = OxmlElement("w:hyperlink")
    link.set(qn("r:id"), r_id)
    run = OxmlElement("w:r")
    props = OxmlElement("w:rPr")
    for tag, val in (("w:color", color), ("w:u", "single")):
        el = OxmlElement(tag)
        el.set(qn("w:val"), val)
        props.append(el)
    run.append(props)
    t = OxmlElement("w:t")
    t.text = text
    run.append(t)
    link.append(run)
    paragraph._p.append(link)


def url(value: str) -> str:
    return value if value.startswith("http") else f"https://{value}"
