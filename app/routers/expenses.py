"""Expense CRUD endpoints."""
from __future__ import annotations

from datetime import date
from typing import Any

import ulid
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_balance_service, get_ledger
from app.models.expense import ExpenseIn, ExpenseUpdate
from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter
from app.services.tz import now_utc

router = APIRouter(prefix="/api/expenses", tags=["expenses"])


def _expense_to_record(data: ExpenseIn, cash_after: float, online_after: float) -> dict[str, Any]:
    return {
        "id": str(ulid.new()),
        "date": data.date.isoformat(),
        "created_at": now_utc().isoformat(),
        "expense_name": data.expense_name,
        "type_category": data.type_category,
        "payment_method": data.payment_method.value,
        "paid_for_someone": data.paid_for_someone,
        "paid_by_someone": data.paid_by_someone,
        "person_name": data.person_name,
        "amount": float(data.amount),
        "cash_balance_after": cash_after,
        "online_balance_after": online_after,
        "source": data.source,
        "raw_transcript": data.raw_transcript,
        "notes": data.notes,
        "import_batch_id": None,
        "custom_tag": data.custom_tag,
        "paid_for_method": data.paid_for_method,
        "adjustment_type": data.adjustment_type,
    }


@router.get("")
def list_expenses(
    date_from: date | None = Query(None),
    date_to: date | None = Query(None),
    limit: int = Query(500, ge=1, le=10000),
    ledger: LedgerWriter = Depends(get_ledger),
) -> list[dict[str, Any]]:
    df = ledger.read("expenses")
    if df.empty:
        return []
    if date_from is not None:
        df = df[df["date"] >= date_from.isoformat()]
    if date_to is not None:
        df = df[df["date"] <= date_to.isoformat()]
    df = df.sort_values(["date", "created_at"], ascending=[False, False]).head(limit)
    return _df_to_records(df)


@router.post("", status_code=201)
def create_expense(
    payload: ExpenseIn,
    ledger: LedgerWriter = Depends(get_ledger),
    balances: BalanceService = Depends(get_balance_service),
) -> dict[str, Any]:
    """Create an expense, or (when payment_method='adjusted') a balance transfer.

    For payment_method in {paid, paid_cash, paid_by, paid_for}: snapshots the
    balance, appends a row to expenses.csv, and returns the record.

    For payment_method='adjusted': does NOT write an expense row. Instead calls
    BalanceService.adjust() which mutates balances.csv with reason='adjusted'.
    Returns {"type": "adjustment", "balances": {...}}.
    """
    pm = payload.payment_method.value

    if pm == "adjusted":
        assert payload.adjustment_type is not None  # enforced by ExpenseIn validator
        new_balances = balances.adjust(float(payload.amount), payload.adjustment_type)
        return {
            "type": "adjustment",
            "adjustment_type": payload.adjustment_type,
            "amount": float(payload.amount),
            "balances": new_balances,
        }

    cash_after, online_after = balances.snapshot_after_expense(
        pm,
        float(payload.amount),
        paid_for_method=payload.paid_for_method,
    )
    record = _expense_to_record(payload, cash_after, online_after)
    ledger.append("expenses", record)
    return record


@router.get("/{expense_id}")
def get_expense(
    expense_id: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    df = ledger.read("expenses")
    if df.empty:
        raise HTTPException(404, "expense not found")
    mask = df["id"].astype("string") == str(expense_id)
    if not mask.any():
        raise HTTPException(404, "expense not found")
    return _df_to_records(df.loc[mask])[0]


@router.patch("/{expense_id}")
def update_expense(
    expense_id: str,
    patch: ExpenseUpdate,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    for key, value in patch.model_dump(exclude_unset=True).items():
        if key == "date" and value is not None:
            updates["date"] = value.isoformat() if isinstance(value, date) else value
        elif key == "payment_method" and value is not None:
            updates[key] = value.value if hasattr(value, "value") else value
        else:
            updates[key] = value
    result = ledger.update("expenses", expense_id, updates)
    if result is None:
        raise HTTPException(404, "expense not found")
    return result


@router.delete("/{expense_id}", status_code=204)
def delete_expense(
    expense_id: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> None:
    if not ledger.delete("expenses", expense_id):
        raise HTTPException(404, "expense not found")


def _df_to_records(df: Any) -> list[dict[str, Any]]:
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]
