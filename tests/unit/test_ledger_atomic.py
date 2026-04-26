"""Atomic write + WAL replay tests."""
from __future__ import annotations

from pathlib import Path

import ulid

from app.services.ledger import LedgerWriter
from app.storage.csv_store import read_csv_typed
from app.storage.schemas import SCHEMAS


def _sample_expense() -> dict:
    return {
        "id": str(ulid.new()),
        "date": "2026-04-23",
        "created_at": "2026-04-23T10:00:00+00:00",
        "expense_name": "Test cafe",
        "type_category": "Want, Food & Drinks",
        "payment_method": "paid",
        "paid_for_someone": False,
        "paid_by_someone": False,
        "person_name": None,
        "amount": 250.0,
        "cash_balance_after": 1000.0,
        "online_balance_after": 40000.0,
        "source": "manual",
        "raw_transcript": None,
        "notes": None,
        "import_batch_id": None,
    }


def test_append_writes_row_and_clears_wal(ledger: LedgerWriter, tmp_workspace: Path) -> None:
    row = _sample_expense()
    ledger.append("expenses", row)

    df = ledger.read("expenses")
    assert len(df) == 1
    assert df.iloc[0]["expense_name"] == "Test cafe"

    # WAL should have no pending entries after a clean write
    assert ledger.wal.pending() == []


def test_wal_replay_applies_unfinished_entry(tmp_workspace: Path) -> None:
    """Simulate a crash between WAL append and CSV write: the entry should be replayed."""
    data_dir = tmp_workspace / "data"
    wal_dir = tmp_workspace / ".wal"
    ledger = LedgerWriter(data_dir, wal_dir)

    # Manually write a WAL entry but DO NOT apply the CSV change and DO NOT clear it.
    row = _sample_expense()
    ledger.wal.append("expenses", "append", row)

    # CSV has no data yet
    df_before = read_csv_typed(data_dir / "expenses.csv", SCHEMAS["expenses"])
    assert df_before.empty

    # Replay picks up the pending entry
    replayed = ledger.replay()
    assert replayed == 1

    df_after = ledger.read("expenses")
    assert len(df_after) == 1

    # Second replay should be a no-op (entry cleared)
    assert ledger.replay() == 0


def test_atomic_write_on_crash_midwrite(tmp_workspace: Path, monkeypatch) -> None:
    """Raising mid-write must not corrupt the CSV; WAL still has the entry for replay."""
    data_dir = tmp_workspace / "data"
    wal_dir = tmp_workspace / ".wal"
    ledger = LedgerWriter(data_dir, wal_dir)

    row = _sample_expense()

    # Seed one good row
    ledger.append("expenses", row)

    # Inject failure inside the CSV write step
    import app.storage.csv_store as cs

    real_replace = cs.os.replace

    def boom(*args, **kwargs):
        raise OSError("simulated crash")

    monkeypatch.setattr(cs.os, "replace", boom)

    row2 = _sample_expense()
    try:
        ledger.append("expenses", row2)
    except OSError:
        pass

    # Restore and recover
    monkeypatch.setattr(cs.os, "replace", real_replace)

    # Original CSV should still be readable with just row 1
    df = ledger.read("expenses")
    assert len(df) == 1

    # WAL has a pending entry for row 2 — replay completes it
    pending_before = ledger.wal.pending()
    assert len(pending_before) == 1

    ledger.replay()
    df2 = ledger.read("expenses")
    assert len(df2) == 2


def test_update_and_delete(ledger: LedgerWriter) -> None:
    row = _sample_expense()
    ledger.append("expenses", row)

    updated = ledger.update("expenses", row["id"], {"amount": 999.0})
    assert updated is not None
    assert float(updated["amount"]) == 999.0

    removed = ledger.delete("expenses", row["id"])
    assert removed is True
    assert len(ledger.read("expenses")) == 0
