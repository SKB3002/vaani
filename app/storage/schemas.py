"""CSV table schemas for FinEye data tables.

Single source of truth for column order, dtypes, and primary keys.
Matches §4 of docs/PLAN-fineye-finance-ai.md.
"""
from __future__ import annotations

from typing import TypedDict


class TableSchema(TypedDict):
    columns: list[str]
    dtypes: dict[str, str]
    pk: str


# NOTE: dtypes use pandas-friendly strings. Dates/datetimes are stored as strings in CSV
# (ISO 8601) and parsed on read. Booleans serialise as "True"/"False".

EXPENSES: TableSchema = {
    "columns": [
        "id",
        "date",
        "created_at",
        "expense_name",
        "type_category",
        "payment_method",
        "paid_for_someone",
        "paid_by_someone",
        "person_name",
        "amount",
        "cash_balance_after",
        "online_balance_after",
        "source",
        "raw_transcript",
        "notes",
        "import_batch_id",
        "custom_tag",
        "paid_for_method",
        "adjustment_type",
    ],
    "dtypes": {
        "id": "string",
        "date": "string",
        "created_at": "string",
        "expense_name": "string",
        "type_category": "string",
        "payment_method": "string",
        "paid_for_someone": "boolean",
        "paid_by_someone": "boolean",
        "person_name": "string",
        "amount": "float64",
        "cash_balance_after": "float64",
        "online_balance_after": "float64",
        "source": "string",
        "raw_transcript": "string",
        "notes": "string",
        "import_batch_id": "string",
        "custom_tag": "string",
        "paid_for_method": "string",
        "adjustment_type": "string",
    },
    "pk": "id",
}

BALANCES: TableSchema = {
    "columns": ["asof", "cash_balance", "online_balance", "reason"],
    "dtypes": {
        "asof": "string",
        "cash_balance": "float64",
        "online_balance": "float64",
        "reason": "string",
    },
    "pk": "asof",
}

INVESTMENTS: TableSchema = {
    "columns": [
        "month",
        "long_term",
        "mid_long_term",
        "emergency_fund",
        "bike_savings_wants",
        "misc_spend_save",
        "fixed_deposits",
        "total",
        "import_batch_id",
    ],
    "dtypes": {
        "month": "string",
        "long_term": "float64",
        "mid_long_term": "float64",
        "emergency_fund": "float64",
        "bike_savings_wants": "float64",
        "misc_spend_save": "float64",
        "fixed_deposits": "float64",
        "total": "float64",
        "import_batch_id": "string",
    },
    "pk": "month",
}

WISHLIST: TableSchema = {
    "columns": [
        "id",
        "item",
        "target_amount",
        "saved_so_far",
        "priority",
        "notes",
        "link",
        "source",
        "created_at",
        "status",
        "import_batch_id",
    ],
    "dtypes": {
        "id": "string",
        "item": "string",
        "target_amount": "float64",
        "saved_so_far": "float64",
        "priority": "string",
        "notes": "string",
        "link": "string",
        "source": "string",
        "created_at": "string",
        "status": "string",
        "import_batch_id": "string",
    },
    "pk": "id",
}

GOALS_A: TableSchema = {
    "columns": [
        "goal_id",
        "goal_name",
        "target_amount",
        "current_amount",
        "monthly_contribution",
        "pct_complete",
        "months_left",
        "status",
        "import_batch_id",
    ],
    "dtypes": {
        "goal_id": "string",
        "goal_name": "string",
        "target_amount": "float64",
        "current_amount": "float64",
        "monthly_contribution": "float64",
        "pct_complete": "float64",
        "months_left": "Int64",
        "status": "string",
        "import_batch_id": "string",
    },
    "pk": "goal_id",
}

GOALS_B: TableSchema = {
    "columns": [
        "goal_id",
        "goal_name",
        "target_amount",
        "manual_saved",
        "auto_added",
        "total_saved",
        "monthly_contribution",
        "pct_complete",
        "months_left",
        "status",
        "import_batch_id",
    ],
    "dtypes": {
        "goal_id": "string",
        "goal_name": "string",
        "target_amount": "float64",
        "manual_saved": "float64",
        "auto_added": "float64",
        "total_saved": "float64",
        "monthly_contribution": "float64",
        "pct_complete": "float64",
        "months_left": "Int64",
        "status": "string",
        "import_batch_id": "string",
    },
    "pk": "goal_id",
}

BUDGET_RULES: TableSchema = {
    "columns": ["category", "monthly_budget", "carry_cap", "priority"],
    "dtypes": {
        "category": "string",
        "monthly_budget": "float64",
        "carry_cap": "float64",
        "priority": "Int64",
    },
    "pk": "category",
}

BUDGET_TABLE_C: TableSchema = {
    "columns": [
        "month",
        "category",
        "budget",
        "actual",
        "remaining",
        "carry_buffer",
        "overflow",
        "to_medical",
        "to_emergency",
        "med_balance",
        "emerg_balance",
        "notes",
    ],
    "dtypes": {
        "month": "string",
        "category": "string",
        "budget": "float64",
        "actual": "float64",
        "remaining": "float64",
        "carry_buffer": "float64",
        "overflow": "float64",
        "to_medical": "float64",
        "to_emergency": "float64",
        "med_balance": "float64",
        "emerg_balance": "float64",
        "notes": "string",
    },
    "pk": "month",
}

DRAFTS: TableSchema = {
    "columns": [
        "id",
        "target_table",
        "row_json",
        "errors",
        "source_filename",
        "created_at",
        "import_batch_id",
    ],
    "dtypes": {
        "id": "string",
        "target_table": "string",
        "row_json": "string",
        "errors": "string",
        "source_filename": "string",
        "created_at": "string",
        "import_batch_id": "string",
    },
    "pk": "id",
}


SCHEMAS: dict[str, TableSchema] = {
    "expenses": EXPENSES,
    "balances": BALANCES,
    "investments": INVESTMENTS,
    "wishlist": WISHLIST,
    "goals_a": GOALS_A,
    "goals_b": GOALS_B,
    "budget_rules": BUDGET_RULES,
    "budget_table_c": BUDGET_TABLE_C,
    "drafts": DRAFTS,
}


IMPORTABLE_TABLES: set[str] = {"expenses", "investments", "wishlist", "goals_a", "goals_b"}


def table_path(data_dir: str, table: str) -> str:
    import os

    return os.path.join(data_dir, f"{table}.csv")
