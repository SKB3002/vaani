"""Tests for the running-state engine — per-category persistent pool."""
from __future__ import annotations

import pandas as pd

from app.models.budget import BudgetRule, CapsConfig, RunningCategoryState
from app.services.overflow import compute_running_state


def _exp_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({
            "amount": pd.array([], dtype="float64"),
            "type_category": pd.array([], dtype="string"),
            "custom_tag": pd.array([], dtype="string"),
            "date": pd.array([], dtype="string"),
        })
    df = pd.DataFrame(rows)
    for col in ("type_category", "custom_tag"):
        if col not in df.columns:
            df[col] = None
    return df


def test_first_run_seeds_pool_with_monthly_budget() -> None:
    """First recompute (no prior state) → current_budget = monthly_budget,
    no historical replay even if expenses exist in past months."""
    rule = BudgetRule(category="Utilities", monthly_budget=3000, carry_cap=2000, priority=1)
    expenses = _exp_df([
        {"amount": 500, "custom_tag": "Utilities", "date": "2026-01-15"},  # past month, ignored
    ])
    res = compute_running_state(
        current_month="2026-05",
        rules=[rule],
        expenses=expenses,
        prior_state={},
        caps=CapsConfig(),
        med_in=0,
        emerg_in=0,
        now_iso="2026-05-01T00:00:00",
    )
    assert len(res.rows) == 1
    row = res.rows[0]
    assert row.budget == 3000
    assert row.actual == 0  # no expenses in 2026-05
    assert row.remaining == 3000
    state = res.new_state[0]
    assert state.current_budget == 3000
    assert state.last_rolled_month == "2026-05"


def test_actual_uses_only_current_month_expenses() -> None:
    rule = BudgetRule(category="Utilities", monthly_budget=3000, carry_cap=0, priority=1)
    expenses = _exp_df([
        {"amount": 500, "custom_tag": "Utilities", "date": "2026-04-10"},  # past
        {"amount": 800, "custom_tag": "Utilities", "date": "2026-05-10"},  # current
        {"amount": 200, "custom_tag": "Utilities", "date": "2026-05-25"},  # current
    ])
    prior = {"Utilities": RunningCategoryState(
        category="Utilities", current_budget=3000, last_rolled_month="2026-05",
    )}
    res = compute_running_state(
        current_month="2026-05",
        rules=[rule],
        expenses=expenses,
        prior_state=prior,
        caps=CapsConfig(),
        med_in=0, emerg_in=0,
        now_iso="2026-05-26T00:00:00",
    )
    row = res.rows[0]
    assert row.actual == 1000  # only current month
    assert row.remaining == 2000


def test_month_rollover_carries_within_cap() -> None:
    """Apr → May: spent ₹2000 of ₹3000 budget, ₹1000 leftover, carry_cap=2000
    → all ₹1000 stays as carry, May pool becomes 1000+3000=4000."""
    rule = BudgetRule(category="Utilities", monthly_budget=3000, carry_cap=2000, priority=1)
    expenses = _exp_df([
        {"amount": 2000, "custom_tag": "Utilities", "date": "2026-04-10"},
    ])
    prior = {"Utilities": RunningCategoryState(
        category="Utilities", current_budget=3000, last_rolled_month="2026-04",
    )}
    res = compute_running_state(
        current_month="2026-05",
        rules=[rule],
        expenses=expenses,
        prior_state=prior,
        caps=CapsConfig(),
        med_in=0, emerg_in=0,
        now_iso="2026-05-01T00:00:00",
    )
    row = res.rows[0]
    assert row.budget == 4000  # 1000 carry + 3000 new
    assert row.actual == 0  # nothing spent in May yet
    assert row.remaining == 4000
    assert row.carry_buffer == 1000
    assert row.overflow == 0
    state = res.new_state[0]
    assert state.current_budget == 4000


def test_month_rollover_overflow_to_emergency() -> None:
    """Apr→May: pool=3000, spent=500, leftover=2500, carry_cap=1000
    → 1000 carry, 1500 overflow → all goes to Emergency (cap 5000)."""
    rule = BudgetRule(category="Utilities", monthly_budget=3000, carry_cap=1000, priority=1)
    expenses = _exp_df([
        {"amount": 500, "custom_tag": "Utilities", "date": "2026-04-10"},
    ])
    prior = {"Utilities": RunningCategoryState(
        category="Utilities", current_budget=3000, last_rolled_month="2026-04",
    )}
    res = compute_running_state(
        current_month="2026-05",
        rules=[rule],
        expenses=expenses,
        prior_state=prior,
        caps=CapsConfig(medical_upper_cap=10000, emergency_monthly_cap=5000),
        med_in=0, emerg_in=0,
        now_iso="2026-05-01T00:00:00",
    )
    row = res.rows[0]
    assert row.carry_buffer == 1000
    assert row.overflow == 1500
    assert row.to_emergency == 1500
    assert row.to_medical == 0
    assert row.budget == 1000 + 3000  # carry + new month
    assert res.emerg_balance_out == 1500


