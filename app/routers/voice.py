"""Voice / LLM-assisted expense parse endpoints."""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any, Literal

import ulid
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps import get_balance_service, get_ledger, get_llm_client
from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter
from app.services.llm import (
    LLMClient,
    LLMParseError,
    LLMTransportError,
    ParseContext,
)
from app.services.tz import now_utc, today_local

router = APIRouter(prefix="/api", tags=["voice"])

_UNIQUES_LOCK = threading.Lock()


# ---------- request / response schemas ----------

class ParseRequest(BaseModel):
    transcript: str = Field(min_length=1, max_length=2000)


class TeachRequest(BaseModel):
    surface: str = Field(min_length=1, max_length=200)
    vendor: str | None = Field(default=None, max_length=200)
    type_category: str | None = Field(default=None, max_length=80)


class ConfirmItem(BaseModel):
    expense_name: str
    type_category: str
    payment_method: str
    amount: float
    date: str                                   # ISO "YYYY-MM-DD"
    paid_for_someone: bool = False
    paid_by_someone: bool = False
    person_name: str | None = None
    paid_for_method: Literal["cash", "online"] | None = None
    adjustment_type: Literal["cash_to_online", "online_to_cash"] | None = None


class ConfirmRequest(BaseModel):
    action: Literal["expense", "atm_transfer"] = "expense"
    items: list[ConfirmItem] = Field(default_factory=list)
    atm_amount: float | None = None
    raw_transcript: str | None = None


# ---------- uniques.json helpers ----------

def _uniques_path() -> Path:
    return get_settings().resolved_data_dir() / "uniques.json"


def _load_uniques() -> dict[str, Any]:
    path = _uniques_path()
    if not path.exists():
        return {"vendors": {}, "aliases": {}, "people": []}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {"vendors": {}, "aliases": {}, "people": []}
    data.setdefault("vendors", {})
    data.setdefault("aliases", {})
    data.setdefault("people", [])
    return data  # type: ignore[no-any-return]


def _save_uniques(data: dict[str, Any]) -> None:
    path = _uniques_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(path)


def _meta_currency() -> str:
    meta_path = get_settings().resolved_data_dir() / "meta.json"
    if meta_path.exists():
        try:
            return str(
                json.loads(meta_path.read_text(encoding="utf-8")).get("currency", "INR")
            )
        except json.JSONDecodeError:
            pass
    return "INR"


# ---------- endpoints ----------

@router.get("/uniques")
def get_uniques() -> dict[str, Any]:
    return _load_uniques()


@router.post("/uniques/teach")
def teach_uniques(payload: TeachRequest) -> dict[str, Any]:
    surface = payload.surface.strip().lower()
    with _UNIQUES_LOCK:
        data = _load_uniques()
        vendors: dict[str, Any] = data.setdefault("vendors", {})
        aliases: dict[str, Any] = data.setdefault("aliases", {})

        canonical = (payload.vendor or surface).strip()
        if payload.type_category:
            if ", " in payload.type_category:
                prefix, _, category = payload.type_category.partition(", ")
            else:
                prefix, _, category = payload.type_category.partition(":")
            if prefix and category:
                vendors[canonical.lower()] = {
                    "category": category,
                    "type": prefix,
                }
        else:
            vendors.setdefault(canonical.lower(), {})

        if payload.vendor and surface != canonical.lower():
            aliases[surface] = canonical

        _save_uniques(data)
    return data


