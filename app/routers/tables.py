"""Universal per-table column management (§4.3 generalised).

Every importable table supports user-defined columns. The registry lives at
``data/meta/user_columns/{table}.json``. CSV columns are forward-only (NaN for
prior rows) and deletes are audit-safe (registry only — CSV column preserved).
"""
from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.config import get_settings
from app.deps import get_ledger
from app.services.ledger import LedgerWriter
from app.storage import user_columns
from app.storage.schemas import SCHEMAS

router = APIRouter(prefix="/api/tables", tags=["tables"])


class AddColumnRequest(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    dtype: Literal["string", "number", "boolean", "date"] = "string"
    default: Any | None = None


class RenameColumnRequest(BaseModel):
    label: str = Field(min_length=1, max_length=128)


def _data_dir() -> Any:
    return get_settings().resolved_data_dir()


def _check_table(table: str) -> None:
    if table not in SCHEMAS:
        raise HTTPException(404, f"unknown table: {table}")


@router.get("/{table}/columns")
def list_columns(table: str) -> dict[str, Any]:
    _check_table(table)
    return {
        "table": table,
        "columns": user_columns.resolve_columns(_data_dir(), table),
    }


@router.post("/{table}/columns", status_code=201)
def add_column(
    table: str,
    payload: AddColumnRequest,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    _check_table(table)
    try:
        entry = user_columns.add_column(
            _data_dir(),
            table,
            key=payload.key,
            label=payload.label,
            dtype=payload.dtype,
            default=payload.default,
        )
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except KeyError as e:
        raise HTTPException(404, str(e)) from e

    # Back-fill the CSV column
    ledger.add_column(table, payload.key, default=payload.default)
    return {
        "table": table,
        "column": entry,
        "columns": user_columns.resolve_columns(_data_dir(), table),
    }


@router.patch("/{table}/columns/{key}")
def rename_column(
    table: str, key: str, payload: RenameColumnRequest
) -> dict[str, Any]:
    _check_table(table)
    try:
        entry = user_columns.rename_column(_data_dir(), table, key, payload.label)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    return {"table": table, "column": entry}


@router.delete("/{table}/columns/{key}")
def delete_column(
    table: str, key: str, ledger: LedgerWriter = Depends(get_ledger)
) -> dict[str, Any]:
    _check_table(table)
    try:
        entry = user_columns.delete_column(_data_dir(), table, key)
    except KeyError as e:
        raise HTTPException(404, str(e)) from e

    # Warning: if column has non-null values, data is preserved in CSV
    warning: str | None = None
    try:
        df = ledger.read(table)
        if key in df.columns and df[key].notna().any():
            warning = (
                f"column '{key}' removed from registry but preserved in CSV "
                f"({int(df[key].notna().sum())} non-null values)"
            )
    except (KeyError, OSError):
        pass

    return {"table": table, "removed": entry, "warning": warning}
