"""First-run bootstrap: ensure directory layout + empty CSVs with headers + meta files."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

from app.config import get_settings
from app.storage.csv_store import write_headers_if_missing
from app.storage.schemas import SCHEMAS, table_path

logger = logging.getLogger(__name__)

# Matches the legacy "Type:Category" storage format. Comma-form rows are left as-is.
_LEGACY_TC_RE = re.compile(
    r"^(Need|Want|Investment):(Food & Drinks|Travel|Enjoyment|Miscellaneous)$"
)
_TC_MIGRATION_MARKER = ".migrated_type_category_comma"
_PM_V2_MIGRATION_MARKER = ".migrated_payment_method_v2"

DEFAULT_META = {
    "currency": "INR",
    "timezone": "Asia/Kolkata",
    "caps": {
        "medical_upper_cap": 10000,
        "emergency_monthly_cap": 5000,
    },
}

DEFAULT_UNIQUES: dict[str, Any] = {
    "vendors": {},
    "aliases": {},
    "people": [],
}

DEFAULT_BUDGET_RULES: list[dict[str, Any]] = [
    # Monthly Emergency Expense — first sink for overflow from category carry caps.
    {"category": "Emergency", "monthly_budget": 0.0, "carry_cap": 0.0, "priority": 90},
    # Medical — second sink for overflow once Emergency cap is filled.
    {"category": "Medical", "monthly_budget": 0.0, "carry_cap": 0.0, "priority": 91},
]

DEFAULT_INVESTMENT_COLUMNS = {
    "columns": [
        {"key": "long_term", "label": "Long Term", "builtin": True},
        {"key": "mid_long_term", "label": "Mid/Long Term", "builtin": True},
        {"key": "emergency_fund", "label": "Emergency Fund", "builtin": True},
        {"key": "bike_savings_wants", "label": "Bike Savings / Wants", "builtin": True},
        {"key": "misc_spend_save", "label": "Misc Spend/Save", "builtin": True},
        {"key": "fixed_deposits", "label": "Fixed Deposits", "builtin": True},
    ]
}

DEFAULT_CHARTS_YAML = """\
# FinEye — chart registry.
# Add a chart = append an entry here, then POST /api/charts/refresh or restart.
# Schema is validated by app/services/charts/registry.py (ChartSpec).
version: 1
charts:
  - id: cumulative_types_pie
    title: "Cumulative Need / Want / Investment"
    source: expenses
    type: pie
    group_by: type
    agg: sum
    y: amount
    format: currency
    palette: ["--chart-need", "--chart-want", "--chart-investment"]

  - id: monthly_stack
    title: "Monthly Expenses by Type"
    source: expenses
    type: stacked_bar
    x: date
    series: type
    time_bucket: month
    agg: sum
    y: amount
    format: currency

  - id: category_donut
    title: "Category Breakdown"
    source: expenses
    type: donut
    group_by: category
    agg: sum
    y: amount
    format: currency
    palette: ["--chart-food", "--chart-travel", "--chart-enjoyment", "--chart-misc"]

  - id: goal_progress
    title: "Goal Progress"
    source: goals_a
    type: horizontal_bar
    x: goal_name
    series: [current_amount, target_amount]
    format: currency

  - id: daily_spend_line
    title: "Daily Spend (Last 30 Days)"
    source: expenses
    type: line
    x: date
    time_bucket: day
    agg: sum
    y: amount
    filter: "date >= '2026-03-24'"
    format: currency

  - id: top_vendors
    title: "Top 10 Vendors (Last 90 Days)"
    source: expenses
    type: bar
    x: expense_name
    agg: sum
    y: amount
    top_n: 10
    top_n_other: true
    order_by: value_desc
    filter: "date >= '2026-01-23'"
    format: currency
