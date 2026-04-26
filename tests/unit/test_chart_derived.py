"""Virtual/derived column tests."""
from __future__ import annotations

import pandas as pd

from app.services.charts.derived import add_derived_columns


def test_expenses_splits_type_category() -> None:
    df = pd.DataFrame(
        {
            "id": ["1", "2", "3"],
            "type_category": ["Need, Travel", "Want, Food & Drinks", "Investment, Miscellaneous"],
            "amount": [100.0, 200.0, 300.0],
        }
    )
    out = add_derived_columns(df, "expenses")
    assert list(out["type"]) == ["Need", "Want", "Investment"]
    assert list(out["category"]) == ["Travel", "Food & Drinks", "Miscellaneous"]


def test_empty_expenses_still_has_derived_columns() -> None:
    df = pd.DataFrame(columns=["id", "type_category", "amount"])
    out = add_derived_columns(df, "expenses")
    assert "type" in out.columns
    assert "category" in out.columns
    assert len(out) == 0


def test_other_source_unchanged() -> None:
    df = pd.DataFrame({"month": ["2026-01"], "total": [1000.0]})
    out = add_derived_columns(df, "investments")
    assert list(out.columns) == ["month", "total"]
