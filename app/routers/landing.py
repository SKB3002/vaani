"""Public landing page — what unauthenticated visitors see first.

This route is always public (added to ``_MULTI_USER_PUBLIC_PREFIXES`` in
``app/middleware/auth.py``). Authenticated users typically land on ``/`` (the
dashboard); ``/welcome`` is the marketing entry point and is also where the
auth middleware redirects unauthenticated visitors so they hit the pitch
before the login form.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

router = APIRouter()
templates = Jinja2Templates(directory="templates")


@router.get("/welcome", response_class=HTMLResponse, include_in_schema=False)
def welcome(request: Request) -> HTMLResponse:
    """Standalone landing page — does not extend ``base.html`` (no sidebar)."""
    return templates.TemplateResponse(request, "landing.html", {})