@router.post("/expense/parse")
async def parse_expense(
    payload: ParseRequest,
    balances: BalanceService = Depends(get_balance_service),
    llm: LLMClient = Depends(get_llm_client),
) -> dict[str, Any]:
    """Parse transcript via LLM — returns preview only, DOES NOT save."""
    uniques = _load_uniques()
    current_balances = balances.current() or {"cash_balance": 0.0, "online_balance": 0.0}
    ctx = ParseContext(
        today=today_local(),
        currency=_meta_currency(),
        uniques=uniques,
        last_known_balances={
            "cash": current_balances["cash_balance"],
            "online": current_balances["online_balance"],
        },
    )

    try:
        parsed = await llm.parse_expense(payload.transcript, ctx)
    except LLMTransportError as exc:
        raise HTTPException(503, detail=f"LLM transport error: {exc}",
                            headers={"Retry-After": "5"}) from exc
    except LLMParseError as exc:
        raise HTTPException(422, detail={
            "error": "llm_parse_failed",
            "raw_transcript": exc.transcript,
            "detail": str(exc),
            "raw": exc.raw,
        }) from exc
    except NotImplementedError as exc:
        raise HTTPException(503, detail="LLM client not configured (set GROQ_API_KEY).") from exc

    # Top-level clarify
    if parsed.action == "clarify" or parsed.needs_clarification:
        return {
            "status": "clarify",
            "question": parsed.question or "Could you clarify?",
            "confidence": parsed.confidence,
        }

    # ATM transfer — preview only (confirm will execute)
    if parsed.action == "atm_transfer":
        if not parsed.atm_amount or parsed.atm_amount <= 0:
            return {
                "status": "clarify",
                "question": "How much did you withdraw from the ATM?",
                "confidence": parsed.confidence,
            }
        from app.services.tz import user_tz_name
        return {
            "status": "preview",
            "action": "atm_transfer",
            "date": parsed.date.isoformat(),
            "timezone": user_tz_name(),
            "atm_amount": float(parsed.atm_amount),
            "confidence": parsed.confidence,
        }

    # Expense preview
    if not parsed.items:
        return {
            "status": "clarify",
            "question": "I couldn't parse any expense. Please rephrase.",
            "confidence": parsed.confidence,
        }

    preview_items = []
    clarify_items = []
    for item in parsed.items:
        if item.needs_clarification:
            clarify_items.append({
                "question": item.question or "Could you clarify this item?",
                "partial": item.model_dump(mode="json"),
            })
            continue
        preview_items.append({
            "expense_name": item.expense_name or "",
            "type_category": item.type_category or "",
            "payment_method": item.payment_method or "paid",
            "amount": item.amount or 0.0,
            "date": parsed.date.isoformat(),
            "paid_for_someone": item.paid_for_someone,
            "paid_by_someone": item.paid_by_someone,
            "person_name": item.person_name,
            "paid_for_method": item.paid_for_method,
            "adjustment_type": item.adjustment_type,
        })

    if not preview_items and clarify_items:
        return {
            "status": "clarify",
            "question": clarify_items[0]["question"],
            "confidence": parsed.confidence,
        }

    from app.services.tz import user_tz_name  # local import — avoids circular at module level
    return {
        "status": "preview",
        "action": "expense",
        "date": parsed.date.isoformat(),
        "timezone": user_tz_name(),
        "items": preview_items,
        "clarify_items": clarify_items,
        "confidence": parsed.confidence,
        "raw_transcript": payload.transcript,
    }


@router.post("/expense/confirm")
def confirm_expenses(
    payload: ConfirmRequest,
    ledger: LedgerWriter = Depends(get_ledger),
    balances: BalanceService = Depends(get_balance_service),
) -> dict[str, Any]:
    """Save the (user-reviewed) expense items returned by /expense/parse."""

    # ATM transfer
    if payload.action == "atm_transfer":
        if not payload.atm_amount or payload.atm_amount <= 0:
            raise HTTPException(422, detail="atm_amount required for atm_transfer")
        new_balances = balances.atm_transfer(float(payload.atm_amount))
        return {
            "status": "atm_transfer",
            "balances": new_balances,
        }

    # Expense items
    inserted_rows: list[dict[str, Any]] = []
    for item in payload.items:
        pm = item.payment_method
        paid_for_method = item.paid_for_method if pm == "paid_for" else None
        if pm == "adjusted":
            adj_type = item.adjustment_type or "cash_to_online"
            amt = float(item.amount)
            balances.adjust(amt, adj_type)
            continue

        cash_after, online_after = balances.snapshot_after_expense(
            pm, float(item.amount), paid_for_method=paid_for_method
        )
        row: dict[str, Any] = {
            "id": str(ulid.new()),
            "date": item.date,
            "created_at": now_utc().isoformat(),
            "expense_name": item.expense_name,
            "type_category": item.type_category,
            "payment_method": pm,
            "paid_for_someone": item.paid_for_someone,
            "paid_by_someone": item.paid_by_someone,
            "person_name": item.person_name,
            "amount": float(item.amount),
            "cash_balance_after": cash_after,
            "online_balance_after": online_after,
            "source": "voice",
            "raw_transcript": payload.raw_transcript,
            "notes": None,
            "import_batch_id": None,
            "paid_for_method": paid_for_method,
            "adjustment_type": None,
        }
        ledger.append("expenses", row)
        inserted_rows.append(row)

    final_balances = balances.current() or {"cash_balance": 0.0, "online_balance": 0.0}
    return {
        "status": "inserted",
        "count": len(inserted_rows),
        "rows": inserted_rows,
        "balances": final_balances,
    }
