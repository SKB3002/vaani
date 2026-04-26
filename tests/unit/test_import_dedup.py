"""Dedup test — same rows imported twice should not double-insert."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.imports import committer
from app.services.ledger import LedgerWriter


def _df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-04-20", "2026-04-21"],
            "expense_name": ["Zomato", "HPCL"],
            "type_category": ["Want, Food & Drinks", "Need, Travel"],
            "payment_method": ["paid", "cash"],
            "amount": [450.0, 800.0],
        }
    )


def test_first_commit_inserts_second_commit_dedups(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    ledger = LedgerWriter(data_dir, tmp_workspace / ".wal")
    mapping = {c: c for c in _df().columns}

    # first commit
    outcome1 = committer.dry_run(_df(), "expenses", mapping, None, data_dir)
    assert len(outcome1.rows) == 2
    assert len(outcome1.duplicates) == 0

    result1 = committer.commit(
        outcome1,
        target_table="expenses",
        on_invalid="skip",
        batch_id="BATCH1",
        ledger=ledger,
        data_dir=data_dir,
    )
    assert result1["inserted"] == 2

    # second commit — same data
    outcome2 = committer.dry_run(_df(), "expenses", mapping, None, data_dir)
    assert len(outcome2.rows) == 0
    assert len(outcome2.duplicates) == 2

    result2 = committer.commit(
        outcome2,
        target_table="expenses",
        on_invalid="skip",
        batch_id="BATCH2",
        ledger=ledger,
        data_dir=data_dir,
    )
    assert result2["inserted"] == 0
    assert result2["duplicates"] == 2

    # Only 2 rows in the ledger
    df = ledger.read("expenses")
    assert len(df) == 2


def test_rollback_batch_removes_rows(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    ledger = LedgerWriter(data_dir, tmp_workspace / ".wal")
    mapping = {c: c for c in _df().columns}

    outcome = committer.dry_run(_df(), "expenses", mapping, None, data_dir)
    committer.commit(
        outcome, "expenses", "skip", "BATCH_X", ledger, data_dir
    )
    committer.write_batch_meta(
        data_dir, "BATCH_X", "test.csv", "abc", None, "expenses", mapping,
        {"total": 2, "inserted": 2, "duplicates": 0, "drafted": 0, "errors": 0}
    )

    result = committer.rollback_batch(ledger, data_dir, "BATCH_X")
    assert result["removed"] == 2
    assert ledger.read("expenses").empty
