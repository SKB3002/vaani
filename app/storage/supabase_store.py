"""Supabase/Postgres dual-write observer for LedgerWriter.

Receives ChangeEvent dicts from LedgerWriter._notify and upserts/deletes
rows in the corresponding Postgres table.  Failures are logged but never
propagated — the CSV path is always primary, so Supabase is a best-effort
mirror, not a write gate.

Column mapping:
  - Every table has user_id injected from settings.OWNER_ID.
  - balances: CSV pk is "asof"; Postgres PK is (user_id, asof).
  - investments: CSV pk is "month"; Postgres PK is (user_id, month).
  - budget_rules: CSV pk is "category"; Postgres PK is (user_id, category).
  - budget_table_c: CSV pk is "month" (not unique alone); Postgres PK is (user_id, month, category).
  - All others: id TEXT PRIMARY KEY (same in both).
"""
from __future__ import annotations

import logging
from typing import Any

import psycopg2

from app.config import get_settings

log = logging.getLogger("vaani.supabase")

# Postgres conflict columns per table (the ON CONFLICT (...) target)
_CONFLICT_COLS: dict[str, list[str]] = {
    "expenses": ["id"],
    "balances": ["user_id", "asof"],
    "investments": ["user_id", "month"],
    "wishlist": ["id"],
    "goals_a": ["goal_id"],
    "goals_b": ["goal_id"],
    "budget_rules": ["user_id", "category"],
    "budget_table_c": ["user_id", "month", "category"],
    "budget_state": ["user_id", "category"],
    "budget_adjustments": ["id"],
    "drafts": ["id"],
}

# Tables whose delete needs (user_id, csv_pk) instead of just (csv_pk)
_COMPOUND_PK_TABLES: set[str] = {
    "balances",
    "investments",
    "budget_rules",
    "budget_table_c",
    "budget_state",
}


def _get_conn() -> "psycopg2.extensions.connection":
    cfg = get_settings()
    return psycopg2.connect(cfg.supabase_dsn)


def _inject_user_id(row: dict[str, Any], user_id: str) -> dict[str, Any]:
    out = dict(row)
    out["user_id"] = user_id
    return out


def _upsert(table: str, row: dict[str, Any]) -> None:
    cfg = get_settings()
    if not cfg.supabase_configured:
        return

    row = _inject_user_id(row, cfg.OWNER_ID)
    conflict_cols = _CONFLICT_COLS.get(table, ["id"])

    # Only include columns that have non-None values; preserves Postgres defaults
    cols = [k for k, v in row.items() if v is not None]
    if not cols:
        return

    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    conflict_target = ", ".join(conflict_cols)
    update_clause = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in cols if c not in conflict_cols
    )
    if not update_clause:
        # All cols are conflict cols — use a harmless no-op
        update_clause = f"{conflict_cols[0]} = EXCLUDED.{conflict_cols[0]}"

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_target}) DO UPDATE SET {update_clause}"
    )

    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, [row[c] for c in cols])
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("supabase upsert failed for table=%s", table)


def _update_by_pk(
    table: str, pk_column: str, pk_value: str, updates: dict[str, Any]
) -> dict[str, Any] | None:
    """Patch existing row by PK. Returns the updated row, or None if no row matched.

    Unlike _upsert, this issues a real UPDATE so NOT NULL columns that aren't
    in `updates` aren't required. Used for partial patches where the row is
    known to exist.
    """
    cfg = get_settings()
    if not cfg.supabase_configured:
        return None

    cols = [k for k, v in updates.items() if v is not None]
    if not cols:
        return None

    set_clause = ", ".join(f"{c} = %s" for c in cols)
    compound = table in _COMPOUND_PK_TABLES
    where = f"{pk_column} = %s" + (" AND user_id = %s" if compound else "")
    params: list[Any] = [updates[c] for c in cols] + [pk_value]
    if compound:
        params.append(cfg.OWNER_ID)

    sql = f"UPDATE {table} SET {set_clause} WHERE {where} RETURNING *"

    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                row = cur.fetchone()
                colnames = [d[0] for d in cur.description] if cur.description else []
            conn.commit()
            if row is None:
                return None
            return dict(zip(colnames, row, strict=False))
        finally:
            conn.close()
    except Exception:
        log.exception("supabase update failed for table=%s pk=%s", table, pk_value)
        return None


