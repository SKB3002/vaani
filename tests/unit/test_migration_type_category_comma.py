"""One-time migration: 'Type:Category' -> 'Type, Category' on bootstrap."""
from __future__ import annotations

import csv
from pathlib import Path

import pytest

from app.bootstrap import _migrate_type_category_to_comma, bootstrap_for


def _write_expenses(
    data_dir: Path, rows: list[dict[str, str]], columns: list[str] | None = None
) -> None:
    path = data_dir / "expenses.csv"
    cols = columns or ["id", "type_category", "amount"]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=cols)
        writer.writeheader()
        for row in rows:
            writer.writerow({c: row.get(c, "") for c in cols})


def test_migration_rewrites_legacy_rows(tmp_path: Path) -> None:
    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp_dir = tmp_path / ".tmp"
    data.mkdir()
    wal.mkdir()
    tmp_dir.mkdir()

    # Seed BEFORE bootstrap_for: mix of legacy + new-format + empty rows.
    _write_expenses(
        data,
        [
            {"id": "1", "type_category": "Need:Food & Drinks", "amount": "100"},
            {"id": "2", "type_category": "Want:Travel", "amount": "200"},
            {"id": "3", "type_category": "Investment:Miscellaneous", "amount": "300"},
            {"id": "4", "type_category": "Need, Food & Drinks", "amount": "50"},   # already new
            {"id": "5", "type_category": "", "amount": "75"},                       # empty
        ],
    )

    bootstrap_for(data, wal, tmp_dir)

    # Marker created
    marker = data / ".migrated_type_category_comma"
    assert marker.exists()

    # Verify rewrites
    with (data / "expenses.csv").open(encoding="utf-8") as f:
        out_rows = list(csv.DictReader(f))
    by_id = {r["id"]: r["type_category"] for r in out_rows}
    assert by_id["1"] == "Need, Food & Drinks"
    assert by_id["2"] == "Want, Travel"
    assert by_id["3"] == "Investment, Miscellaneous"
    assert by_id["4"] == "Need, Food & Drinks"  # untouched
    assert by_id["5"] == ""                      # untouched


def test_migration_idempotent_second_run_is_noop(tmp_path: Path) -> None:
    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp_dir = tmp_path / ".tmp"
    for d in (data, wal, tmp_dir):
        d.mkdir()

    _write_expenses(
        data,
        [{"id": "1", "type_category": "Need:Travel", "amount": "100"}],
    )

    bootstrap_for(data, wal, tmp_dir)
    marker = data / ".migrated_type_category_comma"
    assert marker.exists()
    first_mtime = marker.stat().st_mtime_ns

    # Write a BAD legacy row manually AFTER the marker is placed.
    # Second run must NOT re-scan (marker present).
    _write_expenses(
        data,
        [{"id": "1", "type_category": "Need:Travel", "amount": "100"}],
    )
    _migrate_type_category_to_comma(data)

    with (data / "expenses.csv").open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))
    # Untouched because marker exists
    assert rows[0]["type_category"] == "Need:Travel"
    # Marker file not rewritten
    assert marker.stat().st_mtime_ns == first_mtime


def test_migration_safe_on_empty_data(tmp_path: Path) -> None:
    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp_dir = tmp_path / ".tmp"
    for d in (data, wal, tmp_dir):
        d.mkdir()

    # Fresh bootstrap creates empty expenses.csv (headers only)
    bootstrap_for(data, wal, tmp_dir)
    marker = data / ".migrated_type_category_comma"
    assert marker.exists()


def test_migration_marker_reports_rewrite_count(tmp_path: Path) -> None:
    import json

    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp_dir = tmp_path / ".tmp"
    for d in (data, wal, tmp_dir):
        d.mkdir()

    _write_expenses(
        data,
        [
            {"id": "1", "type_category": "Need:Food & Drinks", "amount": "100"},
            {"id": "2", "type_category": "Want:Enjoyment", "amount": "200"},
            {"id": "3", "type_category": "Need, Travel", "amount": "300"},  # already new
        ],
    )

    bootstrap_for(data, wal, tmp_dir)
    marker = data / ".migrated_type_category_comma"
    payload = json.loads(marker.read_text(encoding="utf-8"))
    assert payload["rewritten"] == 2
    assert payload["format"] == "Type, Category"


@pytest.mark.parametrize(
    "bad_value",
    ["", "plain text", "Something:else", "Need:Unknown", "Unknown:Travel"],
)
def test_migration_leaves_invalid_values_alone(tmp_path: Path, bad_value: str) -> None:
    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp_dir = tmp_path / ".tmp"
    for d in (data, wal, tmp_dir):
        d.mkdir()

    _write_expenses(
        data,
        [{"id": "1", "type_category": bad_value, "amount": "100"}],
    )

    bootstrap_for(data, wal, tmp_dir)
    with (data / "expenses.csv").open(encoding="utf-8") as f:
        row = next(csv.DictReader(f))
    assert row["type_category"] == bad_value
