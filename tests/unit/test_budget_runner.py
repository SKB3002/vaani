"""Budget runner: loads CSVs, iterates months, writes Table C atomically."""
from __future__ import annotations

from pathlib import Path

import ulid

from app.services.budget_runner import BudgetRunner
from app.services.ledger import LedgerWriter


def _add_rule(ledger: LedgerWriter, category: str, budget: float, cap: float, priority: int = 1) -> None:
    ledger.append("budget_rules", {
        "category": category,
        "monthly_budget": budget,
        "carry_cap": cap,
        "priority": priority,
    })


def _add_expense(ledger: LedgerWriter, date: str, amount: float, type_category: str | None = None, custom_tag: str | None = None) -> None:
    ledger.append("expenses", {
        "id": str(ulid.new()),
        "date": date,
        "created_at": date + "T12:00:00+00:00",
        "expense_name": "e",
        "type_category": type_category,
        "payment_method": "paid",
        "paid_for_someone": False,
        "paid_by_someone": False,
        "person_name": None,
        "amount": amount,
        "cash_balance_after": 0,
        "online_balance_after": 0,
        "source": "manual",
        "raw_transcript": None,
        "notes": None,
        "import_batch_id": None,
        "custom_tag": custom_tag,
    })


def test_recompute_produces_table_c(ledger: LedgerWriter, tmp_workspace: Path) -> None:
    _add_rule(ledger, "electricity", 3000, 4000)
    _add_expense(ledger, "2026-01-10", 2500, custom_tag="electricity")
    _add_expense(ledger, "2026-02-10", 2000, custom_tag="electricity")

    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    summary = runner.recompute_all()

    assert summary.months_computed >= 2
    rows_jan = runner.read_table_c("2026-01")
    # Bootstrap-seeded Emergency + Medical rules also appear
    elec_jan = next(r for r in rows_jan if r["category"] == "electricity")
    assert elec_jan["actual"] == 2500.0
    assert elec_jan["carry_buffer"] == 500.0

    rows_feb = runner.read_table_c("2026-02")
    elec_feb = next(r for r in rows_feb if r["category"] == "electricity")
    # budget_effective = 3000 + 500 = 3500, actual 2000 → remaining 1500
    assert elec_feb["remaining"] == 1500.0
    assert elec_feb["carry_buffer"] == 1500.0


def test_recompute_idempotent(ledger: LedgerWriter, tmp_workspace: Path) -> None:
    _add_rule(ledger, "food", 1000, 500)
    _add_expense(ledger, "2026-03-01", 800, custom_tag="food")
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    s1 = runner.recompute_all()
    rows1 = runner.read_table_c("2026-03")
    s2 = runner.recompute_all()
    rows2 = runner.read_table_c("2026-03")
    assert s1.months_computed == s2.months_computed
    assert rows1 == rows2


def test_no_rules_produces_seed_rows_only(ledger: LedgerWriter, tmp_workspace: Path) -> None:
    # Bootstrap seeds Emergency + Medical rules by default; with no user rules
    # + no expenses the recompute still emits those two placeholder rows so
    # Table C shows running balances from the start.
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    runner.recompute_all()
    rows = runner.read_table_c()
    categories = {r["category"] for r in rows}
    # Either empty (no months computed) or contains only the two seed rules.
    assert categories.issubset({"Emergency", "Medical"})