def _delete_by_pk(table: str, pk_column: str, pk_value: str) -> None:
    cfg = get_settings()
    if not cfg.supabase_configured:
        return

    if table in _COMPOUND_PK_TABLES:
        sql = f"DELETE FROM {table} WHERE user_id = %s AND {pk_column} = %s"
        params: list[Any] = [cfg.OWNER_ID, pk_value]
    else:
        sql = f"DELETE FROM {table} WHERE {pk_column} = %s"
        params = [pk_value]

    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, params)
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("supabase delete failed for table=%s pk=%s", table, pk_value)


def _delete_where(table: str, column: str, value: Any) -> None:
    cfg = get_settings()
    if not cfg.supabase_configured:
        return

    sql = f"DELETE FROM {table} WHERE user_id = %s AND {column} = %s"
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, [cfg.OWNER_ID, value])
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("supabase delete_where failed for table=%s col=%s", table, column)


def read_table(table: str) -> "pd.DataFrame":
    """Read all rows for the current owner from Supabase, returned as a DataFrame.

    Used by LedgerWriter.read() when STORAGE_BACKEND=supabase (Vercel).
    Drops the user_id column before returning so callers see the same
    schema as the CSV path.
    """
    import pandas as pd
    from app.storage.schemas import SCHEMAS

    cfg = get_settings()
    schema = SCHEMAS[table]

    if not cfg.supabase_configured:
        return _empty_frame(schema)

    # Build column list excluding user_id (not in CSV schema)
    cols = schema["columns"]
    col_list = ", ".join(cols)

    # Sort by PK for stable ordering; expenses sorted by date DESC
    order = "date DESC, created_at DESC" if table == "expenses" else schema["pk"]
    sql = (
        f"SELECT {col_list} FROM {table} "
        f"WHERE user_id = %s ORDER BY {order}"
    )

    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                cur.execute(sql, [cfg.OWNER_ID])
                rows = cur.fetchall()
        finally:
            conn.close()

        if not rows:
            return _empty_frame(schema)

        df = pd.DataFrame(rows, columns=cols)
        # Cast to schema dtypes best-effort
        for col, dtype in schema["dtypes"].items():
            if col in df.columns:
                try:
                    df[col] = df[col].astype(dtype)
                except (ValueError, TypeError):
                    pass
        return df

    except Exception:
        log.exception("supabase read_table failed for table=%s", table)
        return _empty_frame(schema)


def _empty_frame(schema: "Any") -> "pd.DataFrame":
    import pandas as pd
    data = {col: pd.array([], dtype=schema["dtypes"][col]) for col in schema["columns"]}
    return pd.DataFrame(data)


def supabase_observer(event: dict[str, Any]) -> None:
    """LedgerWriter post-commit observer — mirrors every CSV mutation to Supabase."""
    op: str = event.get("op", "")
    table: str = event.get("table", "")

    if op in ("append", "update"):
        row = event.get("row") or {}
        if row:
            _upsert(table, row)

    elif op == "delete":
        pk_col = event.get("pk_column") or "id"
        pk_val = event.get("pk_value")
        if pk_val:
            _delete_by_pk(table, pk_col, str(pk_val))

    elif op == "delete_where":
        col = event.get("column")
        val = event.get("value")
        if col:
            _delete_where(table, col, val)


def bulk_upsert(table: str, rows: list[dict[str, Any]]) -> int:
    """Upsert a batch of rows atomically (used by migration script).

    Returns count of rows attempted.  On error, logs and returns 0.
    """
    cfg = get_settings()
    if not cfg.supabase_configured or not rows:
        return 0

    user_id = cfg.OWNER_ID
    rows_with_uid = [_inject_user_id(r, user_id) for r in rows]
    conflict_cols = _CONFLICT_COLS.get(table, ["id"])

    cols = list(rows_with_uid[0].keys())
    col_list = ", ".join(cols)
    placeholders = ", ".join(["%s"] * len(cols))
    conflict_target = ", ".join(conflict_cols)
    update_clause = ", ".join(
        f"{c} = EXCLUDED.{c}" for c in cols if c not in conflict_cols
    )
    if not update_clause:
        update_clause = f"{conflict_cols[0]} = EXCLUDED.{conflict_cols[0]}"

    sql = (
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT ({conflict_target}) DO UPDATE SET {update_clause}"
    )

    count = 0
    try:
        conn = _get_conn()
        try:
            with conn.cursor() as cur:
                for row in rows_with_uid:
                    cur.execute(sql, [row[c] for c in cols])
                    count += 1
            conn.commit()
        finally:
            conn.close()
    except Exception:
        log.exception("supabase bulk_upsert failed for table=%s", table)
        return 0

    return count
