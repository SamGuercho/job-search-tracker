"""Outlook (personal Microsoft accounts: outlook.com, hotmail, live) through Microsoft Graph, read-only.

The mail service (services/mail.py) does everything else: the sign-in, the filter, the model, the updates. This
module only knows Graph's shapes: which emails are new, and what one email says, as the same dict a Gmail message
becomes ({id, sender, subject, received_at, text, list_unsubscribe}).

- Sign-in: Microsoft's "consumers" endpoint (personal accounts only), scopes Mail.Read (read-only) + User.Read (the
  address) + offline_access (a refresh token, so the server checks in the background). Microsoft gives a new refresh
  token at each renewal: the mail service saves it. Unused for 90 days, it lapses (the checks keep it alive).
- Ids: Graph's ids change when an email moves to another folder; asking for immutable ids keeps them stable, so an
  email already looked at is never read twice.
- Graph has no "everything but sent, drafts, junk and trash" query: emails are listed across folders and those in
  the four skipped folders are left out by their folder's id.
"""
from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone

from . import web

AUTH_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/authorize"
TOKEN_URL = "https://login.microsoftonline.com/consumers/oauth2/v2.0/token"
SCOPE = "offline_access https://graph.microsoft.com/Mail.Read https://graph.microsoft.com/User.Read"
GRAPH = "https://graph.microsoft.com/v1.0/me"
# Immutable ids (stable across folders), and the body as plain text
PREFER = 'IdType="ImmutableId", outlook.body-content-type="text"'
SKIPPED_FOLDERS = ("sentitems", "drafts", "deleteditems", "junkemail")
CONSENT_PAGE = "https://account.live.com/consent/Manage"   # where people remove an app's access themselves

Get = Callable[..., dict]   # get(path_or_url, params=None) -> Graph's JSON answer


def granted(scope: str) -> bool:
    """Whether the token's scopes include reading mail."""
    return "mail.read" in scope.lower().replace("https://graph.microsoft.com/", "").split()


def address(get: Get) -> str:
    """The mailbox's address (personal accounts often have no `mail`: their sign-in name is the address)."""
    me = get("", {"$select": "mail,userPrincipalName"})
    return me.get("mail") or me.get("userPrincipalName") or ""


def skipped_folders(get: Get) -> set[str]:
    """The ids of Sent, Drafts, Deleted and Junk (a folder that can't be read is simply not skipped)."""
    ids = set()
    for name in SKIPPED_FOLDERS:
        try:
            ids.add(get(f"/mailFolders/{name}", {"$select": "id"})["id"])
        except Exception:  # noqa: BLE001  (a mailbox without that folder)
            continue
    return ids


def _utc(epoch: int) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _local(iso: str) -> str:
    """Graph's "2026-11-26T08:00:00Z" as the server's local time, like Gmail's dates."""
    if not iso:
        return ""
    return datetime.fromisoformat(iso.replace("Z", "+00:00")).astimezone().replace(tzinfo=None).isoformat(
        timespec="seconds")


def _listing(get: Get, since: int, select: str, limit: int, skip: set[str]) -> list[dict]:
    """Emails received since `since` (epoch seconds), newest first, outside the skipped folders, at most `limit`."""
    found: list[dict] = []
    answer = get("/messages", {"$filter": f"receivedDateTime ge {_utc(since)}", "$orderby": "receivedDateTime desc",
                               "$select": f"id,parentFolderId,{select}", "$top": 100})
    while True:
        found += [m for m in answer.get("value", []) if m.get("parentFolderId") not in skip]
        link = answer.get("@odata.nextLink")
        if len(found) >= limit or not link:
            return found[:limit]
        answer = get(link)


def list_new(get: Get, after: int, limit: int) -> list[str]:
    """Ids of the emails received after `after`, oldest first, at most `limit` (the newest ones)."""
    return [m["id"] for m in reversed(_listing(get, after, "receivedDateTime", limit, skipped_folders(get)))]


def _sender(message: dict) -> str:
    who = (message.get("from") or message.get("sender") or {}).get("emailAddress") or {}
    name, mail = (who.get("name") or "").replace('"', "").strip(), (who.get("address") or "").strip()
    if not mail:
        return name
    if not name or name.lower() == mail.lower():
        return mail
    return f'"{name}" <{mail}>' if any(ch in name for ch in ',;:<>@()[]') else f"{name} <{mail}>"


def _headers(message: dict) -> dict[str, str]:
    return {h["name"].lower(): h["value"] for h in message.get("internetMessageHeaders") or []}


def parse(message: dict) -> dict:
    """A Graph message as {id, sender, subject, received_at, text, list_unsubscribe}."""
    body = message.get("body") or {}
    text = body.get("content") or ""
    if (body.get("contentType") or "").lower() == "html":
        text = web.text_of("", text)["text"]
    return {"id": message["id"], "sender": _sender(message), "subject": message.get("subject") or "",
            "received_at": _local(message.get("receivedDateTime", "")),
            "text": text.strip() or message.get("bodyPreview", ""),
            "list_unsubscribe": bool(_headers(message).get("list-unsubscribe"))}


def fetch(get: Get, message_id: str) -> dict:
    return parse(get(f"/messages/{message_id}", {
        "$select": "id,from,sender,subject,receivedDateTime,body,bodyPreview,internetMessageHeaders"}))


def recent(get: Get, days: int, limit: int, now: float) -> list[dict]:
    """The last `days` days' emails, newest first, headers and preview only:
    {id, sender, subject, snippet, received_at, list_unsubscribe}."""
    found = _listing(get, int(now - days * 86400), "from,sender,subject,bodyPreview,receivedDateTime,"
                     "internetMessageHeaders", limit, skipped_folders(get))
    return [{"id": m["id"], "sender": _sender(m), "subject": m.get("subject") or "",
             "snippet": m.get("bodyPreview") or "", "received_at": _local(m.get("receivedDateTime", "")),
             "list_unsubscribe": bool(_headers(m).get("list-unsubscribe"))} for m in found]
