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

COOKIE_NAME = "fineye_session"
_LOGIN_HTML = """\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>FinEye — Login</title>
  <style>
    body {{ font-family: system-ui, sans-serif; background: #0f172a; color: #e2e8f0;
           display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; }}
    .card {{ background: #1e293b; border-radius: 12px; padding: 2.5rem; width: 320px; box-shadow: 0 4px 24px #0005; }}
    h1 {{ margin: 0 0 1.5rem; font-size: 1.4rem; text-align: center; color: #38bdf8; }}
    input {{ width: 100%; padding: .6rem .8rem; border-radius: 6px; border: 1px solid #334155;
             background: #0f172a; color: #e2e8f0; font-size: 1rem; box-sizing: border-box; }}
    button {{ width: 100%; margin-top: 1rem; padding: .7rem; border-radius: 6px; border: none;
              background: #0ea5e9; color: #fff; font-size: 1rem; cursor: pointer; }}
    button:hover {{ background: #38bdf8; }}
    .err {{ color: #f87171; margin-top: .75rem; text-align: center; font-size: .9rem; }}
  </style>
</head>
<body>
  <div class="card">
    <h1>FinEye</h1>
    <form method="post" action="/login">
      <input type="password" name="password" placeholder="Password" autofocus required>
      <button type="submit">Sign in</button>
      {error}
    </form>
  </div>
</body>
</html>
"""

_PUBLIC_PREFIXES = ("/login", "/static", "/health")


def _signer(password: str) -> URLSafeSerializer:
    secret = hashlib.sha256(password.encode()).hexdigest()
    return URLSafeSerializer(secret, salt="fineye-auth")


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
