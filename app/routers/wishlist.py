"""Wishlist CRUD — manual + AI-flagged items with target + saved-so-far.

Key behaviours (§4.4):
- Create with `source="manual"`, `saved_so_far=0`, `status="active"`, ULID id.
- Contribute increments `saved_so_far`. When `source=expense`, also writes
  an expense row with `type_category="Investment, Miscellaneous"` and
  `notes="wishlist:{id}"` so the single-ledger principle (§4.1) holds.
- Delete is soft by default (sets `status="abandoned"`); `?hard=true` deletes.
"""
from __future__ import annotations

from typing import Any, Literal

import ulid
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from app.deps import get_balance_service, get_ledger
from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter
from app.services.tz import now_utc

router = APIRouter(prefix="/api/wishlist", tags=["wishlist"])

Priority = Literal["high", "med", "low"]
StatusFilter = Literal["active", "achieved", "abandoned", "all"]


class WishlistCreateIn(BaseModel):
    item: str = Field(min_length=1, max_length=200)
    target_amount: float = Field(gt=0)
    priority: Priority | None = None
    notes: str | None = None
    link: str | None = None


class WishlistPatchIn(BaseModel):
    item: str | None = Field(default=None, min_length=1, max_length=200)
    target_amount: float | None = Field(default=None, gt=0)
    saved_so_far: float | None = Field(default=None, ge=0)
    priority: Priority | None = None
    notes: str | None = None
    link: str | None = None
    status: Literal["active", "achieved", "abandoned"] | None = None


class WishlistContributeIn(BaseModel):
    amount: float = Field(gt=0)
    source: Literal["expense", "manual"] = "manual"


def _df_to_records(df: Any) -> list[dict[str, Any]]:
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]


def _get_row(ledger: LedgerWriter, wish_id: str) -> dict[str, Any]:
    df = ledger.read("wishlist")
    mask = df["id"].astype("string") == wish_id
    if not mask.any():
        raise HTTPException(404, f"wishlist item '{wish_id}' not found")
    return _df_to_records(df.loc[mask])[0]


@router.get("")
def list_wishlist(
    status: StatusFilter = Query("active"),
    ledger: LedgerWriter = Depends(get_ledger),
) -> list[dict[str, Any]]:
    df = ledger.read("wishlist")
    if df.empty:
        return []
    if status != "all":
        df = df[df["status"].astype("string") == status]
    df = df.sort_values("created_at", ascending=False)
    return _df_to_records(df)


@router.post("", status_code=201)
def create_wishlist(
    payload: WishlistCreateIn,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    row = {
        "id": str(ulid.new()),
        "item": payload.item,
        "target_amount": float(payload.target_amount),
        "saved_so_far": 0.0,
        "priority": payload.priority,
        "notes": payload.notes,
        "link": payload.link,
        "source": "manual",
        "created_at": now_utc().isoformat(),
        "status": "active",
        "import_batch_id": None,
    }
    ledger.append("wishlist", row)
    return row


@router.get("/{wish_id}")
def get_wishlist(
    wish_id: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    return _get_row(ledger, wish_id)


@router.patch("/{wish_id}")
def patch_wishlist(
    wish_id: str,
    patch: WishlistPatchIn,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    current = _get_row(ledger, wish_id)
    updates = patch.model_dump(exclude_unset=True)
    if not updates:
        return current

    merged = {**current, **updates}
    target = float(merged.get("target_amount") or 0.0)
    saved = float(merged.get("saved_so_far") or 0.0)
    if target > 0 and saved >= target and merged.get("status") == "active":
        updates["status"] = "achieved"

    updated = ledger.update("wishlist", wish_id, updates)
    if updated is None:  # pragma: no cover
        raise HTTPException(404, f"wishlist item '{wish_id}' not found")
    return _get_row(ledger, wish_id)


@router.post("/{wish_id}/contribute")
def contribute_wishlist(
    wish_id: str,
    payload: WishlistContributeIn,
    ledger: LedgerWriter = Depends(get_ledger),
    balances: BalanceService = Depends(get_balance_service),
) -> dict[str, Any]:
    current = _get_row(ledger, wish_id)
    amount = float(payload.amount)
    new_saved = float(current.get("saved_so_far") or 0.0) + amount
    target = float(current.get("target_amount") or 0.0)

    updates: dict[str, Any] = {"saved_so_far": new_saved}
    if target > 0 and new_saved >= target and current.get("status") == "active":
        updates["status"] = "achieved"

    result = ledger.update("wishlist", wish_id, updates)
    if result is None:  # pragma: no cover
        raise HTTPException(404, f"wishlist item '{wish_id}' not found")
    updated = _get_row(ledger, wish_id)

    expense_record: dict[str, Any] | None = None
    if payload.source == "expense":
        # Dual-write to expenses.csv per §4.1 single-ledger principle.
        # Payment defaults to 'paid' (online) — wishlist contributions are
        # typically digital.
        cash_after, online_after = balances.snapshot_after_expense("paid", amount)
        expense_record = {
            "id": str(ulid.new()),
            "date": now_utc().date().isoformat(),
            "created_at": now_utc().isoformat(),
            "expense_name": current.get("item") or "wishlist contribution",
            "type_category": "Investment, Miscellaneous",
            "payment_method": "paid",
            "paid_for_someone": False,
            "paid_by_someone": False,
            "person_name": None,
            "amount": amount,
            "cash_balance_after": cash_after,
            "online_balance_after": online_after,
            "source": "manual",
            "raw_transcript": None,
            "notes": f"wishlist:{wish_id}",
            "import_batch_id": None,
        }
        ledger.append("expenses", expense_record)

    return {
        "wishlist": updated,
        "expense": expense_record,
    }


@router.delete("/{wish_id}")
def delete_wishlist(
    wish_id: str,
    hard: bool = Query(False),
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    if hard:
        if not ledger.delete("wishlist", wish_id):
            raise HTTPException(404, f"wishlist item '{wish_id}' not found")
        return {"id": wish_id, "deleted": "hard"}

    _get_row(ledger, wish_id)  # 404 if missing
    result = ledger.update("wishlist", wish_id, {"status": "abandoned"})
    if result is None:  # pragma: no cover
        raise HTTPException(404, f"wishlist item '{wish_id}' not found")
    return {"id": wish_id, "deleted": "soft", "status": "abandoned"}
