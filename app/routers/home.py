"""Home page — renders the dashboard overview with live KPIs."""
from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

from app.config import get_settings
from app.deps import get_balance_service, get_ledger
from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter
from app.services.tz import today_local, user_tz_name

router = APIRouter()
templates = Jinja2Templates(directory="templates")


def _format_inr(v: float | None) -> str | None:
    if v is None:
        return None
    return f"₹{v:,.0f}"


def _greeting_for_hour(hour: int) -> str:
    if hour < 12:
        return "morning"
    if hour < 17:
        return "afternoon"
    return "evening"


@router.get("/", response_class=HTMLResponse)
def home(
    request: Request,
    ledger: LedgerWriter = Depends(get_ledger),
    balances: BalanceService = Depends(get_balance_service),
) -> HTMLResponse:
    settings = get_settings()
    today = today_local()
    month_prefix = today.strftime("%Y-%m")

    df = ledger.read("expenses")
    today_total = 0.0
    month_total = 0.0
    recent: list[dict[str, Any]] = []

    if not df.empty:
        try:
            today_total = float(df[df["date"] == today.isoformat()]["amount"].astype("float64").sum())
            month_total = float(
                df[df["date"].astype("string").str.startswith(month_prefix)]["amount"]
                .astype("float64")
                .sum()
            )
            recent_df = df.sort_values(
                ["date", "created_at"], ascending=[False, False]
            ).head(5)
            for _, r in recent_df.iterrows():
                amount_val = float(r["amount"]) if r["amount"] == r["amount"] else 0.0  # NaN check
                recent.append(
                    {
                        "date": r["date"],
                        "expense_name": r["expense_name"],
                        "type_category": r["type_category"] if r["type_category"] == r["type_category"] else None,
                        "payment_method": r["payment_method"] if r["payment_method"] == r["payment_method"] else None,
                        "amount": amount_val,
                        "amount_display": _format_inr(amount_val),
                    }
                )
        except (KeyError, ValueError):
            pass

    current = balances.current() or {}
    kpis = {
        "today": today_total,
        "today_display": _format_inr(today_total),
        "month": month_total,
        "month_display": _format_inr(month_total),
        "cash": current.get("cash_balance"),
        "cash_display": _format_inr(current.get("cash_balance")),
        "online": current.get("online_balance"),
        "online_display": _format_inr(current.get("online_balance")),
    }

    return templates.TemplateResponse(
        request,
        "index.html",
        {
            "currency": settings.default_currency,
            "timezone": user_tz_name(),
            "today_str": today.isoformat(),
            "greeting": _greeting_for_hour(datetime.now().hour),
            "kpis": kpis,
            "recent_expenses": recent,
        },
    )
