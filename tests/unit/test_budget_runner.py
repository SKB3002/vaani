"""Budget runner: orchestrates running-state engine + writes Table C / budget_state."""
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


def _add_expense(
    ledger: LedgerWriter,
    date: str,
    amount: float,
    type_category: str | None = None,
    custom_tag: str | None = None,
) -> None:
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


def _today_month(runner: BudgetRunner) -> str:
    return runner._current_month()  # noqa: SLF001 — test helper


def test_recompute_seeds_pool_and_tracks_current_month_actual(
    ledger: LedgerWriter, tmp_workspace: Path
) -> None:
    _add_rule(ledger, "electricity", 3000, 4000)
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")

    # First recompute seeds the pool: budget=3000, no expenses yet
    runner.recompute_all()
    rows = runner.read_table_c()
    elec = next(r for r in rows if r["category"] == "electricity")
    assert elec["budget"] == 3000.0
    assert elec["actual"] == 0.0
    assert elec["remaining"] == 3000.0

    # Add a current-month expense → actual reflects it, remaining drops
    today = _today_month(runner)
    _add_expense(ledger, f"{today}-10", 800, custom_tag="electricity")
    runner.recompute_all()
    rows = runner.read_table_c()
    elec = next(r for r in rows if r["category"] == "electricity")
    assert elec["actual"] == 800.0
    assert elec["remaining"] == 2200.0


def test_recompute_idempotent_no_double_top_up(
    ledger: LedgerWriter, tmp_workspace: Path
) -> None:
    """Running recompute twice in the same month must not double the budget pool."""
    _add_rule(ledger, "food", 1000, 500)
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")

    runner.recompute_all()
    rows1 = runner.read_table_c()

    runner.recompute_all()
    rows2 = runner.read_table_c()

    food1 = next(r for r in rows1 if r["category"] == "food")
    food2 = next(r for r in rows2 if r["category"] == "food")
    assert food1["budget"] == food2["budget"] == 1000.0


def test_no_rules_produces_seed_rows_only(
    ledger: LedgerWriter, tmp_workspace: Path
) -> None:
    """Bootstrap seeds Emergency + Medical with monthly_budget=0 → they appear
    in Table C with budget=0."""
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    runner.recompute_all()
    rows = runner.read_table_c()
    categories = {r["category"] for r in rows}
    # Default seed adds Emergency + Medical only
    assert categories == {"Emergency", "Medical"}


def test_apply_adjustment_add(
    ledger: LedgerWriter, tmp_workspace: Path
) -> None:
    _add_rule(ledger, "Medical", 0, 0, priority=91)
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    runner.recompute_all()  # seeds state with current_budget=0

    state = runner.apply_adjustment("Medical", 500.0, "add", note="set aside")
    assert state.current_budget == 500.0

    state = runner.apply_adjustment("Medical", 200.0, "add")
    assert state.current_budget == 700.0

    runner.recompute_all()
    rows = runner.read_table_c()
    med = next(r for r in rows if r["category"] == "Medical")
    assert med["budget"] == 700.0


def test_apply_adjustment_set_overwrites(
    ledger: LedgerWriter, tmp_workspace: Path
) -> None:
    _add_rule(ledger, "Utilities", 7000, 0)
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    runner.recompute_all()  # pool = 7000

    state = runner.apply_adjustment("Utilities", 3000.0, "set", note="reset to 3k")
    assert state.current_budget == 3000.0

    runner.recompute_all()
    rows = runner.read_table_c()
    util = next(r for r in rows if r["category"] == "Utilities")
    assert util["budget"] == 3000.0


def test_adjustment_logged_to_audit_table(
    ledger: LedgerWriter, tmp_workspace: Path
) -> None:
    _add_rule(ledger, "Medical", 0, 0)
    runner = BudgetRunner(ledger, tmp_workspace / "data", timezone="Asia/Kolkata")
    runner.recompute_all()

    runner.apply_adjustment("Medical", 500.0, "add", note="manual top-up")

    df = ledger.read("budget_adjustments")
    assert len(df) == 1
    assert df.iloc[0]["category"] == "Medical"
    assert df.iloc[0]["amount"] == 500.0
    assert df.iloc[0]["kind"] == "add"
    assert df.iloc[0]["note"] == "manual top-up"
