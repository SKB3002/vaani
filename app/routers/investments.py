"""Investments — monthly planning grid with upsert-by-month semantics.

Per §4.3: `investments.csv` is a monthly aggregate table, not a transaction
ledger. One row per `YYYY-MM`. `total` is computed as the sum of all numeric
columns except `total`, `month`, and `import_batch_id`. User-defined columns
(added via the universal `/api/tables/investments/columns` registry) are
included in the total automatically.
"""
from __future__ import annotations

import math
import re
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, field_validator

from app.config import get_settings
from app.deps import get_ledger
from app.services.ledger import LedgerWriter
from app.storage import user_columns
from app.storage.schemas import INVESTMENTS

router = APIRouter(prefix="/api/investments", tags=["investments"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")

# Columns excluded from the computed `total`.
_TOTAL_EXCLUDED = {"total", "month", "import_batch_id"}


class InvestmentUpsertIn(BaseModel):
    """POST body: `{month, ...values}`. Extra keys allowed for user columns."""

    model_config = {"extra": "allow"}

    month: str = Field(min_length=7, max_length=7)

    @field_validator("month")
    @classmethod
    def _valid_month(cls, v: str) -> str:
        if not _MONTH_RE.match(v):
            raise ValueError("month must be YYYY-MM")
        return v


class InvestmentPatchIn(BaseModel):
    """PATCH body: partial values only. Extra keys allowed for user columns."""

    model_config = {"extra": "allow"}


def _numeric_columns(df: pd.DataFrame) -> list[str]:
    return [c for c in df.columns if c not in _TOTAL_EXCLUDED]


def _row_numeric_total(row: dict[str, Any], numeric_cols: list[str]) -> float:
    total = 0.0
    for col in numeric_cols:
        v = row.get(col)
        if v is None:
            continue
        try:
            f = float(v)
        except (TypeError, ValueError):
            continue
        if math.isnan(f):
            continue
        total += f
    return total


def _all_numeric_keys(ledger: LedgerWriter) -> list[str]:
    """Built-in + user-defined columns that participate in `total`.

    Only columns with numeric dtype are included. User columns of dtype
    ``string`` / ``boolean`` / ``date`` are ignored for total math.
    """
    data_dir = get_settings().resolved_data_dir()
    builtins = [c for c in INVESTMENTS["columns"] if c not in _TOTAL_EXCLUDED]
    user_numeric = [
        c["key"]
        for c in user_columns.list_user_columns(data_dir, "investments")
        if c.get("dtype") == "number"
    ]
    seen: set[str] = set()
    out: list[str] = []
    for col in [*builtins, *user_numeric]:
        if col not in seen and col not in _TOTAL_EXCLUDED:
            seen.add(col)
            out.append(col)
    return out


def _df_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]


@router.get("")
def list_investments(
    ledger: LedgerWriter = Depends(get_ledger),
) -> list[dict[str, Any]]:
    df = ledger.read("investments")
    if df.empty:
        return []
    df = df.sort_values("month", ascending=False)
    return _df_to_records(df)


@router.get("/summary")
def investments_summary(
    year: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    if not re.match(r"^\d{4}$", year):
        raise HTTPException(400, "year must be YYYY")
    df = ledger.read("investments")
    if df.empty:
        return {"year": year, "totals": {}, "grand_total": 0.0, "months": 0}

    mask = df["month"].astype("string").str.startswith(year)
    subset = df.loc[mask]
    if subset.empty:
        return {"year": year, "totals": {}, "grand_total": 0.0, "months": 0}

    numeric_cols = _numeric_columns(subset)
    totals: dict[str, float] = {}
    for col in numeric_cols:
        if col == "total":
            continue
        series = pd.to_numeric(subset[col], errors="coerce").fillna(0.0)
        totals[col] = float(series.sum())
    grand_total = float(sum(totals.values()))
    return {
        "year": year,
        "totals": totals,
        "grand_total": grand_total,
        "months": int(len(subset)),
    }


@router.get("/{month}")
def get_investment(
    month: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    if not _MONTH_RE.match(month):
        raise HTTPException(400, "month must be YYYY-MM")
    df = ledger.read("investments")
    mask = df["month"].astype("string") == month
    if not mask.any():
        raise HTTPException(404, f"no investment row for {month}")
    return _df_to_records(df.loc[mask])[0]


@router.post("", status_code=201)
def upsert_investment(
    payload: InvestmentUpsertIn,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    body = payload.model_dump()
    month = body.pop("month")

    numeric_keys = _all_numeric_keys(ledger)
    row: dict[str, Any] = {"month": month, "import_batch_id": None}
    for key in numeric_keys:
        row[key] = body.get(key)
    # forward any other extras (e.g., newly registered user columns)
    for key, value in body.items():
        if key not in row:
            row[key] = value
    row["total"] = _row_numeric_total(row, numeric_keys)

    df = ledger.read("investments")
    existing = df["month"].astype("string") == month
    if existing.any():
        updates = {k: v for k, v in row.items() if k != "month"}
        updated = ledger.update("investments", month, updates)
        if updated is None:  # pragma: no cover — existence checked above
            raise HTTPException(500, "upsert failed")
        # Re-read to return a clean NaN-free record.
        df2 = ledger.read("investments")
        return _df_to_records(df2.loc[df2["month"].astype("string") == month])[0]
    ledger.append("investments", row)
    df2 = ledger.read("investments")
    return _df_to_records(df2.loc[df2["month"].astype("string") == month])[0]


@router.patch("/{month}")
def patch_investment(
    month: str,
    patch: InvestmentPatchIn,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    if not _MONTH_RE.match(month):
        raise HTTPException(400, "month must be YYYY-MM")
    df = ledger.read("investments")
    mask = df["month"].astype("string") == month
    if not mask.any():
        raise HTTPException(404, f"no investment row for {month}")

    current: dict[str, Any] = df.loc[mask].iloc[0].to_dict()
    updates = patch.model_dump(exclude_unset=True)
    updates.pop("month", None)
    if not updates:
        return _df_to_records(df.loc[mask])[0]

    merged = {**current, **updates}
    numeric_keys = _all_numeric_keys(ledger)
    merged["total"] = _row_numeric_total(merged, numeric_keys)
    updates["total"] = merged["total"]

    updated = ledger.update("investments", month, updates)
    if updated is None:  # pragma: no cover
        raise HTTPException(500, "patch failed")
    df2 = ledger.read("investments")
    return _df_to_records(df2.loc[df2["month"].astype("string") == month])[0]


@router.delete("/{month}", status_code=204)
def delete_investment(
    month: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> None:
    if not _MONTH_RE.match(month):
        raise HTTPException(400, "month must be YYYY-MM")
    if not ledger.delete("investments", month):
        raise HTTPException(404, f"no investment row for {month}")
