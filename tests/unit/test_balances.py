"""Balance service: seed → expense decrements → ATM transfer."""
from __future__ import annotations

from pathlib import Path

from app.services.balances import BalanceService
from app.services.ledger import LedgerWriter


def test_seed_and_current(tmp_workspace: Path) -> None:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    svc = BalanceService(ledger)

    assert svc.current() is None
    svc.seed(1000.0, 50000.0)
    assert svc.current() == {"cash_balance": 1000.0, "online_balance": 50000.0}


def test_snapshot_after_expense_cash(tmp_workspace: Path) -> None:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    svc = BalanceService(ledger)
    svc.seed(1000.0, 50000.0)

    cash, online = svc.snapshot_after_expense("paid_cash", 300.0)
    assert cash == 700.0
    assert online == 50000.0

    current = svc.current()
    assert current == {"cash_balance": 700.0, "online_balance": 50000.0}


def test_snapshot_after_expense_paid(tmp_workspace: Path) -> None:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    svc = BalanceService(ledger)
    svc.seed(1000.0, 50000.0)

    svc.snapshot_after_expense("paid", 2500.0)
    assert svc.current() == {"cash_balance": 1000.0, "online_balance": 47500.0}


def test_atm_transfer_moves_online_to_cash(tmp_workspace: Path) -> None:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    svc = BalanceService(ledger)
    svc.seed(500.0, 10000.0)

    result = svc.atm_transfer(2000.0)
    assert result == {"cash_balance": 2500.0, "online_balance": 8000.0}
    assert svc.current() == {"cash_balance": 2500.0, "online_balance": 8000.0}
