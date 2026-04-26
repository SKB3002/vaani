"""Atomic CSV I/O utilities with per-file locking."""
from __future__ import annotations

import os
import threading
import uuid
from pathlib import Path
from typing import Any

import pandas as pd

from app.storage.schemas import SCHEMAS, TableSchema

_LOCKS: dict[str, threading.Lock] = {}
_LOCKS_GUARD = threading.Lock()


def file_lock(path: str | Path) -> threading.Lock:
    """Return a process-wide lock for a given file path."""
    key = str(Path(path).resolve())
    with _LOCKS_GUARD:
        if key not in _LOCKS:
            _LOCKS[key] = threading.Lock()
        return _LOCKS[key]


def atomic_write_csv(df: pd.DataFrame, path: str | Path) -> None:
    """Write a DataFrame to CSV atomically.

    Writes to a temporary file alongside the target and uses os.replace,
    which is atomic on POSIX and on Windows for same-volume renames.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f"{target.name}.tmp.{uuid.uuid4().hex}")
    df.to_csv(tmp, index=False, encoding="utf-8", lineterminator="\n")
    os.replace(tmp, target)


def read_csv_typed(path: str | Path, schema: TableSchema) -> pd.DataFrame:
    """Read a CSV into a DataFrame enforcing the schema's columns and dtypes.

    If the file does not exist, an empty DataFrame with the schema columns is returned.
    Missing columns (from older schemas) are added as NA.

    NOTE: Any *extra* columns present in the CSV but NOT in the schema are preserved
    as-is and appended AFTER the schema columns. This keeps user-defined columns
    round-trippable even when callers use the built-in schema directly.
    """
    target = Path(path)
    columns = schema["columns"]
    dtypes = schema["dtypes"]

    if not target.exists() or target.stat().st_size == 0:
        return _empty_frame(schema)

    # Only apply dtypes for columns we know; let pandas infer the rest
    df = pd.read_csv(target, dtype=dtypes, keep_default_na=True)
    # Ensure every schema column is present
    for col in columns:
        if col not in df.columns:
            df[col] = pd.array([pd.NA] * len(df), dtype=dtypes.get(col, "string"))
    # Preserve extra columns (user-defined) after schema columns
    extra_cols = [c for c in df.columns if c not in columns]
    return df[[*columns, *extra_cols]]


def _empty_frame(schema: TableSchema) -> pd.DataFrame:
    data: dict[str, Any] = {col: pd.array([], dtype=schema["dtypes"][col]) for col in schema["columns"]}
    return pd.DataFrame(data)


def write_headers_if_missing(path: str | Path, table: str) -> None:
    """Create an empty CSV file with headers if it does not exist."""
    schema = SCHEMAS[table]
    target = Path(path)
    if target.exists():
        return
    atomic_write_csv(_empty_frame(schema), target)
