"""Tiny helpers shared by the model modules (kept separate to avoid import cycles)."""
import uuid
from datetime import date


def new_id() -> str:
    return uuid.uuid4().hex[:10]


def today() -> str:
    return date.today().isoformat()
