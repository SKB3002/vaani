"""BalanceService.adjust(): bidirectional balance transfer."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter


def _svc(tmp_workspace: Path) -> BalanceService:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    svc = BalanceService(ledger)
    svc.seed(1000.0, 50000.0)
    return svc


def test_cash_to_online(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    result = svc.adjust(500.0, "cash_to_online")
    assert result == {"cash_balance": 500.0, "online_balance": 50500.0}
    assert svc.current() == result


def test_online_to_cash(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    result = svc.adjust(750.0, "online_to_cash")
    assert result == {"cash_balance": 1750.0, "online_balance": 49250.0}


def test_invalid_direction_raises(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    with pytest.raises(ValueError):
        svc.adjust(100.0, "sideways")


def test_nonpositive_amount_raises(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    with pytest.raises(ValueError):
        svc.adjust(0.0, "cash_to_online")
    with pytest.raises(ValueError):
        svc.adjust(-1.0, "online_to_cash")


def test_adjust_appends_balances_row_with_reason_adjusted(tmp_workspace: Path) -> None:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    svc = BalanceService(ledger)
    svc.seed(1000.0, 50000.0)
    svc.adjust(200.0, "cash_to_online")

    df = ledger.read("balances")
    # seed row + adjust row
    assert len(df) == 2
    last = df.iloc[-1]
    assert last["reason"] == "adjusted"
    assert float(last["cash_balance"]) == 800.0
    assert float(last["online_balance"]) == 50200.0
