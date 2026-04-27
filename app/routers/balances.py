"""Balance endpoints."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from app.deps import get_balance_service, get_ledger
from app.models.balance import AtmTransferIn, BalanceSeedIn
from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter

router = APIRouter(prefix="/api/balances", tags=["balances"])


@router.get("/current")
def current_balance(balances: BalanceService = Depends(get_balance_service)) -> dict[str, float]:
    current = balances.current()
    if current is None:
        raise HTTPException(404, "no balance seeded yet")
    return current


@router.get("")
def list_balances(ledger: LedgerWriter = Depends(get_ledger)) -> list[dict[str, Any]]:
    df = ledger.read("balances")
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]


@router.post("", status_code=201)
def seed_balance(
    payload: BalanceSeedIn,
    balances: BalanceService = Depends(get_balance_service),
) -> dict[str, float]:
    return balances.seed(payload.cash_balance, payload.online_balance, payload.reason, payload.mode)


@router.post("/atm-transfer", status_code=201)
def atm_transfer(
    payload: AtmTransferIn,
    balances: BalanceService = Depends(get_balance_service),
) -> dict[str, float]:
    return balances.atm_transfer(float(payload.amount))
