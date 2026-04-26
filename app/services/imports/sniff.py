"""Encoding + schema detection for uploaded files."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import chardet
import pandas as pd


def detect_encoding(path: str | Path, sample_bytes: int = 65536) -> str:
    with open(path, "rb") as f:
        raw = f.read(sample_bytes)
    guess = chardet.detect(raw) or {}
    encoding = guess.get("encoding") or "utf-8"
    return str(encoding)


def list_sheets(path: str | Path) -> list[str]:
    """Return sheet names for an Excel workbook, or [""] for a CSV."""
    p = Path(path)
    if p.suffix.lower() in {".xlsx", ".xls"}:
        xl = pd.ExcelFile(p)
        return list(xl.sheet_names)
    return [""]


def read_preview(
    path: str | Path,
    sheet_name: str | None = None,
    nrows: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Return (preview_df, full_df) — preview limited to nrows, full for counts."""
    p = Path(path)
    suffix = p.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        full = pd.read_excel(p, sheet_name=sheet_name or 0)
        if isinstance(full, dict):  # defensive — sheet_name=None returns dict
            first_key = next(iter(full.keys()))
            full = full[first_key]
    else:
        encoding = detect_encoding(p)
        full = pd.read_csv(p, encoding=encoding, engine="python", sep=None)
    preview = full.head(nrows).copy()
    return preview, full


def guess_dtypes(df: pd.DataFrame) -> dict[str, str]:
    out: dict[str, str] = {}
    for col in df.columns:
        out[str(col)] = str(df[col].dtype)
    return out


def preview_to_records(df: pd.DataFrame) -> list[dict[str, Any]]:
    """Convert preview DataFrame to JSON-safe list of dicts."""
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]
