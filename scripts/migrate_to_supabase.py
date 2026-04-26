"""One-shot migration: push all existing CSV data to Supabase.

Run from the project root:
    python scripts/migrate_to_supabase.py

Tables migrated (in dependency order):
    expenses, balances, investments, wishlist, goals_a, goals_b,
    budget_rules, budget_table_c, drafts

Safe to run multiple times — uses ON CONFLICT DO UPDATE (upsert).
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make sure we can import app.*
sys.path.insert(0, str(Path(__file__).parent.parent))

import math
import pandas as pd

from app.config import get_settings
from app.storage.schemas import SCHEMAS
from app.storage.supabase_store import bulk_upsert


def _nan_to_none(val: object) -> object:
    if isinstance(val, float) and math.isnan(val):
        return None
    return val


def _df_to_dicts(df: pd.DataFrame) -> list[dict]:
    rows = []
    for rec in df.to_dict(orient="records"):
        rows.append({k: _nan_to_none(v) for k, v in rec.items()})
    return rows


TABLE_ORDER = [
    "expenses",
    "balances",
    "investments",
    "wishlist",
    "goals_a",
    "goals_b",
    "budget_rules",
    "budget_table_c",
    "drafts",
]


def main() -> None:
    cfg = get_settings()
    if not cfg.supabase_configured:
        print("ERROR: Supabase not configured. Set DB_HOST and DB_PASSWORD in .env")
        sys.exit(1)

    data_dir = cfg.resolved_data_dir()
    print(f"Data dir: {data_dir}")
    print(f"Supabase host: {cfg.DB_HOST}")
    print(f"Owner ID: {cfg.OWNER_ID}\n")

    total_rows = 0
    for table in TABLE_ORDER:
        csv_path = data_dir / f"{table}.csv"
        if not csv_path.exists() or csv_path.stat().st_size == 0:
            print(f"  {table}: (no CSV, skipping)")
            continue

        schema = SCHEMAS[table]
        df = pd.read_csv(csv_path, dtype=schema["dtypes"], keep_default_na=True)
        # Only send columns that exist in the schema (drop any extra user cols for now)
        schema_cols = [c for c in schema["columns"] if c in df.columns]
        df = df[schema_cols]

        rows = _df_to_dicts(df)
        if not rows:
            print(f"  {table}: (empty, skipping)")
            continue

        count = bulk_upsert(table, rows)
        total_rows += count
        print(f"  {table}: {count}/{len(rows)} rows upserted")

    print(f"\nDone. {total_rows} total rows pushed to Supabase.")


if __name__ == "__main__":
    main()
