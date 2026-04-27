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
