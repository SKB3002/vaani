"""Unit: `total` = sum of numeric columns excluding total / month / import_batch_id.

Covers user-defined columns via the universal registry.
"""
from __future__ import annotations

from pathlib import Path

from hypothesis import given, settings
from hypothesis import strategies as st

from app.routers.investments import _TOTAL_EXCLUDED, _row_numeric_total
from app.storage import user_columns
from app.storage.schemas import INVESTMENTS

_BUILTIN_NUMERIC = [c for c in INVESTMENTS["columns"] if c not in _TOTAL_EXCLUDED]


@given(
    values=st.lists(
        st.one_of(st.none(), st.floats(min_value=-1e6, max_value=1e6, allow_nan=False)),
        min_size=len(_BUILTIN_NUMERIC),
        max_size=len(_BUILTIN_NUMERIC),
    )
)
@settings(max_examples=80, deadline=None)
def test_total_sums_only_numeric_excluding_reserved(values: list[float | None]) -> None:
    row: dict[str, float | None] = {
        "month": "2026-04",
        "import_batch_id": None,
        "total": 999999.0,  # must be ignored
    }
    for key, v in zip(_BUILTIN_NUMERIC, values, strict=True):
        row[key] = v

    total = _row_numeric_total(row, _BUILTIN_NUMERIC)
    expected = sum(v for v in values if v is not None)
    assert abs(total - expected) < 1e-6


def test_total_includes_user_column(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    user_columns.add_column(
        data_dir, "investments", key="crypto", label="Crypto", dtype="number"
    )

    numeric_keys = [*_BUILTIN_NUMERIC, "crypto"]
    row = {k: 0.0 for k in numeric_keys}
    row["long_term"] = 1000.0
    row["crypto"] = 2500.0
    row["month"] = "2026-05"
    row["total"] = 0.0
    row["import_batch_id"] = None

    total = _row_numeric_total(row, numeric_keys)
    assert total == 3500.0


def test_total_ignores_nan_and_non_numeric() -> None:
    row = {
        "month": "2026-06",
        "long_term": "not a number",
        "mid_long_term": None,
        "emergency_fund": float("nan"),
        "bike_savings_wants": 100.0,
        "misc_spend_save": 200.0,
        "fixed_deposits": 300.0,
        "total": 0.0,
        "import_batch_id": None,
    }
    total = _row_numeric_total(row, _BUILTIN_NUMERIC)
    assert total == 600.0
