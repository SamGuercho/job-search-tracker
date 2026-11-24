"""The data an eval case starts from: a person's tracker files, exported by the monitor and written into a throwaway
data folder when the case runs. Chats, usage and push subscriptions are left out (not needed, or secret)."""
from __future__ import annotations

import json
from pathlib import Path

from .. import config, users

FIXTURE_FILES = ("companies.json", "master_cv.json", "profile.json", "preps.json", "practice.json", "recordings.json",
                 "learning.json", "linkedin.json")


def data_folder(login: str) -> Path:
    """The data folder of the person with this Tailscale login (the owner's: the data folder itself)."""
    if not login or not users.enabled():
        return config.DATA_DIR
    with users.acting_as(users.resolve(login)):
        return users.data_dir()


def export(login: str) -> dict:
    folder = data_folder(login)
    out = {}
    for name in FIXTURE_FILES:
        path = folder / name
        if path.exists():
            out[name] = json.loads(path.read_text(encoding="utf-8"))
    return out


def write(fixture: dict, folder: Path) -> None:
    folder.mkdir(parents=True, exist_ok=True)
    for name, content in fixture.items():
        if name in FIXTURE_FILES:  # never anything else (a case file is data, not a path list)
            (folder / name).write_text(json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8")
