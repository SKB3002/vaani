"""Simple password-gate middleware for Vercel deployment.

When FINEYE_APP_PASSWORD is set:
  - /login  (GET/POST) is always public — serves the login form / processes it.
  - /static/* is always public — CSS/JS needed to render the login page.
  - /health  is always public — for Vercel health checks.
  - All other routes require a valid signed session cookie.

When FINEYE_APP_PASSWORD is empty, the middleware is a no-op pass-through.

Cookie is signed with itsdangerous.URLSafeSerializer using the password itself
as the secret key — no separate SECRET_KEY env var needed.
"""
from __future__ import annotations

import hashlib
from typing import Awaitable, Callable

from fastapi import Request, Response
from fastapi.responses import HTMLResponse, RedirectResponse
from itsdangerous import BadSignature, URLSafeSerializer
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.types import ASGIApp

COOKIE_NAME = "vaani_session"
_LOGIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Vaani — Sign in</title>
  <link rel="preconnect" href="https://fonts.googleapis.com">
  <link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;1,9..144,300&family=Inter+Tight:wght@400;500;600&display=swap">
  <style>
    *, *::before, *::after {{ box-sizing: border-box; margin: 0; padding: 0; }}

    body {{
      font-family: "Inter Tight", system-ui, sans-serif;
      background: #060d1a;
      color: #e2e8f0;
      min-height: 100svh;
      display: flex;
      align-items: center;
      justify-content: center;
      padding: 1.5rem;
      position: relative;
      overflow: hidden;
    }}

    /* Ambient glow blobs */
    body::before, body::after {{
      content: "";
      position: fixed;
      border-radius: 50%;
      filter: blur(80px);
      opacity: 0.18;
      pointer-events: none;
    }}
    body::before {{
      width: 480px; height: 480px;
      background: radial-gradient(circle, #0ea5e9, transparent 70%);
      top: -120px; left: -100px;
    }}
    body::after {{
      width: 360px; height: 360px;
      background: radial-gradient(circle, #6366f1, transparent 70%);
      bottom: -80px; right: -80px;
    }}

    .card {{
      position: relative;
      z-index: 1;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(18px);
      -webkit-backdrop-filter: blur(18px);
      border: 1px solid rgba(255,255,255,0.07);
      border-radius: 20px;
      padding: 2.75rem 2.5rem 2.25rem;
      width: 100%;
      max-width: 360px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(14,165,233,0.08);
    }}

    .logo {{
      display: flex;
      align-items: center;
      gap: 0.6rem;
      justify-content: center;
      margin-bottom: 0.5rem;
    }}
    .logo__dot {{
      width: 10px; height: 10px;
      border-radius: 50%;
      background: #0ea5e9;
      box-shadow: 0 0 12px #0ea5e9aa;
      flex-shrink: 0;
    }}
    .logo__name {{
      font-family: "Fraunces", Georgia, serif;
      font-size: 2rem;
      font-weight: 400;
      letter-spacing: -0.02em;
      color: #f1f5f9;
      line-height: 1;
    }}

    .tagline {{
      text-align: center;
      font-size: 0.8rem;
      color: #64748b;
      letter-spacing: 0.06em;
      text-transform: uppercase;
      margin-bottom: 2rem;
    }}

    label {{
      display: block;
      font-size: 0.75rem;
      font-weight: 600;
      letter-spacing: 0.05em;
      text-transform: uppercase;
      color: #94a3b8;
      margin-bottom: 0.45rem;
    }}

    .input-wrap {{
      position: relative;
      margin-bottom: 1.25rem;
    }}
    .input-wrap svg {{
      position: absolute;
      left: 0.8rem;
      top: 50%;
      transform: translateY(-50%);
      color: #475569;
      pointer-events: none;
    }}
    input[type="password"] {{
      width: 100%;
      padding: 0.65rem 0.9rem 0.65rem 2.5rem;
      border-radius: 10px;
      border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04);
      color: #e2e8f0;
      font-size: 0.95rem;
      font-family: inherit;
      outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }}
    input[type="password"]:focus {{
      border-color: #0ea5e9;
      box-shadow: 0 0 0 3px rgba(14,165,233,0.15);
      background: rgba(14,165,233,0.05);
    }}
    input[type="password"]::placeholder {{ color: #475569; }}

    button[type="submit"] {{
      width: 100%;
      padding: 0.7rem;
      border-radius: 10px;
      border: none;
      background: linear-gradient(135deg, #0ea5e9, #6366f1);
      color: #fff;
      font-size: 0.95rem;
      font-weight: 600;
      font-family: inherit;
      cursor: pointer;
      letter-spacing: 0.01em;
      transition: opacity 0.15s, transform 0.1s;
      box-shadow: 0 4px 16px rgba(14,165,233,0.3);
    }}
    button[type="submit"]:hover {{ opacity: 0.92; transform: translateY(-1px); }}
    button[type="submit"]:active {{ transform: translateY(0); }}

    .err {{
      display: flex;
      align-items: center;
      gap: 0.4rem;
      background: rgba(248,113,113,0.1);
      border: 1px solid rgba(248,113,113,0.25);
      border-radius: 8px;
      color: #fca5a5;
      font-size: 0.85rem;
      padding: 0.55rem 0.75rem;
      margin-top: 1rem;
    }}

    .footer {{
      text-align: center;
      font-size: 0.72rem;
      color: #334155;
      margin-top: 1.75rem;
    }}
  </style>
</head>
<body>
  <div class="card">
    <div class="logo">
      <span class="logo__dot"></span>
      <span class="logo__name">Vaani</span>
    </div>
    <p class="tagline">Your personal finance, by voice</p>

    <form method="post" action="/login">
      <label for="pw">Password</label>
      <div class="input-wrap">
        <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">
          <rect x="3" y="11" width="18" height="11" rx="2" ry="2"/>
          <path d="M7 11V7a5 5 0 0 1 10 0v4"/>
        </svg>
        <input type="password" id="pw" name="password" placeholder="Enter your password" autofocus required>
      </div>
      <button type="submit">Sign in</button>
      {error}
    </form>

    <p class="footer">Private deployment &middot; Only you have access</p>
  </div>
</body>
</html>
"""

_PUBLIC_PREFIXES = ("/login", "/static", "/health")

# Public paths for multi-user mode (signup + landing are added vs single-user).
_MULTI_USER_PUBLIC_PREFIXES = (
    "/welcome",
    "/login",
    "/signup",
    "/logout",
    "/static",
    "/health",
)

# Session cookie max age (30 days) — matches what the single-user gate uses.
_SESSION_MAX_AGE = 60 * 60 * 24 * 30


def _signer(password: str) -> URLSafeSerializer:
    secret = hashlib.sha256(password.encode()).hexdigest()
    return URLSafeSerializer(secret, salt="vaani-auth")


def _is_authed(request: Request, password: str) -> bool:
    token = request.cookies.get(COOKIE_NAME)
    if not token:
        return False
    try:
        _signer(password).loads(token)
        return True
    except BadSignature:
        return False


class PasswordGateMiddleware(BaseHTTPMiddleware):
    def __init__(self, app: ASGIApp, password: str) -> None:
        super().__init__(app)
        self._password = password

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        path = request.url.path

        # Public paths bypass the gate
        if any(path.startswith(p) for p in _PUBLIC_PREFIXES):
            return await call_next(request)

        if not _is_authed(request, self._password):
            return RedirectResponse("/login", status_code=302)

        return await call_next(request)


def make_login_router():  # type: ignore[return]
    """Return an APIRouter with GET /login and POST /login."""
    from fastapi import APIRouter, Form
    from fastapi.responses import HTMLResponse, RedirectResponse

    router = APIRouter()

    @router.get("/login", include_in_schema=False)
    async def login_page() -> HTMLResponse:
        return HTMLResponse(_LOGIN_HTML.format(error=""))

    @router.post("/login", include_in_schema=False)
    async def login_submit(password: str = Form(...)) -> Response:
        from app.config import get_settings

        cfg = get_settings()
        if password != cfg.APP_PASSWORD:
            html = _LOGIN_HTML.format(
                error='<p class="err">Incorrect password</p>'
            )
            return HTMLResponse(html, status_code=401)

        token = _signer(cfg.APP_PASSWORD).dumps("ok")
        response = RedirectResponse("/", status_code=302)
        response.set_cookie(
            COOKIE_NAME,
            token,
            httponly=True,
            secure=True,   # Vercel is always HTTPS
            samesite="lax",
            max_age=60 * 60 * 24 * 30,  # 30 days
        )
        return response

    @router.get("/logout", include_in_schema=False)
    async def logout() -> Response:
        response = RedirectResponse("/login", status_code=302)
        response.delete_cookie(COOKIE_NAME)
        return response

    return router


# ===========================================================================
# Multi-user mode — per-account signup/login + per-request user context
#
# When FINEYE_MULTI_USER=true, the app factory mounts ``AuthMiddleware``
# instead of ``PasswordGateMiddleware`` and includes ``make_multi_user_router``
# below. The middleware:
#   1. Lets public paths through.
#   2. Validates the session cookie; on failure redirects to /login.
#   3. Binds the active user id to ``app.context._current_user_id`` for the
#      duration of the request so the storage layer scopes every query.
# ===========================================================================


MULTI_USER_COOKIE_NAME = "vaani_user"
# Companion non-httponly "presence" cookie so client-side JS can tell the user
# is signed in (e.g. to reveal the logout button) without exposing the signed
# session token. Carries no security value — just a sentinel.
MULTI_USER_PRESENCE_COOKIE = "vaani_logged_in"


class AuthMiddleware(BaseHTTPMiddleware):
    """Per-request authentication + user-context binding for multi-user mode."""

    async def dispatch(
        self, request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        from app.context import reset_current_user, set_current_user
        from app.services.auth import read_session_token

        path = request.url.path
        if any(path.startswith(p) for p in _MULTI_USER_PUBLIC_PREFIXES):
            return await call_next(request)

        token = request.cookies.get(MULTI_USER_COOKIE_NAME, "")
        user_id = read_session_token(token, max_age_seconds=_SESSION_MAX_AGE)
        if not user_id:
            # Unauth visitors hit the landing page first (pitch + features),
            # then click through to /login or /signup themselves.
            return RedirectResponse("/welcome", status_code=302)

        # Stash the id for templates / handlers that want to render the
        # signed-in user's email without re-validating the cookie.
        request.state.user_id = user_id

        ctx_token = set_current_user(user_id)
        try:
            return await call_next(request)
        finally:
            reset_current_user(ctx_token)


# Shared styling block reused by both signup and login pages — copied from
# the single-user login HTML so reviewers see the same polished look.
_AUTH_PAGE_STYLE = """
    *, *::before, *::after { box-sizing: border-box; margin: 0; padding: 0; }
    body {
      font-family: "Inter Tight", system-ui, sans-serif;
      background: #060d1a; color: #e2e8f0;
      min-height: 100svh; display: flex; align-items: center; justify-content: center;
      padding: 1.5rem; position: relative; overflow: hidden;
    }
    body::before, body::after {
      content: ""; position: fixed; border-radius: 50%;
      filter: blur(80px); opacity: 0.18; pointer-events: none;
    }
    body::before { width: 480px; height: 480px;
      background: radial-gradient(circle, #0ea5e9, transparent 70%);
      top: -120px; left: -100px; }
    body::after { width: 360px; height: 360px;
      background: radial-gradient(circle, #6366f1, transparent 70%);
      bottom: -80px; right: -80px; }
    .card {
      position: relative; z-index: 1;
      background: rgba(15, 23, 42, 0.85);
      backdrop-filter: blur(18px); -webkit-backdrop-filter: blur(18px);
      border: 1px solid rgba(255,255,255,0.07); border-radius: 20px;
      padding: 2.5rem 2.5rem 2rem; width: 100%; max-width: 380px;
      box-shadow: 0 8px 40px rgba(0,0,0,0.5), 0 0 0 1px rgba(14,165,233,0.08);
    }
    .back {
      position: absolute; top: 1.3rem; left: 1.3rem; z-index: 2;
      display: inline-flex; align-items: center; gap: 0.4rem;
      color: #94a3b8; text-decoration: none; font-size: 0.82rem;
      padding: 0.4rem 0.7rem; border-radius: 8px;
      border: 1px solid rgba(255,255,255,0.06);
      transition: color 0.15s, border-color 0.15s, background 0.15s;
    }
    .back:hover { color: #f1f5f9; border-color: rgba(255,255,255,0.18);
      background: rgba(255,255,255,0.03); }
    .logo { display: flex; align-items: center; gap: 0.6rem;
      justify-content: center; margin-bottom: 0.55rem; }
    .logo__dot { width: 11px; height: 11px; border-radius: 50%;
      background: #0ea5e9; box-shadow: 0 0 14px rgba(14,165,233,0.7);
      animation: vaani-pulse 1.8s ease-in-out infinite; }
    @keyframes vaani-pulse { 0%, 100% { opacity: 1; } 50% { opacity: 0.4; } }
    .logo__name { font-family: "Fraunces", Georgia, serif; font-size: 2rem;
      font-weight: 400; letter-spacing: -0.02em; color: #f1f5f9; line-height: 1; }
    .tagline { text-align: center; font-size: 0.78rem; color: #64748b;
      letter-spacing: 0.06em; text-transform: uppercase; margin-bottom: 1.75rem; }
    .accent-strip {
      height: 2px; width: 38px; margin: 0 auto 1.5rem;
      background: linear-gradient(90deg, transparent, #0ea5e9, #6366f1, transparent);
      border-radius: 999px;
    }
    label { display: block; font-size: 0.72rem; font-weight: 600;
      letter-spacing: 0.05em; text-transform: uppercase; color: #94a3b8;
      margin-bottom: 0.4rem; }
    .field { margin-bottom: 1rem; }
    input[type="email"], input[type="password"], input[type="text"] {
      width: 100%; padding: 0.65rem 0.9rem;
      border-radius: 10px; border: 1px solid rgba(255,255,255,0.08);
      background: rgba(255,255,255,0.04); color: #e2e8f0;
      font-size: 0.95rem; font-family: inherit; outline: none;
      transition: border-color 0.15s, box-shadow 0.15s;
    }
    input:focus { border-color: #0ea5e9;
      box-shadow: 0 0 0 3px rgba(14,165,233,0.15);
      background: rgba(14,165,233,0.05); }
    input::placeholder { color: #475569; }
    button[type="submit"] {
      width: 100%; padding: 0.7rem; border-radius: 10px; border: none;
      background: linear-gradient(135deg, #0ea5e9, #6366f1);
      color: #fff; font-size: 0.95rem; font-weight: 600;
      font-family: inherit; cursor: pointer; letter-spacing: 0.01em;
      transition: opacity 0.15s, transform 0.1s;
      box-shadow: 0 4px 16px rgba(14,165,233,0.3); margin-top: 0.25rem;
    }
    button[type="submit"]:hover { opacity: 0.92; transform: translateY(-1px); }
    .err { display: flex; align-items: center; gap: 0.4rem;
      background: rgba(248,113,113,0.1); border: 1px solid rgba(248,113,113,0.25);
      border-radius: 8px; color: #fca5a5; font-size: 0.85rem;
      padding: 0.55rem 0.75rem; margin-top: 0.9rem; }
    .swap { text-align: center; font-size: 0.85rem; color: #94a3b8;
      margin-top: 1.4rem; }
    .swap a { color: #38bdf8; text-decoration: none; font-weight: 500; }
    .swap a:hover { text-decoration: underline; }
    .consent { display: flex; gap: 0.55rem; align-items: flex-start;
      font-size: 0.78rem; color: #94a3b8; line-height: 1.45;
      background: rgba(14,165,233,0.05); border: 1px solid rgba(14,165,233,0.12);
      border-radius: 10px; padding: 0.7rem 0.85rem; margin: 0.4rem 0 1.1rem; }
    .consent input { margin-top: 0.15rem; accent-color: #0ea5e9; }
    .footer { text-align: center; font-size: 0.7rem; color: #334155;
      margin-top: 1.5rem; }
"""

_BACK_LINK = (
    '<a href="/welcome" class="back" aria-label="Back to Vaani">'
    '<svg width="13" height="13" viewBox="0 0 24 24" fill="none" stroke="currentColor" '
    'stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round">'
    '<line x1="19" y1="12" x2="5" y2="12"/>'
    '<polyline points="12 19 5 12 12 5"/></svg>'
    "Back</a>"
)

_SIGNUP_HTML = """\
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vaani — Create account</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;1,9..144,300&family=Inter+Tight:wght@400;500;600&display=swap">
<style>__STYLE__</style></head><body>
__BACK__
<div class="card">
  <div class="logo"><span class="logo__dot"></span><span class="logo__name">Vaani</span></div>
  <p class="tagline">Create your account</p>
  <div class="accent-strip"></div>
  <form method="post" action="/signup">
    <div class="field">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" placeholder="you@example.com" value="__EMAIL__" required autofocus>
    </div>
    <div class="field">
      <label for="pw">Password</label>
      <input type="password" id="pw" name="password" placeholder="At least 8 characters" required minlength="8">
    </div>
    <label class="consent">
      <input type="checkbox" name="consent" value="yes" required>
      <span>This is a hosted trial. I understand my finance data will be stored on Vaani's server for the trial. The local/offline version of Vaani keeps data on your own device.</span>
    </label>
    <button type="submit">Create account</button>
    __ERROR__
  </form>
  <p class="swap">Already have an account? <a href="/login">Sign in</a></p>
  <p class="footer">Vaani trial &middot; Your data stays per-account</p>
</div></body></html>"""

_MULTI_LOGIN_HTML = """\
<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Vaani — Sign in</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="stylesheet" href="https://fonts.googleapis.com/css2?family=Fraunces:ital,opsz,wght@0,9..144,400;1,9..144,300&family=Inter+Tight:wght@400;500;600&display=swap">
<style>__STYLE__</style></head><body>
__BACK__
<div class="card">
  <div class="logo"><span class="logo__dot"></span><span class="logo__name">Vaani</span></div>
  <p class="tagline">Welcome back</p>
  <div class="accent-strip"></div>
  <form method="post" action="/login">
    <div class="field">
      <label for="email">Email</label>
      <input type="email" id="email" name="email" placeholder="you@example.com" value="__EMAIL__" required autofocus>
    </div>
    <div class="field">
      <label for="pw">Password</label>
      <input type="password" id="pw" name="password" placeholder="Your password" required>
    </div>
    <button type="submit">Sign in</button>
    __ERROR__
  </form>
  <p class="swap">No account yet? <a href="/signup">Create one</a></p>
  <p class="footer">Vaani trial &middot; Voice-driven finance, by account</p>
</div></body></html>"""


def _render_auth_page(template: str, *, email: str = "", error: str = "") -> str:
    err_block = f'<p class="err">{error}</p>' if error else ""
    return (
        template
        .replace("__STYLE__", _AUTH_PAGE_STYLE)
        .replace("__BACK__", _BACK_LINK)
        .replace("__EMAIL__", _escape(email))
        .replace("__ERROR__", err_block)
    )


def _escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def make_multi_user_router():  # type: ignore[no-untyped-def]
    """Return an APIRouter with /signup, /login, /logout for multi-user mode."""
    from fastapi import APIRouter, Form
    from fastapi.responses import HTMLResponse, RedirectResponse

    from app.services.auth import (
        AuthError,
        EmailAlreadyRegisteredError,
        InvalidEmailError,
        WeakPasswordError,
        issue_session_token,
        login,
        signup,
    )

    router = APIRouter()

    def _set_session_cookie(response: Response, user_id: str) -> None:
        token = issue_session_token(user_id)
        response.set_cookie(
            MULTI_USER_COOKIE_NAME,
            token,
            httponly=True,
            secure=True,  # Vercel is always HTTPS; harmless on http://localhost during dev
            samesite="lax",
            max_age=_SESSION_MAX_AGE,
            path="/",
        )
        # Non-httponly sentinel so the topbar can show the sign-out button.
        # The value is meaningless; the browser dropping it just means the
        # logout icon stops appearing — auth itself is unaffected.
        response.set_cookie(
            MULTI_USER_PRESENCE_COOKIE,
            "1",
            httponly=False,
            secure=True,
            samesite="lax",
            max_age=_SESSION_MAX_AGE,
            path="/",
        )

    @router.get("/signup", include_in_schema=False)
    async def signup_page() -> HTMLResponse:
        return HTMLResponse(_render_auth_page(_SIGNUP_HTML))

    @router.post("/signup", include_in_schema=False)
    async def signup_submit(
        email: str = Form(...),
        password: str = Form(...),
        consent: str = Form(""),
    ) -> Response:
        try:
            user = signup(email, password, consented=(consent == "yes"))
        except InvalidEmailError as exc:
            return HTMLResponse(
                _render_auth_page(_SIGNUP_HTML, email=email, error=str(exc)),
                status_code=400,
            )
        except WeakPasswordError as exc:
            return HTMLResponse(
                _render_auth_page(_SIGNUP_HTML, email=email, error=str(exc)),
                status_code=400,
            )
        except EmailAlreadyRegisteredError as exc:
            return HTMLResponse(
                _render_auth_page(_SIGNUP_HTML, email=email, error=str(exc)),
                status_code=409,
            )
        except AuthError as exc:
            return HTMLResponse(
                _render_auth_page(_SIGNUP_HTML, email=email, error=str(exc)),
                status_code=400,
            )

        response = RedirectResponse("/", status_code=302)
        _set_session_cookie(response, user.id)
        return response

    @router.get("/login", include_in_schema=False)
    async def login_page() -> HTMLResponse:
        return HTMLResponse(_render_auth_page(_MULTI_LOGIN_HTML))

    @router.post("/login", include_in_schema=False)
    async def login_submit(
        email: str = Form(...),
        password: str = Form(...),
    ) -> Response:
        user = login(email, password)
        if user is None:
            return HTMLResponse(
                _render_auth_page(
                    _MULTI_LOGIN_HTML,
                    email=email,
                    error="Incorrect email or password",
                ),
                status_code=401,
            )
        response = RedirectResponse("/", status_code=302)
        _set_session_cookie(response, user.id)
        return response

    @router.get("/logout", include_in_schema=False)
    async def logout() -> Response:
        response = RedirectResponse("/welcome", status_code=302)
        response.delete_cookie(MULTI_USER_COOKIE_NAME, path="/")
        response.delete_cookie(MULTI_USER_PRESENCE_COOKIE, path="/")
        return response

    return router
