"""User-defined column registry — add/rename/delete + validation."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.storage import user_columns


def test_add_column_on_expenses(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    entry = user_columns.add_column(data, "expenses", key="project", label="Project", dtype="string")
    assert entry["key"] == "project"
    assert entry["label"] == "Project"
    assert entry["dtype"] == "string"

    merged = user_columns.resolve_columns(data, "expenses")
    keys = [c["key"] for c in merged]
    assert "project" in keys
    # built-in still present and first
    assert keys[0] == "id"


def test_add_column_key_clash_with_builtin_rejected(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    with pytest.raises(ValueError, match="clashes"):
        user_columns.add_column(data, "expenses", key="amount", label="X", dtype="number")


def test_add_column_key_snake_case_enforced(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    for bad in ["Project", "project name", "1project", "project-name"]:
        with pytest.raises(ValueError):
            user_columns.add_column(data, "expenses", key=bad, label="X", dtype="string")


def test_add_column_invalid_dtype(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    with pytest.raises(ValueError, match="dtype"):
        user_columns.add_column(data, "expenses", key="x", label="X", dtype="json")  # type: ignore[arg-type]


def test_add_column_duplicate_rejected(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    user_columns.add_column(data, "expenses", key="trip_id", label="Trip", dtype="string")
    with pytest.raises(ValueError, match="already exists"):
        user_columns.add_column(data, "expenses", key="trip_id", label="Trip 2", dtype="string")


def test_rename_label_only(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    user_columns.add_column(data, "expenses", key="trip_id", label="Trip", dtype="string")
    updated = user_columns.rename_column(data, "expenses", "trip_id", "Trip ID")
    assert updated["label"] == "Trip ID"
    assert updated["key"] == "trip_id"


def test_delete_column_removes_from_registry(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    user_columns.add_column(data, "expenses", key="trip_id", label="Trip", dtype="string")
    user_columns.delete_column(data, "expenses", "trip_id")
    merged = user_columns.resolve_columns(data, "expenses")
    assert "trip_id" not in [c["key"] for c in merged]


def test_unknown_table_rejected(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    with pytest.raises(KeyError):
        user_columns.add_column(data, "not_a_table", key="x", label="X", dtype="string")


def test_ledger_add_column_backfills_nan(tmp_workspace: Path) -> None:
    from app.services.ledger import LedgerWriter

    ledger = LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")
    # existing row first
    ledger.append(
        "expenses",
        {
            "id": "01ABC",
            "date": "2026-04-20",
            "created_at": "2026-04-20T10:00:00Z",
            "expense_name": "Zomato",
            "type_category": "Want, Food & Drinks",
            "payment_method": "paid",
            "paid_for_someone": False,
            "paid_by_someone": False,
            "person_name": None,
            "amount": 450.0,
            "cash_balance_after": 0.0,
            "online_balance_after": 0.0,
            "source": "manual",
            "raw_transcript": None,
            "notes": None,
            "import_batch_id": None,
        },
    )
    ledger.add_column("expenses", "project", default=None)
    df = ledger.read("expenses")
    assert "project" in df.columns
    assert df["project"].iloc[0] is None or str(df["project"].iloc[0]) in ("nan", "<NA>")


def test_resolve_columns_preserves_order(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    user_columns.add_column(data, "expenses", key="project", label="Project", dtype="string")
    user_columns.add_column(data, "expenses", key="trip_id", label="Trip", dtype="string")
    merged = user_columns.resolve_columns(data, "expenses")
    keys = [c["key"] for c in merged]
    assert keys.index("project") < keys.index("trip_id")
