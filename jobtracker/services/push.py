"""Phone and desktop notifications (Web Push).

The server has one key pair (VAPID), created on first use in the data folder. Each person registers their
devices (push.subscribe, from the app's "Notifications" switch); a device's browser vendor (Google, Apple,
Mozilla) delivers the messages. A notification carries only a short title, text and the page to open.

iPhone: notifications only work in the app installed on the Home Screen (iOS 16.4+), not in Safari tabs.
"""
from __future__ import annotations

import json
from datetime import datetime

from pydantic import BaseModel, Field

from .. import config, users
from ..logs import log
from ..storage import UserDocument


class Subscription(BaseModel):
    endpoint: str
    keys: dict[str, str]
    device: str = ""
    timezone: str = "UTC"     # the device's time zone, for reminders at local times
    created_at: str = Field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))


class PushBook(BaseModel):
    subscriptions: list[Subscription] = []
    sent: dict[str, str] = {}  # reminder key -> when it was sent (so each reminder goes once)


store = UserDocument("push.json", PushBook)


# ------------------------------------------------------------------------ keys

def _key_path():
    return config.DATA_DIR / "vapid_private.pem"   # the server's key: shared by everyone, kept with the owner's data


def _vapid():
    from py_vapid import Vapid02

    path = _key_path()
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        key = Vapid02()
        key.generate_keys()
        key.save_key(str(path))
        log.info("Created the server's notification key (%s)", path)
    return Vapid02.from_file(str(path))


def public_key() -> str:
    """The key browsers need to subscribe (applicationServerKey), base64url."""
    from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat
    from py_vapid import b64urlencode

    raw = _vapid().public_key.public_bytes(Encoding.X962, PublicFormat.UncompressedPoint)
    return b64urlencode(raw)


# --------------------------------------------------------------- subscriptions

def subscribe(subscription: dict, timezone: str = "UTC", device: str = "") -> dict:
    sub = Subscription(endpoint=subscription["endpoint"], keys=subscription.get("keys", {}),
                       timezone=_valid_tz(timezone), device=device[:80])
    with store.transaction() as book:
        book.subscriptions = [s for s in book.subscriptions if s.endpoint != sub.endpoint] + [sub]
    return {"devices": len(store.load().subscriptions)}


def unsubscribe(endpoint: str) -> dict:
    with store.transaction() as book:
        book.subscriptions = [s for s in book.subscriptions if s.endpoint != endpoint]
    return {"devices": len(store.load().subscriptions)}


def devices() -> list[dict]:
    return [{"endpoint": s.endpoint, "device": s.device, "timezone": s.timezone, "created_at": s.created_at}
            for s in store.load().subscriptions]


def _valid_tz(name: str) -> str:
    from zoneinfo import ZoneInfo
    try:
        ZoneInfo(name)
        return name
    except Exception:
        return "UTC"


# --------------------------------------------------------------------- sending

def notify(title: str, body: str, url: str = "/", tag: str = "") -> int:
    """Send to every device of the current user; devices that unsubscribed are forgotten. Returns how many got
    it. Never raises: a notification must not break the feature that sends it."""
    subs = store.load().subscriptions
    if not subs:
        return 0
    try:
        from pywebpush import WebPushException, webpush
        vapid = _vapid()
    except Exception as e:
        log.warning("Notifications unavailable: %s", e)
        return 0
    payload = json.dumps({"title": title, "body": body, "url": url, "tag": tag or title})
    sent, gone = 0, []
    for s in subs:
        try:
            webpush({"endpoint": s.endpoint, "keys": s.keys}, payload, vapid_private_key=vapid,
                    vapid_claims={"sub": config.PUSH_CONTACT}, ttl=6 * 3600, timeout=10)
            sent += 1
        except WebPushException as e:
            status = getattr(e.response, "status_code", None)
            if status in (404, 410):  # the device unsubscribed or the app was removed
                gone.append(s.endpoint)
            else:
                log.warning("Notification to %s failed: %s", s.device or "a device", e)
        except Exception as e:
            log.warning("Notification to %s failed: %s", s.device or "a device", e)
    if gone:
        with store.transaction() as book:
            book.subscriptions = [s for s in book.subscriptions if s.endpoint not in gone]
    return sent


def notify_user(user: str, title: str, body: str, url: str = "/", tag: str = "") -> int:
    with users.acting_as(user):
        return notify(title, body, url, tag)
