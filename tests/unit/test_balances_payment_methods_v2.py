"""BalanceService coverage for all 5 payment_method branches (v2)."""
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


def test_paid_decrements_online(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    cash, online = svc.snapshot_after_expense("paid", 250.0)
    assert cash == 1000.0
    assert online == 49750.0


def test_paid_cash_decrements_cash(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    cash, online = svc.snapshot_after_expense("paid_cash", 300.0)
    assert cash == 700.0
    assert online == 50000.0


def test_paid_by_no_change(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    cash, online = svc.snapshot_after_expense("paid_by", 500.0)
    assert cash == 1000.0
    assert online == 50000.0


def test_paid_for_cash(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    cash, online = svc.snapshot_after_expense(
        "paid_for", 200.0, paid_for_method="cash"
    )
    assert cash == 800.0
    assert online == 50000.0


def test_paid_for_online(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    cash, online = svc.snapshot_after_expense(
        "paid_for", 200.0, paid_for_method="online"
    )
    assert cash == 1000.0
    assert online == 49800.0


def test_paid_for_defaults_to_online_when_method_null(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    cash, online = svc.snapshot_after_expense("paid_for", 150.0, paid_for_method=None)
    assert cash == 1000.0
    assert online == 49850.0


def test_adjusted_bypasses_snapshot_after_expense(tmp_workspace: Path) -> None:
    svc = _svc(tmp_workspace)
    with pytest.raises(ValueError, match="adjusted"):
        svc.snapshot_after_expense("adjusted", 500.0)
