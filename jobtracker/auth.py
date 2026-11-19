"""Who may use the app.

- With JOBTRACKER_OWNER set (behind `tailscale serve`): only the Tailscale identity counts (see users.py). The
  owner and invited friends get in, anyone else is refused, and so is any request without the identity, even
  with the password: otherwise anyone knowing the password would be the owner.
- Otherwise, optional password login. Off unless JOBTRACKER_PASSWORD is set (local use needs none).

A successful login sets a signed, HTTP-only cookie "<expiry>.<hmac>". The signing key is derived from the
password, so changing the password signs everyone out. Health checks and the login page stay public.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import html
import time

from fastapi import Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from . import config, users

COOKIE = "jt_session"
PUBLIC = ("/login", "/health", "/favicon.svg", "/favicon-32.png", "/apple-touch-icon.png",
          "/manifest.webmanifest", "/icons/", "/sw.js")  # what the login page and app install need


def enabled() -> bool:
    return bool(config.PASSWORD)


def _key() -> bytes:
    return hashlib.sha256(f"jobtracker-session:{config.PASSWORD}".encode()).digest()


def _sign(expiry: int) -> str:
    return hmac.new(_key(), str(expiry).encode(), hashlib.sha256).hexdigest()


def make_token(now: float | None = None) -> str:
    expiry = int((now or time.time()) + config.SESSION_DAYS * 86400)
    return f"{expiry}.{_sign(expiry)}"


def valid(token: str | None, now: float | None = None) -> bool:
    try:
        expiry, signature = (token or "").split(".", 1)
        return int(expiry) > (now or time.time()) and hmac.compare_digest(signature, _sign(int(expiry)))
    except ValueError:
        return False


def is_public(path: str) -> bool:
    return any(path == p or (p.endswith("/") and path.startswith(p)) for p in PUBLIC)


async def middleware(request: Request, call_next):
    if is_public(request.url.path):
        return await call_next(request)
    if users.enabled():
        login = request.headers.get(users.HEADER, "").strip()
        if not login:  # not through tailscale serve: no way to know who it is
            return _refused(request, "Open the app through its Tailscale address (https://…ts.net).")
        try:
            user = users.resolve(login)
        except users.NotInvited:
            return _refused(request, f"{login} is not invited to this Job Search Tracker. Ask its owner to add you.")
        with users.acting_as(user):  # every data path in this request is this user's
            return await call_next(request)
    if not enabled() or valid(request.cookies.get(COOKIE)):
        return await call_next(request)
    if request.url.path.startswith(("/api/", "/files/")):
        return JSONResponse({"detail": "Login required"}, status_code=401)
    return RedirectResponse("/login", status_code=303)


def _refused(request: Request, message: str):
    if request.url.path.startswith(("/api/", "/files/")):
        return JSONResponse({"detail": message}, status_code=403)
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Job Search · No access</title>
<style>body{{font:16px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif;max-width:32em;margin:15vh auto;
padding:0 16px;color:#1c2430}}@media(prefers-color-scheme:dark){{body{{background:#12151b;color:#e6e9ee}}}}</style>
</head><body><h1>No access</h1><p>{html.escape(message)}</p></body></html>""", status_code=403)


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto") == "https"


async def login(request: Request):
    form = await request.form()
    if hmac.compare_digest(str(form.get("password", "")).encode(), config.PASSWORD.encode()):
        response = RedirectResponse("/", status_code=303)
        response.set_cookie(COOKIE, make_token(), max_age=config.SESSION_DAYS * 86400,
                            httponly=True, samesite="lax", secure=_secure(request))
        return response
    await asyncio.sleep(1)  # slows down password guessing
    return login_page(error="Wrong password")


def logout() -> RedirectResponse:
    response = RedirectResponse("/login", status_code=303)
    response.delete_cookie(COOKIE)
    return response


def login_page(error: str = "") -> HTMLResponse:
    message = f'<p class="err">{html.escape(error)}</p>' if error else ""
    return HTMLResponse(f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Job Search · Sign in</title>
<link rel="icon" href="/favicon.svg"><style>
body{{margin:0;min-height:100vh;display:grid;place-items:center;background:#f5f6f8;color:#1c2430;
font:15px/1.5 -apple-system,BlinkMacSystemFont,"Segoe UI",sans-serif}}
form{{background:#fff;border:1px solid #e1e4e8;border-radius:14px;padding:28px;width:min(340px,90vw);display:grid;gap:12px}}
img{{width:44px}}h1{{font-size:20px;margin:0}}input,button{{font:inherit;padding:10px 12px;border-radius:8px;border:1px solid #e1e4e8}}
button{{background:#2557d6;color:#fff;border:0;font-weight:600;cursor:pointer}}.err{{color:#c4332f;margin:0}}
@media(prefers-color-scheme:dark){{body{{background:#12151b;color:#e6e9ee}}form{{background:#1b2029;border-color:#2f3744}}
input{{background:#12151b;color:#e6e9ee;border-color:#2f3744}}}}</style></head><body>
<form method="post" action="/login"><img src="/favicon.svg" alt=""><h1>Job Search</h1>{message}
<input type="password" name="password" placeholder="Password" autofocus required autocomplete="current-password">
<button type="submit">Sign in</button></form></body></html>""")
