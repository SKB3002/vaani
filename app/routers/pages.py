"""HTML page routes for FinEye dashboard.

The `/` route is owned by `home.py`. This module adds the remaining
M0/M1 pages so users can navigate the full shell even when only a
subset of APIs is live. Data-fetching per page is intentionally
minimal — templates already render polished empty states when
backing data is missing.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _ctx(request: Request, **extra: Any) -> dict[str, Any]:
    settings = get_settings()
    ctx: dict[str, Any] = {
        "currency": settings.default_currency,
        "timezone": settings.default_timezone,
        "settings": {
            "currency": settings.default_currency,
            "timezone": settings.default_timezone,
            "locale": "en-IN",
        },
    }
    ctx.update(extra)
    return ctx


@router.get("/expenses", response_class=HTMLResponse)
def expenses_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "expenses.html", _ctx(request))


@router.get("/balances", response_class=HTMLResponse)
def balances_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "balances.html", _ctx(request, balances=[])
    )


@router.get("/investments", response_class=HTMLResponse)
def investments_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "investments.html", _ctx(request))


@router.get("/wishlist", response_class=HTMLResponse)
def wishlist_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "wishlist.html", _ctx(request))


@router.get("/goals/overview", response_class=HTMLResponse)
def goals_overview_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "goals_overview.html", _ctx(request, goals=[])
    )


@router.get("/goals/sources", response_class=HTMLResponse)
def goals_sources_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(
        request, "goals_sources.html", _ctx(request, goals=[])
    )


@router.get("/budgets", response_class=HTMLResponse)
def budgets_page(request: Request) -> HTMLResponse:
    caps = {"medical_upper_cap": 10000, "emergency_monthly_cap": 5000}
    return templates.TemplateResponse(
        request, "budgets.html", _ctx(request, budget_rules=[], caps=caps)
    )


@router.get("/budgets/monthly", response_class=HTMLResponse)
def budgets_monthly_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "budgets_monthly.html", _ctx(request))


@router.get("/charts", response_class=HTMLResponse)
def charts_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "charts.html", _ctx(request))


@router.get("/imports", response_class=HTMLResponse)
def imports_page(request: Request) -> HTMLResponse:
    return templates.TemplateResponse(request, "imports.html", _ctx(request))


@router.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request) -> HTMLResponse:
    uniques: dict[str, list[str]] = {"people": [], "vendors": []}
    return templates.TemplateResponse(
        request, "settings.html", _ctx(request, uniques=uniques)
    )
