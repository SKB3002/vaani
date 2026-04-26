"""Idempotent payment_method v2 migration — 4 buckets + marker + re-run."""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from app.bootstrap import _migrate_payment_method_v2, bootstrap_for


def _write_expenses(data_dir: Path, rows: list[dict]) -> Path:
    path = data_dir / "expenses.csv"
    pd.DataFrame(rows).to_csv(path, index=False)
    return path


def _fresh_dirs(tmp_path: Path) -> Path:
    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp = tmp_path / ".tmp"
    for d in (data, wal, tmp):
        d.mkdir()
    bootstrap_for(data, wal, tmp)
    # bootstrap_for already ran the migration on empty data; remove marker so
    # we can re-run it against seeded rows.
    marker = data / ".migrated_payment_method_v2"
    if marker.exists():
        marker.unlink()
    return data


def test_all_four_buckets(tmp_path: Path) -> None:
    data_dir = _fresh_dirs(tmp_path)
    _write_expenses(
        data_dir,
        [
            # paid_by: paid_by_someone=True dominates old payment_method
            {"id": "1", "date": "2026-04-01", "payment_method": "paid",
             "paid_by_someone": "True", "paid_for_someone": "False",
             "amount": 100.0, "expense_name": "A", "type_category": "Want, Enjoyment"},
            # paid_for with old cash -> paid_for + paid_for_method=cash
            {"id": "2", "date": "2026-04-02", "payment_method": "cash",
             "paid_by_someone": "False", "paid_for_someone": "True",
             "amount": 200.0, "expense_name": "B", "type_category": "Need, Food & Drinks"},
            # paid_for with old paid -> paid_for + paid_for_method=online
            {"id": "3", "date": "2026-04-03", "payment_method": "paid",
             "paid_by_someone": "False", "paid_for_someone": "True",
             "amount": 300.0, "expense_name": "C", "type_category": "Need, Travel"},
            # legacy cash -> paid_cash
            {"id": "4", "date": "2026-04-04", "payment_method": "cash",
             "paid_by_someone": "False", "paid_for_someone": "False",
             "amount": 50.0, "expense_name": "D", "type_category": "Need, Miscellaneous"},
            # legacy paid -> paid (unchanged)
            {"id": "5", "date": "2026-04-05", "payment_method": "paid",
             "paid_by_someone": "False", "paid_for_someone": "False",
             "amount": 75.0, "expense_name": "E", "type_category": "Want, Food & Drinks"},
        ],
    )

    _migrate_payment_method_v2(data_dir)

    df = pd.read_csv(data_dir / "expenses.csv", dtype=str, keep_default_na=False)
    by_id = {r["id"]: r for r in df.to_dict(orient="records")}

    assert by_id["1"]["payment_method"] == "paid_by"
    assert by_id["2"]["payment_method"] == "paid_for"
    assert by_id["2"]["paid_for_method"] == "cash"
    assert by_id["3"]["payment_method"] == "paid_for"
    assert by_id["3"]["paid_for_method"] == "online"
    assert by_id["4"]["payment_method"] == "paid_cash"
    assert by_id["5"]["payment_method"] == "paid"

    marker = data_dir / ".migrated_payment_method_v2"
    assert marker.exists()
    info = json.loads(marker.read_text(encoding="utf-8"))
    assert info["rewritten"] >= 4
    assert info["by_bucket"]["paid_by"] >= 1
    assert info["by_bucket"]["paid_for"] >= 2


def test_rerun_is_noop(tmp_path: Path) -> None:
    data_dir = _fresh_dirs(tmp_path)
    _write_expenses(
        data_dir,
        [
            {"id": "1", "date": "2026-04-01", "payment_method": "cash",
             "paid_by_someone": "False", "paid_for_someone": "False",
             "amount": 10.0, "expense_name": "x", "type_category": "Need, Miscellaneous"},
        ],
    )
    _migrate_payment_method_v2(data_dir)
    first = (data_dir / "expenses.csv").read_text(encoding="utf-8")

    # Second run: marker exists → does nothing
    _migrate_payment_method_v2(data_dir)
    second = (data_dir / "expenses.csv").read_text(encoding="utf-8")
    assert first == second


def test_empty_csv_safe(tmp_path: Path) -> None:
    data_dir = _fresh_dirs(tmp_path)
    # File exists with only header (created by bootstrap_for).
    _migrate_payment_method_v2(data_dir)
    marker = data_dir / ".migrated_payment_method_v2"
    assert marker.exists()
    info = json.loads(marker.read_text(encoding="utf-8"))
    assert info["rewritten"] == 0