"""


DEFAULT_IMPORT_PRESETS: dict[str, Any] = {
    "presets": [
        {
            "id": "personal_ledger_v1",
            "label": "My personal ledger (DD/MM/YYYY - combined tags - daily totals)",
            "target_table": "expenses",
            "date_format": "%d/%m/%Y",
            "column_mapping": {
                "Date": "date",
                "Vendor": "vendor",
                "Payment": "__payment_dual",
                "Tags": "__tags_combined",
                "Item": "expense_name",
                "Amount": "amount",
                "Cash balance": "__cash_snapshot",
                "Online balance": "__online_snapshot",
            },
            "row_filters": {
                "skip_when_payment_equals": ["Total"],
                "detect_balance_adjust": True,
            },
        }
    ]
}


def bootstrap() -> None:
    """Idempotent: create directories, CSVs with headers, and meta/JSON config files."""
    settings = get_settings()

    data_dir = settings.resolved_data_dir()
    wal_dir = settings.resolved_wal_dir()
    tmp_dir = settings.resolved_tmp_dir()

    for d in (
        data_dir,
        wal_dir,
        tmp_dir,
        data_dir / "meta",
        data_dir / "meta" / "user_columns",
        data_dir / "imports",
    ):
        d.mkdir(parents=True, exist_ok=True)

    for table in SCHEMAS:
        write_headers_if_missing(table_path(str(data_dir), table), table)

    # Seed default Emergency + Medical rules so they're visible in Table C.
    _seed_default_budget_rules(data_dir)

    meta_path = data_dir / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps(DEFAULT_META, indent=2), encoding="utf-8")

    uniques_path = data_dir / "uniques.json"
    if not uniques_path.exists():
        uniques_path.write_text(json.dumps(DEFAULT_UNIQUES, indent=2), encoding="utf-8")

    inv_cols_path = data_dir / "meta" / "investment_columns.json"
    if not inv_cols_path.exists():
        inv_cols_path.write_text(json.dumps(DEFAULT_INVESTMENT_COLUMNS, indent=2), encoding="utf-8")

    presets_path = data_dir / "meta" / "import_presets.json"
    if not presets_path.exists():
        presets_path.write_text(
            json.dumps(DEFAULT_IMPORT_PRESETS, indent=2), encoding="utf-8"
        )

    charts_path = data_dir / "meta" / "charts.yaml"
    if not charts_path.exists():
        charts_path.write_text(DEFAULT_CHARTS_YAML, encoding="utf-8")

    dedup_path = data_dir / ".dedup_keys.jsonl"
    if not dedup_path.exists():
        dedup_path.touch()

    _migrate_type_category_to_comma(data_dir)
    _migrate_payment_method_v2(data_dir)


def _migrate_payment_method_v2(data_dir: Path) -> None:
    """One-time rewrite of `expenses.payment_method` to the 5-value enum.

    Rules (priority order):
      - paid_by_someone=True  → payment_method='paid_by'
      - paid_for_someone=True → payment_method='paid_for',
                                paid_for_method='cash' if old method=='cash' else 'online'
      - old payment_method == 'cash'  → 'paid_cash'
      - old payment_method == 'paid'  → 'paid' (unchanged)
      - anything else → left as-is

    Adds empty `paid_for_method` / `adjustment_type` columns if missing. Idempotent.
    """
    marker = data_dir / _PM_V2_MIGRATION_MARKER
    if marker.exists():
        return

    expenses_path = data_dir / "expenses.csv"
    rewritten = 0
    buckets: dict[str, int] = {"paid_by": 0, "paid_for": 0, "paid_cash": 0, "paid": 0}

    if expenses_path.exists() and expenses_path.stat().st_size > 0:
        import pandas as pd  # noqa: PLC0415

        try:
            df = pd.read_csv(expenses_path, dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            df = None

        if df is not None and not df.empty and "payment_method" in df.columns:
            if "paid_for_method" not in df.columns:
                df["paid_for_method"] = ""
            if "adjustment_type" not in df.columns:
                df["adjustment_type"] = ""

            def _truthy(v: Any) -> bool:
                return str(v).strip().lower() in {"true", "1", "yes", "y"}

            paid_by_col = df["paid_by_someone"] if "paid_by_someone" in df.columns else None
            paid_for_col = df["paid_for_someone"] if "paid_for_someone" in df.columns else None

            for i in range(len(df)):
                old_pm = str(df.at[i, "payment_method"]).strip()
                is_paid_by = bool(paid_by_col is not None and _truthy(paid_by_col.iloc[i]))
                is_paid_for = bool(paid_for_col is not None and _truthy(paid_for_col.iloc[i]))

                new_pm: str | None = None
                new_pfm: str | None = None

                if is_paid_by:
                    new_pm = "paid_by"
                elif is_paid_for:
                    new_pm = "paid_for"
                    new_pfm = "cash" if old_pm == "cash" else "online"
                elif old_pm == "cash":
                    new_pm = "paid_cash"
                elif old_pm == "paid":
                    new_pm = "paid"

                if new_pm is not None and new_pm != old_pm:
                    df.at[i, "payment_method"] = new_pm
                    rewritten += 1
                if new_pfm is not None:
                    df.at[i, "paid_for_method"] = new_pfm
                if new_pm in buckets:
                    buckets[new_pm] += 1

            tmp_path = expenses_path.with_suffix(".csv.tmp")
            df.to_csv(tmp_path, index=False)
            tmp_path.replace(expenses_path)

    marker.write_text(
        json.dumps({"rewritten": rewritten, "by_bucket": buckets}),
        encoding="utf-8",
    )
    logger.info(
        "payment_method v2 migration: rewrote %d row(s); buckets=%s", rewritten, buckets
    )


def _migrate_type_category_to_comma(data_dir: Path) -> None:
    """One-time rewrite of `expenses.type_category` from 'Type:Category' to
    'Type, Category'. Idempotent — a marker file prevents repeat scans.

    Safe on empty data (CSV missing, empty, or all rows already migrated).
    """
    marker = data_dir / _TC_MIGRATION_MARKER
    if marker.exists():
        return

    expenses_path = data_dir / "expenses.csv"
    rewritten = 0
    if expenses_path.exists() and expenses_path.stat().st_size > 0:
        # Lazy import: pandas is heavy and bootstrap is called early.
        import pandas as pd  # noqa: PLC0415

        try:
            df = pd.read_csv(expenses_path, dtype=str, keep_default_na=False)
        except pd.errors.EmptyDataError:
            df = None

        if df is not None and "type_category" in df.columns and not df.empty:
            mask = df["type_category"].astype(str).str.match(_LEGACY_TC_RE)
            rewritten = int(mask.sum())
            if rewritten > 0:
                df.loc[mask, "type_category"] = (
                    df.loc[mask, "type_category"].str.replace(":", ", ", n=1, regex=False)
                )
                tmp_path = expenses_path.with_suffix(".csv.tmp")
                df.to_csv(tmp_path, index=False)
                tmp_path.replace(expenses_path)

    marker.write_text(
        json.dumps({"rewritten": rewritten, "format": "Type, Category"}),
        encoding="utf-8",
    )
    logger.info(
        "type_category migration: rewrote %d row(s) from 'Type:Category' to 'Type, Category'",
        rewritten,
    )


def bootstrap_for(data_dir: Path, wal_dir: Path, tmp_dir: Path) -> None:
    """Bootstrap against explicit directories (for tests)."""
    for d in (
        data_dir,
        wal_dir,
        tmp_dir,
        data_dir / "meta",
        data_dir / "meta" / "user_columns",
        data_dir / "imports",
    ):
        d.mkdir(parents=True, exist_ok=True)
    for table in SCHEMAS:
        write_headers_if_missing(table_path(str(data_dir), table), table)
    _seed_default_budget_rules(data_dir)
    meta_path = data_dir / "meta.json"
    if not meta_path.exists():
        meta_path.write_text(json.dumps(DEFAULT_META, indent=2), encoding="utf-8")
    uniques_path = data_dir / "uniques.json"
    if not uniques_path.exists():
        uniques_path.write_text(json.dumps(DEFAULT_UNIQUES, indent=2), encoding="utf-8")
    inv_cols = data_dir / "meta" / "investment_columns.json"
    if not inv_cols.exists():
        inv_cols.write_text(json.dumps(DEFAULT_INVESTMENT_COLUMNS, indent=2), encoding="utf-8")
    presets_path = data_dir / "meta" / "import_presets.json"
    if not presets_path.exists():
        presets_path.write_text(
            json.dumps(DEFAULT_IMPORT_PRESETS, indent=2), encoding="utf-8"
        )
    charts_path = data_dir / "meta" / "charts.yaml"
    if not charts_path.exists():
        charts_path.write_text(DEFAULT_CHARTS_YAML, encoding="utf-8")
    dedup = data_dir / ".dedup_keys.jsonl"
    if not dedup.exists():
        dedup.touch()

    _migrate_type_category_to_comma(data_dir)
    _migrate_payment_method_v2(data_dir)


def _seed_default_budget_rules(data_dir: Path) -> None:
    """Ensure Emergency + Medical rules always exist — they're overflow sinks
    and should be visible in Table C by default. Never clobbers user edits."""
    rules_path = data_dir / "budget_rules.csv"
    if not rules_path.exists():
        return
    try:
        with rules_path.open(encoding="utf-8") as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
    except OSError:
        return
    existing: set[str] = set()
    for ln in lines[1:]:
        first_cell = ln.split(",", 1)[0].strip()
        if first_cell:
            existing.add(first_cell)

    missing = [r for r in DEFAULT_BUDGET_RULES if r["category"] not in existing]
    if not missing:
        return

    appended = "\n".join(
        f"{r['category']},{r['monthly_budget']},{r['carry_cap']},{r['priority']}"
        for r in missing
    )
    with rules_path.open("a", encoding="utf-8") as f:
        f.write(appended + "\n")
    logger.info(
        "Seeded %d default budget rule(s): %s",
        len(missing),
        ", ".join(r["category"] for r in missing),
    )