def test_overflow_cascades_to_medical_when_emergency_full() -> None:
    rule = BudgetRule(category="X", monthly_budget=0, carry_cap=0, priority=1)
    expenses = _exp_df([])
    # Pool of 8000 with no spend, all overflows. Emergency room = 50, medical room = 100.
    prior = {"X": RunningCategoryState(
        category="X", current_budget=8000, last_rolled_month="2026-04",
    )}
    res = compute_running_state(
        current_month="2026-05",
        rules=[rule],
        expenses=expenses,
        prior_state=prior,
        caps=CapsConfig(medical_upper_cap=100, emergency_monthly_cap=50),
        med_in=0, emerg_in=0,
        now_iso="2026-05-01T00:00:00",
    )
    row = res.rows[0]
    assert row.to_emergency == 50
    assert row.to_medical == 100
    assert any("overflow_lost" in w for w in res.warnings)


def test_no_double_top_up_within_same_month() -> None:
    """Calling compute twice in May with same prior_state shouldn't add monthly_budget twice."""
    rule = BudgetRule(category="Utilities", monthly_budget=3000, carry_cap=0, priority=1)
    expenses = _exp_df([])

    # First call — fresh, seeds pool to 3000
    r1 = compute_running_state(
        current_month="2026-05", rules=[rule], expenses=expenses,
        prior_state={}, caps=CapsConfig(), med_in=0, emerg_in=0,
        now_iso="2026-05-10T00:00:00",
    )
    assert r1.rows[0].budget == 3000

    # Second call — uses prior_state, no rollover (still May)
    state_dict = {s.category: s for s in r1.new_state}
    r2 = compute_running_state(
        current_month="2026-05", rules=[rule], expenses=expenses,
        prior_state=state_dict, caps=CapsConfig(), med_in=0, emerg_in=0,
        now_iso="2026-05-11T00:00:00",
    )
    assert r2.rows[0].budget == 3000  # not 6000


def test_multi_month_gap_replays_each_month() -> None:
    """User opens app in July after last using it in April → cascade May, June, July."""
    rule = BudgetRule(category="Utilities", monthly_budget=1000, carry_cap=500, priority=1)
    expenses = _exp_df([
        {"amount": 200, "custom_tag": "Utilities", "date": "2026-05-10"},
        {"amount": 0, "custom_tag": "Utilities", "date": "2026-06-10"},
    ])
    # April-end pool = 1000, no spend in April recorded
    prior = {"Utilities": RunningCategoryState(
        category="Utilities", current_budget=1000, last_rolled_month="2026-04",
    )}
    res = compute_running_state(
        current_month="2026-07",
        rules=[rule],
        expenses=expenses,
        prior_state=prior,
        caps=CapsConfig(medical_upper_cap=10000, emergency_monthly_cap=5000),
        med_in=0, emerg_in=0,
        now_iso="2026-07-01T00:00:00",
    )
    # Apr→May: leftover=1000-0=1000, carry=500, overflow=500 → emerg=500.
    #          New pool = 500 + 1000 = 1500.
    # May→Jun: leftover=1500-200=1300, carry=500, overflow=800 → emerg=1300.
    #          New pool = 500 + 1000 = 1500.
    # Jun→Jul: leftover=1500-0=1500, carry=500, overflow=1000 → emerg=2300.
    #          New pool = 500 + 1000 = 1500.
    row = res.rows[0]
    assert row.budget == 1500
    assert res.emerg_balance_out == 2300


def test_case_insensitive_matching() -> None:
    rule = BudgetRule(category="Utilities", monthly_budget=3000, carry_cap=0, priority=1)
    expenses = _exp_df([
        {"amount": 100, "type_category": "Need, utilities", "date": "2026-05-10"},
        {"amount": 200, "type_category": "WANT, Utilities", "date": "2026-05-11"},
        {"amount": 300, "custom_tag": "UTILITIES", "date": "2026-05-12"},
        {"amount": 50, "custom_tag": "utilities", "date": "2026-05-13"},
    ])
    prior = {"Utilities": RunningCategoryState(
        category="Utilities", current_budget=3000, last_rolled_month="2026-05",
    )}
    res = compute_running_state(
        current_month="2026-05", rules=[rule], expenses=expenses,
        prior_state=prior, caps=CapsConfig(), med_in=0, emerg_in=0,
        now_iso="2026-05-15T00:00:00",
    )
    assert res.rows[0].actual == 650


def test_over_budget_remaining_negative_pool_clamped_on_rollover() -> None:
    """If user overspends in April, the rollover into May resets pool to monthly_budget
    (no carry, no negative leftover propagation)."""
    rule = BudgetRule(category="X", monthly_budget=1000, carry_cap=500, priority=1)
    expenses = _exp_df([
        {"amount": 1500, "custom_tag": "X", "date": "2026-04-10"},
    ])
    prior = {"X": RunningCategoryState(
        category="X", current_budget=1000, last_rolled_month="2026-04",
    )}
    res = compute_running_state(
        current_month="2026-05", rules=[rule], expenses=expenses,
        prior_state=prior, caps=CapsConfig(), med_in=0, emerg_in=0,
        now_iso="2026-05-01T00:00:00",
    )
    row = res.rows[0]
    assert row.budget == 1000  # only the new monthly_budget; no carry from negative
    assert row.carry_buffer == 0
    assert row.overflow == 0
