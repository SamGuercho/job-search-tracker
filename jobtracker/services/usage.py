"""Usage and costs: every AI call is logged with what the provider reported (tokens, or audio seconds) and its
cost at list prices (prices.py). Codex runs are logged too, at $0: they use the owner's ChatGPT plan.

These are estimates from measured usage; the providers' bills are the reference (they can differ slightly:
taxes, rounding, price changes). Each person's usage is in their own usage.json; the owner also sees everyone's
(they pay for it). The feature is the user-facing action that caused the call (chat.send, recording.analyze…).
"""
from __future__ import annotations

from collections import defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timedelta
from pathlib import Path

from .. import config, prices, users
from ..logs import log
from ..models import UsageBook, UsageEntry
from ..storage import JsonDocument, UserDocument

store = UserDocument("usage.json", UsageBook)
MAX_ENTRIES = 20_000
_feature: ContextVar[str] = ContextVar("usage_feature", default="")
_tallies: ContextVar[tuple] = ContextVar("usage_tallies", default=())


@contextmanager
def tally():
    """Collect the entries recorded inside (also in nested tallies): `with tally() as t: …; sum_cost(t)`."""
    entries: list[UsageEntry] = []
    token = _tallies.set(_tallies.get() + (entries,))
    try:
        yield entries
    finally:
        _tallies.reset(token)


def sum_cost(entries: list[UsageEntry]) -> float:
    return round(sum(e.cost_usd or 0 for e in entries), 6)


@contextmanager
def feature(name: str):
    """Attribute the AI calls made inside to `name`, unless an outer feature is already set (a chat that runs
    tools stays "chat.send")."""
    token = _feature.set(name) if not _feature.get() else None
    try:
        yield
    finally:
        if token is not None:
            _feature.reset(token)


def _add(entry: UsageEntry) -> UsageEntry:
    for collected in _tallies.get():
        collected.append(entry)
    try:
        with store.transaction() as book:
            book.entries.append(entry)
            if len(book.entries) > MAX_ENTRIES:
                book.entries = book.entries[-MAX_ENTRIES:]
    except Exception as e:  # never let bookkeeping break the feature itself
        log.warning("Could not record usage: %s", e)
    return entry


def record_openai(response, model: str) -> None:
    """A Responses API call: usage.input_tokens (includes cached), output_tokens (includes reasoning)."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    details = getattr(u, "input_tokens_details", None)
    cached = getattr(details, "cached_tokens", 0) or 0
    fresh = max((u.input_tokens or 0) - cached, 0)
    model = getattr(response, "model", None) or model
    _add(UsageEntry(provider="openai", model=model, feature=_feature.get(), input_tokens=fresh,
                    cached_tokens=cached, output_tokens=u.output_tokens or 0,
                    cost_usd=prices.token_cost("openai", model, fresh, u.output_tokens or 0, cached)))


def record_anthropic(response) -> None:
    """A Messages API call: input_tokens excludes cache reads/writes, which are reported separately."""
    u = getattr(response, "usage", None)
    if u is None:
        return
    model = getattr(response, "model", "") or ""
    cached = getattr(u, "cache_read_input_tokens", 0) or 0
    written = getattr(u, "cache_creation_input_tokens", 0) or 0
    _add(UsageEntry(provider="anthropic", model=model, feature=_feature.get(), input_tokens=u.input_tokens or 0,
                    cached_tokens=cached, cache_write_tokens=written, output_tokens=u.output_tokens or 0,
                    cost_usd=prices.token_cost("anthropic", model, u.input_tokens or 0, u.output_tokens or 0,
                                               cached, written)))


def record_audio(model: str, seconds: float) -> None:
    _add(UsageEntry(provider="openai", model=model, feature=_feature.get(), seconds=round(seconds, 1),
                    cost_usd=prices.audio_cost("openai", model, seconds)))


def record_plan(model: str, seconds: float) -> None:
    """A Codex run on the owner's ChatGPT plan: no per-use cost."""
    _add(UsageEntry(provider="codex", model=model, feature=_feature.get(), seconds=round(seconds, 1),
                    cost_usd=0.0, covered_by_plan=True))


# ------------------------------------------------------------------ estimates

