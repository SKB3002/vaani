"""Sheets disabled path: zero observer wiring, zero side effects."""
from __future__ import annotations

from pathlib import Path

from app.services.ledger import LedgerWriter


def test_ledger_has_no_observers_by_default(tmp_workspace: Path) -> None:
    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    assert ledger._observers == []  # noqa: SLF001


def test_no_sheets_pending_wal_when_disabled(tmp_workspace: Path, ledger: LedgerWriter) -> None:
    # Append a row — no sheets observer registered, so no pending WAL file.
    ledger.append(
        "wishlist",
        {
            "id": "W1",
            "item": "Camera",
            "target_amount": 10000.0,
            "saved_so_far": 0.0,
            "priority": "med",
            "source": "manual",
            "created_at": "2026-04-23T00:00:00Z",
            "status": "active",
        },
    )
    pending = tmp_workspace / ".wal" / "sheets_pending.jsonl"
    assert not pending.exists()
