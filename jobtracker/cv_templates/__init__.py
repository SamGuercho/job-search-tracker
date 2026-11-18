"""CV templates registry.

A template is any object with:
  - `name` / `label`
  - `render(cv: TailoredCV, path: Path) -> None`   writes a .docx
  - `estimate_pages(cv: TailoredCV) -> float`      rough page usage (1.0 = full page)

To add a template, create a module here (see elegant.py) and add it to TEMPLATES.
The CV builder shows a template picker only when there is more than one.
"""
from .elegant import ElegantTemplate

TEMPLATES = {t.name: t for t in [ElegantTemplate()]}
DEFAULT_TEMPLATE = "elegant"


def get_template(name: str | None):
    try:
        return TEMPLATES[name or DEFAULT_TEMPLATE]
    except KeyError:
        raise ValueError(f"Unknown template {name!r}. Available: {', '.join(TEMPLATES)}") from None
