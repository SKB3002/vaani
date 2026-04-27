"""User-defined column registry — one JSON file per table.

Generalises the investments-only column registry to every CSV table.
Registry file: ``data/meta/user_columns/{table}.json``.

Key design rules:
- Keys are snake_case and must not clash with built-in schema columns.
- Supported dtypes: ``string``, ``number``, ``boolean``, ``date``.
- Forward-only: older rows get NaN / default for newly added columns.
- Deletion removes from registry but PRESERVES the CSV column (audit safety).
- ``investments`` table keeps backward-compat with the old
  ``data/meta/investment_columns.json`` by one-shot read on first call.
"""
from __future__ import annotations

import json
import re
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, TypedDict

from app.storage.schemas import SCHEMAS

DType = Literal["string", "number", "boolean", "date"]

VALID_DTYPES: tuple[str, ...] = ("string", "number", "boolean", "date")
_KEY_RE = re.compile(r"^[a-z][a-z0-9_]*$")

# pandas dtype for each user dtype — matches the TableSchema dtypes[] convention
_PD_DTYPES: dict[str, str] = {
    "string": "string",
    "number": "float64",
    "boolean": "boolean",
    "date": "string",  # stored as ISO text in CSV, same as built-in date columns
}


class UserColumn(TypedDict, total=False):
    key: str
    label: str
    dtype: DType
    default: Any
    added_at: str


_REGISTRY_LOCK = threading.RLock()


def _registry_dir(data_dir: Path) -> Path:
    return data_dir / "meta" / "user_columns"


def _registry_path(data_dir: Path, table: str) -> Path:
    return _registry_dir(data_dir) / f"{table}.json"


def _legacy_investment_path(data_dir: Path) -> Path:
    return data_dir / "meta" / "investment_columns.json"


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _ensure_table(table: str) -> None:
    if table not in SCHEMAS:
        raise KeyError(f"unknown table: {table}")


def _load_raw(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"table": path.stem, "columns": []}
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    data.setdefault("columns", [])
    return data


def _save_raw(path: Path, data: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _migrate_legacy_investments(data_dir: Path) -> list[UserColumn]:
    """One-shot migration of the old investment_columns.json.

    Copies non-builtin entries into the new registry. Old file is NOT deleted.
    """
    legacy = _legacy_investment_path(data_dir)
    if not legacy.exists():
        return []
    try:
        raw = json.loads(legacy.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    migrated: list[UserColumn] = []
    for entry in raw.get("columns", []):
        if entry.get("builtin"):
            continue
        key = entry.get("key")
        label = entry.get("label") or key
        if not key or not _KEY_RE.match(key):
            continue
        migrated.append(
            {
                "key": key,
                "label": label,
                "dtype": "number",
                "default": None,
                "added_at": entry.get("added_at") or _now(),
            }
        )
    return migrated


def list_user_columns(data_dir: Path, table: str) -> list[UserColumn]:
    """Return the user-defined columns for a table (empty list if none)."""
    _ensure_table(table)
    path = _registry_path(data_dir, table)
    with _REGISTRY_LOCK:
        data = _load_raw(path)
        if table == "investments" and not data.get("columns"):
            # one-shot migrate from legacy file
            legacy_cols = _migrate_legacy_investments(data_dir)
            if legacy_cols:
                data["columns"] = legacy_cols
                data["table"] = table
                _save_raw(path, data)
        return list(data.get("columns", []))


class BuiltInColumn(TypedDict):
    key: str
    label: str
    dtype: str
    builtin: bool


def _builtin_columns(table: str) -> list[BuiltInColumn]:
    schema = SCHEMAS[table]
    out: list[BuiltInColumn] = []
    for col in schema["columns"]:
        pd_dtype = schema["dtypes"].get(col, "string").lower()
        if pd_dtype.startswith(("float", "int")):
            dtype = "number"
        elif pd_dtype == "boolean":
            dtype = "boolean"
        else:
            dtype = "string"
        out.append({"key": col, "label": col, "dtype": dtype, "builtin": True})
    return out


def resolve_columns(data_dir: Path, table: str) -> list[dict[str, Any]]:
    """Merged schema: built-in columns first, then user columns."""
    _ensure_table(table)
    builtin = _builtin_columns(table)
    merged: list[dict[str, Any]] = [dict(b) for b in builtin]
    builtin_keys = {c["key"] for c in builtin}
    for uc in list_user_columns(data_dir, table):
        if uc["key"] in builtin_keys:
            # user column would clash — skip (shouldn't happen, add_column guards)
            continue
        merged.append(
            {
                "key": uc["key"],
                "label": uc.get("label") or uc["key"],
                "dtype": uc.get("dtype") or "string",
                "default": uc.get("default"),
                "added_at": uc.get("added_at"),
                "builtin": False,
            }
        )
    return merged


def validate_key(table: str, key: str) -> None:
    if not _KEY_RE.match(key):
        raise ValueError(
            "key must be snake_case, start with a letter, only [a-z0-9_]"
        )
    schema = SCHEMAS[table]
    if key in schema["columns"]:
        raise ValueError(f"key '{key}' clashes with built-in column")


def add_column(
    data_dir: Path,
    table: str,
    key: str,
    label: str,
    dtype: str,
    default: Any = None,
) -> UserColumn:
    _ensure_table(table)
    validate_key(table, key)
    if dtype not in VALID_DTYPES:
        raise ValueError(f"dtype must be one of {VALID_DTYPES}")
    if not label or not label.strip():
        raise ValueError("label is required")

    path = _registry_path(data_dir, table)
    with _REGISTRY_LOCK:
        # trigger legacy migration for investments first
        existing = list_user_columns(data_dir, table)
        if any(c["key"] == key for c in existing):
            raise ValueError(f"column '{key}' already exists")
        entry: UserColumn = {
            "key": key,
            "label": label.strip(),
            "dtype": dtype,  # type: ignore[typeddict-item]
            "default": default,
            "added_at": _now(),
        }
        data = _load_raw(path)
        data["table"] = table
        data["columns"] = [*existing, entry]
        _save_raw(path, data)
    return entry


def rename_column(data_dir: Path, table: str, key: str, label: str) -> UserColumn:
    _ensure_table(table)
    if not label or not label.strip():
        raise ValueError("label is required")
    path = _registry_path(data_dir, table)
    with _REGISTRY_LOCK:
        cols = list_user_columns(data_dir, table)
        found: UserColumn | None = None
        for c in cols:
            if c["key"] == key:
                c["label"] = label.strip()
                found = c
                break
        if found is None:
            raise KeyError(f"column '{key}' not found")
        data = {"table": table, "columns": cols}
        _save_raw(path, data)
    return found


def delete_column(data_dir: Path, table: str, key: str) -> UserColumn:
    """Remove from registry. CSV column is preserved on disk (audit safety)."""
    _ensure_table(table)
    path = _registry_path(data_dir, table)
    with _REGISTRY_LOCK:
        cols = list_user_columns(data_dir, table)
        removed: UserColumn | None = None
        remaining: list[UserColumn] = []
        for c in cols:
            if c["key"] == key and removed is None:
                removed = c
            else:
                remaining.append(c)
        if removed is None:
            raise KeyError(f"column '{key}' not found")
        data = {"table": table, "columns": remaining}
        _save_raw(path, data)
    return removed


def pandas_dtype_for(dtype: str) -> str:
    return _PD_DTYPES.get(dtype, "string")
