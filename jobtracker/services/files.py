"""Where an application's files live: <output folder>/<Company>/ (CVs, job description)."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

from .. import users


def safe_name(name: str, sep: str = "_") -> str:
    return sep.join(re.sub(r"[^\w\-&. ]+", " ", name).split()) or "Unknown"


def output_folder(company_name: str) -> Path:
    """One folder per company, named like the company ("Sensi AI")."""
    folder = re.sub(r'[/\\:*?"<>|]+', " ", company_name)
    return users.output_dir() / (" ".join(folder.split()).strip(" .") or "Unknown")


def archive_previous(*paths: Path) -> None:
    """Never overwrite a file silently: move existing ones to <folder>/archive/ with a timestamp."""
    stamp = datetime.now().strftime("%Y-%m-%d_%H%M%S")
    for path in paths:
        if path.exists():
            archive = path.parent / "archive"
            archive.mkdir(exist_ok=True)
            path.rename(archive / f"{path.stem}_{stamp}{path.suffix}")


def relative(path: Path) -> str:
    return path.relative_to(users.output_dir()).as_posix()
