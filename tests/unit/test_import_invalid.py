"""on_invalid behavior: skip / abort / draft."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from app.services.imports import committer
from app.services.ledger import LedgerWriter


def _mixed_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": ["2026-04-20", "not-a-date", "2026-04-22"],
            "expense_name": ["Zomato", "Broken", "HPCL"],
            "type_category": ["Want, Food & Drinks", "invalid", "Need, Travel"],
            "payment_method": ["paid", "paid", "cash"],
            "amount": [450.0, 100.0, 800.0],
        }
    )


def test_skip_invalid(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    ledger = LedgerWriter(data_dir, tmp_workspace / ".wal")
    mapping = {c: c for c in _mixed_df().columns}

    outcome = committer.dry_run(_mixed_df(), "expenses", mapping, None, data_dir)
    assert len(outcome.rows) == 2
    assert len(outcome.errors) == 1

    counts = committer.commit(outcome, "expenses", "skip", "B1", ledger, data_dir)
    assert counts["inserted"] == 2
    assert counts["errors"] == 1
    assert counts["drafted"] == 0


def test_abort_on_invalid(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    ledger = LedgerWriter(data_dir, tmp_workspace / ".wal")
    mapping = {c: c for c in _mixed_df().columns}

    outcome = committer.dry_run(_mixed_df(), "expenses", mapping, None, data_dir)
    with pytest.raises(ValueError):
        committer.commit(outcome, "expenses", "abort", "B2", ledger, data_dir)
    # Nothing should have been inserted
    assert ledger.read("expenses").empty


def test_draft_invalid(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    ledger = LedgerWriter(data_dir, tmp_workspace / ".wal")
    mapping = {c: c for c in _mixed_df().columns}

    outcome = committer.dry_run(_mixed_df(), "expenses", mapping, None, data_dir)
    counts = committer.commit(outcome, "expenses", "draft", "B3", ledger, data_dir)
    assert counts["inserted"] == 2
    assert counts["drafted"] == 1

    drafts = ledger.read("drafts")
    assert len(drafts) == 1
    assert drafts.iloc[0]["target_table"] == "expenses"
