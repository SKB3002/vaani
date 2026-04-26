"""Aggregated reports (daily / monthly totals)."""
from __future__ import annotations

from datetime import date
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from app.deps import get_ledger
from app.services.ledger import LedgerWriter
from app.services.tz import today_local

router = APIRouter(prefix="/api/reports", tags=["reports"])


@router.get("/totals")
def totals(
    scope: Literal["daily", "monthly"] = Query("daily"),
    day: date | None = Query(None, description="Local date; defaults to today in user's tz"),
    month: str | None = Query(None, description="YYYY-MM"),
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    df = ledger.read("expenses")
    if df.empty:
        return _empty_response(scope, day, month)

    if scope == "daily":
        target_day = day or today_local()
        mask = df["date"] == target_day.isoformat()
        subset = df[mask]
        return {
            "scope": "daily",
            "date": target_day.isoformat(),
            "total": float(subset["amount"].astype("float64").sum()),
            "count": int(len(subset)),
            "by_type": _split(subset, by="type"),
            "by_category": _split(subset, by="category"),
        }

    target_month = month or today_local().strftime("%Y-%m")
    mask = df["date"].astype("string").str.startswith(target_month)
    subset = df[mask]
    return {
        "scope": "monthly",
        "month": target_month,
        "total": float(subset["amount"].astype("float64").sum()),
        "count": int(len(subset)),
        "by_type": _split(subset, by="type"),
        "by_category": _split(subset, by="category"),
        "by_day": _by_day(subset),
    }


def _split(df: Any, by: Literal["type", "category"]) -> dict[str, float]:
    if df.empty:
        return {}
    idx = 0 if by == "type" else 1
    parts = df["type_category"].astype("string").str.split(", ", n=1, expand=True)
    if parts.shape[1] <= idx:
        return {}
    key = parts[idx]
    grouped = df.assign(_key=key).groupby("_key")["amount"].sum()
    return {str(k): float(v) for k, v in grouped.items() if k is not None}


def _by_day(df: Any) -> dict[str, float]:
    if df.empty:
        return {}
    grouped = df.groupby("date")["amount"].sum()
    return {str(k): float(v) for k, v in grouped.items()}


def _empty_response(scope: str, day: date | None, month: str | None) -> dict[str, Any]:
    if scope == "daily":
        return {
            "scope": "daily",
            "date": (day or today_local()).isoformat(),
            "total": 0.0,
            "count": 0,
            "by_type": {},
            "by_category": {},
        }
    return {
        "scope": "monthly",
        "month": month or today_local().strftime("%Y-%m"),
        "total": 0.0,
        "count": 0,
        "by_type": {},
        "by_category": {},
        "by_day": {},
    }
