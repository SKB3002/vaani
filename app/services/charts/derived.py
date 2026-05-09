"""Virtual/derived columns synthesised from stored columns at aggregation time.

Keeps the CSV schema stable (no new columns written) while letting the chart
registry reference logical fields like `type` and `category` derived from
`expenses.type_category`.
"""
from __future__ import annotations

import pandas as pd


def add_derived_columns(df: pd.DataFrame, source: str) -> pd.DataFrame:
    """Return a copy of `df` with virtual columns added for the given source.

    Never mutates the caller's DataFrame. Safe on empty frames.
    """
    if df.empty:
        out = df.copy()
        if source == "expenses":
            for col in ("type", "category"):
                if col not in out.columns:
                    out[col] = pd.Series(dtype="string")
        if source == "goals_a" and "remaining" not in out.columns:
            out["remaining"] = pd.Series(dtype="float64")
        return out

    out = df.copy()
    if source == "expenses" and "type_category" in out.columns:
        parts = out["type_category"].astype("string").str.split(", ", n=1, expand=True)
        out["type"] = parts[0] if parts.shape[1] >= 1 else pd.Series(dtype="string")
        if parts.shape[1] >= 2:
            out["category"] = parts[1]
        else:
            out["category"] = pd.Series(dtype="string", index=out.index)
    if source == "goals_a" and {"target_amount", "current_amount"}.issubset(out.columns):
        target = pd.to_numeric(out["target_amount"], errors="coerce").fillna(0.0)
        current = pd.to_numeric(out["current_amount"], errors="coerce").fillna(0.0)
        out["remaining"] = (target - current).clip(lower=0.0)
    return out