def estimate_tokens(text: str) -> int:
    """Rough token count (about 4 characters per token in English; Hebrew uses more tokens per character)."""
    return max(1, len(text) // 3)


def estimate_text(model: str, input_tokens: int, output_tokens: int, provider: str = "openai") -> float | None:
    return prices.token_cost(provider, model, input_tokens, output_tokens)


def average_cost(feature_name: str) -> dict | None:
    """Average cost per use of a feature from this person's history (per user action, not per AI call)."""
    entries = [e for e in store.load().entries if e.feature == feature_name and e.cost_usd is not None]
    if not entries:
        return None
    # calls within 3 minutes of each other belong to the same use (a chat message runs several calls)
    uses, last = 0, None
    for e in entries:
        at = datetime.fromisoformat(e.at)
        if last is None or at - last > timedelta(minutes=3):
            uses += 1
        last = at
    total = sum(e.cost_usd for e in entries)
    return {"feature": feature_name, "uses": uses, "average_usd": round(total / uses, 5), "total_usd": round(total, 4)}


# --------------------------------------------------------------------- report

def _all_books() -> dict[str, UsageBook]:
    """Everyone's usage (for the owner): "" is the owner, other keys are Tailscale logins (folder names)."""
    books = {"": JsonDocument(config.DATA_DIR / "usage.json", UsageBook).load()}
    if config.USERS_DIR.exists():
        for folder in sorted(config.USERS_DIR.iterdir()):
            path: Path = folder / "data" / "usage.json"
            if path.exists():
                books[folder.name] = JsonDocument(path, UsageBook).load()
    return books


def report(days: int = 30) -> dict:
    """Costs this month, last month and over the last `days` days: by provider, feature and model, per day,
    recent calls, and (owner in multi-user mode) per person."""
    entries = store.load().entries
    today = date.today()
    month_start = today.replace(day=1)
    last_month_start = (month_start - timedelta(days=1)).replace(day=1)
    since = today - timedelta(days=days - 1)

    def total(es):
        return round(sum(e.cost_usd or 0 for e in es), 4)

    def group(es, key):
        out = defaultdict(lambda: {"cost_usd": 0.0, "calls": 0, "input_tokens": 0, "output_tokens": 0, "seconds": 0.0})
        for e in es:
            g = out[key(e)]
            g["cost_usd"] += e.cost_usd or 0
            g["calls"] += 1
            g["input_tokens"] += e.input_tokens + e.cached_tokens + e.cache_write_tokens
            g["output_tokens"] += e.output_tokens
            g["seconds"] += e.seconds
        return sorted(({"name": k, **{n: round(v, 4) if isinstance(v, float) else v for n, v in g.items()}}
                       for k, g in out.items()), key=lambda g: -g["cost_usd"])

    day = lambda e: e.at[:10]  # noqa: E731
    this_month = [e for e in entries if day(e) >= month_start.isoformat()]
    last_month = [e for e in entries if last_month_start.isoformat() <= day(e) < month_start.isoformat()]
    recent = [e for e in entries if day(e) >= since.isoformat()]
    per_day = {(since + timedelta(days=i)).isoformat(): 0.0 for i in range(days)}
    for e in recent:
        per_day[day(e)] = per_day.get(day(e), 0.0) + (e.cost_usd or 0)

    out = {
        "prices_checked_on": prices.CHECKED_ON,
        "this_month_usd": total(this_month), "last_month_usd": total(last_month),
        "unpriced_calls": sum(1 for e in this_month if e.cost_usd is None),
        "plan_runs_this_month": sum(1 for e in this_month if e.covered_by_plan),
        "by_provider": group(this_month, lambda e: e.provider),
        "by_feature": group(this_month, lambda e: e.feature or "other"),
        "by_model": group(this_month, lambda e: f"{e.provider}/{e.model}"),
        "per_day": [{"date": d, "cost_usd": round(c, 4)} for d, c in sorted(per_day.items())],
        "recent": [e.model_dump(mode="json") for e in entries[-50:][::-1]],
        "people": None,
    }
    if users.enabled() and users.current() == "":
        out["people"] = [{"user": login or (users.owner() or "you (owner)"),
                          "this_month_usd": total([e for e in b.entries if day(e) >= month_start.isoformat()]),
                          "last_month_usd": total([e for e in b.entries
                                                   if last_month_start.isoformat() <= day(e) < month_start.isoformat()])}
                         for login, b in _all_books().items()]
    return out
