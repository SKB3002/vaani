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
        return out

    out = df.copy()
    if source == "expenses" and "type_category" in out.columns:
        parts = out["type_category"].astype("string").str.split(", ", n=1, expand=True)
        out["type"] = parts[0] if parts.shape[1] >= 1 else pd.Series(dtype="string")
        if parts.shape[1] >= 2:
            out["category"] = parts[1]
        else:
            out["category"] = pd.Series(dtype="string", index=out.index)
    return out
