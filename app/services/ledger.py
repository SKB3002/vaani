"""LedgerWriter — WAL + atomic CSV writes with per-file locks.

All mutations to any FinEye CSV go through this class so we get:
- Crash recovery via WAL replay on startup.
- Atomic writes via write-temp + os.replace.
- Single-writer-per-table ordering via threading.Lock.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

from app.storage.csv_store import atomic_write_csv, file_lock, read_csv_typed
from app.storage.schemas import SCHEMAS, table_path
from app.storage.wal import WalEntry, WriteAheadLog

# Post-commit observer: receives a dict describing the mutation.
# Shape: {"table": str, "op": "append"|"update"|"delete"|"delete_where",
#         "pk_column": str, "pk_value": str | None, "row": dict | None,
#         "column": str | None, "value": Any | None}
ChangeEvent = dict[str, Any]
ChangeCallback = Callable[[ChangeEvent], None]


class LedgerWriter:
    def __init__(self, data_dir: str | Path, wal_dir: str | Path) -> None:
        self.data_dir = Path(data_dir)
        self.wal = WriteAheadLog(wal_dir)
        self._observers: list[ChangeCallback] = []

    # ---------- observer registration ----------
    def on_change(self, callback: ChangeCallback) -> None:
        """Register a post-commit callback. Never raises back into the writer."""
        self._observers.append(callback)

    def off_change(self, callback: ChangeCallback) -> bool:
        """Deregister a previously registered callback. Returns True if removed."""
        try:
            self._observers.remove(callback)
            return True
        except ValueError:
            return False

    def clear_observers(self) -> None:
        self._observers.clear()

    def _notify(self, event: ChangeEvent) -> None:
        for cb in self._observers:
            try:
                cb(event)
            except Exception:  # noqa: BLE001 — observers must never break the write path
                logging.getLogger("vaani.ledger").exception(
                    "observer callback failed for %s", event.get("table")
                )

    # ---------- paths ----------
    def _path(self, table: str) -> Path:
        return Path(table_path(str(self.data_dir), table))

    # ---------- public API ----------
    def append(self, table: str, row: dict[str, Any]) -> dict[str, Any]:
        """Append a row to a table. Returns the row actually written."""
        self._check_table(table)
        entry = self.wal.append(table, "append", row)
        self._apply_append(table, row)
        self.wal.clear(entry.entry_id)
        pk = SCHEMAS[table]["pk"]
        self._notify(
            {
                "table": table,
                "op": "append",
                "pk_column": pk,
                "pk_value": row.get(pk),
                "row": row,
            }
        )
        return row

    def update(self, table: str, pk_value: str, updates: dict[str, Any]) -> dict[str, Any] | None:
        """Patch one row identified by the table's PK. Returns updated row or None."""
        self._check_table(table)
        entry = self.wal.append(table, "update", {"pk_value": pk_value, "updates": updates})
        updated = self._apply_update(table, pk_value, updates)
        self.wal.clear(entry.entry_id)
        if updated is not None:
            self._notify(
                {
                    "table": table,
                    "op": "update",
                    "pk_column": SCHEMAS[table]["pk"],
                    "pk_value": pk_value,
                    "row": updated,
                }
            )
        return updated

    def delete(self, table: str, pk_value: str) -> bool:
        """Delete a row by PK. Returns True if a row was removed."""
        self._check_table(table)
        entry = self.wal.append(table, "delete", {"pk_value": pk_value})
        removed = self._apply_delete(table, pk_value)
        self.wal.clear(entry.entry_id)
        if removed:
            self._notify(
                {
                    "table": table,
                    "op": "delete",
                    "pk_column": SCHEMAS[table]["pk"],
                    "pk_value": pk_value,
                    "row": None,
                }
            )
        return removed

    def delete_where(self, table: str, column: str, value: Any) -> int:
        """Delete all rows where column == value. Returns count removed."""
        self._check_table(table)
        entry = self.wal.append(
            table, "delete_where", {"column": column, "value": value}
        )
        count = self._apply_delete_where(table, column, value)
        self.wal.clear(entry.entry_id)
        if count > 0:
            self._notify(
                {
                    "table": table,
                    "op": "delete_where",
                    "pk_column": SCHEMAS[table]["pk"],
                    "pk_value": None,
                    "column": column,
                    "value": value,
                    "row": None,
                }
            )
        return count

    def read(self, table: str) -> pd.DataFrame:
        self._check_table(table)
        return read_csv_typed(self._path(table), SCHEMAS[table])

    def add_column(self, table: str, key: str, default: Any = None) -> None:
        """Append a column to the CSV with NaN (or supplied default) for existing rows.

        Atomic via the same lock + write-temp path as other mutations.
        """
        self._check_table(table)
        path = self._path(table)
        schema = SCHEMAS[table]
        with file_lock(path):
            df = read_csv_typed(path, schema)
            if key in df.columns:
                return
            df[key] = default if default is not None else pd.NA
            ordered = [*schema["columns"], *[c for c in df.columns if c not in schema["columns"]]]
            atomic_write_csv(df[ordered], path)

    def replay(self) -> int:
        """Replay any WAL entries that were written but never cleared."""
        return self.wal.replay_unfinished(self._handle_replay)

    # ---------- internals ----------
    def _check_table(self, table: str) -> None:
        if table not in SCHEMAS:
            raise KeyError(f"unknown table: {table}")

    def _apply_append(self, table: str, row: dict[str, Any]) -> None:
        path = self._path(table)
        schema = SCHEMAS[table]
        with file_lock(path):
            df = read_csv_typed(path, schema)
            # Preserve both built-in and any user-defined columns already in the df.
            # Any key in `row` that matches an existing column is written; extras are added.
            all_cols = list(df.columns)
            for key in row:
                if key not in all_cols:
                    all_cols.append(key)
            full_row = {col: row.get(col) for col in all_cols}
            new_df = pd.concat([df, pd.DataFrame([full_row])], ignore_index=True)
            # Keep schema columns first, then any extras in stable order
            ordered = [*schema["columns"], *[c for c in all_cols if c not in schema["columns"]]]
            atomic_write_csv(new_df[ordered], path)

    def _apply_update(
        self, table: str, pk_value: str, updates: dict[str, Any]
    ) -> dict[str, Any] | None:
        path = self._path(table)
        schema = SCHEMAS[table]
        pk = schema["pk"]
        with file_lock(path):
            df = read_csv_typed(path, schema)
            mask = df[pk].astype("string") == str(pk_value)
            if not mask.any():
                return None
            for col, val in updates.items():
                if col not in df.columns:
                    # add unknown column on the fly (user-defined) and fill NA
                    df[col] = pd.array([pd.NA] * len(df), dtype="object")
                df.loc[mask, col] = val
            ordered = [*schema["columns"], *[c for c in df.columns if c not in schema["columns"]]]
            atomic_write_csv(df[ordered], path)
            updated: dict[str, Any] = df.loc[mask].iloc[0].to_dict()
            return updated

    def _apply_delete(self, table: str, pk_value: str) -> bool:
        path = self._path(table)
        schema = SCHEMAS[table]
        pk = schema["pk"]
        with file_lock(path):
            df = read_csv_typed(path, schema)
            mask = df[pk].astype("string") == str(pk_value)
            if not mask.any():
                return False
            df = df.loc[~mask].reset_index(drop=True)
            ordered = [*schema["columns"], *[c for c in df.columns if c not in schema["columns"]]]
            atomic_write_csv(df[ordered], path)
            return True

    def _apply_delete_where(self, table: str, column: str, value: Any) -> int:
        path = self._path(table)
        schema = SCHEMAS[table]
        with file_lock(path):
            df = read_csv_typed(path, schema)
            if column not in df.columns:
                return 0
            mask = df[column].astype("string") == str(value)
            count = int(mask.sum())
            if count == 0:
                return 0
            df = df.loc[~mask].reset_index(drop=True)
            ordered = [*schema["columns"], *[c for c in df.columns if c not in schema["columns"]]]
            atomic_write_csv(df[ordered], path)
            return count

    def _handle_replay(self, entry: WalEntry) -> None:
        op = entry.op
        row = entry.row
        if op == "append":
            self._apply_append(entry.table, row)
        elif op == "update":
            self._apply_update(entry.table, row["pk_value"], row["updates"])
        elif op == "delete":
            self._apply_delete(entry.table, row["pk_value"])
        elif op == "delete_where":
            self._apply_delete_where(entry.table, row["column"], row["value"])
