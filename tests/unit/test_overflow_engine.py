"""Pure overflow engine tests — §7.3 worked example + hypothesis invariants."""
from __future__ import annotations

import pandas as pd
import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st

from app.models.budget import BudgetRule, CapsConfig
from app.services.overflow import compute_month


def _exp_df(rows: list[dict]) -> pd.DataFrame:
    if not rows:
        return pd.DataFrame({"amount": pd.array([], dtype="float64"),
                             "type_category": pd.array([], dtype="string"),
                             "custom_tag": pd.array([], dtype="string")})
    df = pd.DataFrame(rows)
    for col in ("type_category", "custom_tag"):
        if col not in df.columns:
            df[col] = None
    return df


def test_single_rule_remaining_carries_500() -> None:
    # §7.3 Month 1: budget 3000, actual 2500 → remaining 500 → carry 500, overflow 0
    rule = BudgetRule(category="electricity", monthly_budget=3000, carry_cap=4000, priority=1)
    caps = CapsConfig(medical_upper_cap=10000, emergency_monthly_cap=5000)
    expenses = _exp_df([{"amount": 2500.0, "type_category": None, "custom_tag": "electricity"}])
    res = compute_month("2026-01", [rule], expenses, {}, caps, 0, 0)
    assert len(res.rows) == 1
    row = res.rows[0]
    assert row.budget == 3000
    assert row.actual == 2500
    assert row.remaining == 500
    assert row.carry_buffer == 500
    assert row.overflow == 0
    assert res.next_carry["electricity"] == 500
    assert res.med_balance_out == 0
    assert res.emerg_balance_out == 0


def test_four_month_cascade_worked_example() -> None:
    """Replay §7.3 — carry grows, then overflow starts flowing to medical."""
    rule = BudgetRule(category="electricity", monthly_budget=3000, carry_cap=4000, priority=1)
    caps = CapsConfig(medical_upper_cap=10000, emergency_monthly_cap=5000)

    # Month 1: actual 2500
    r1 = compute_month("2026-01", [rule],
                      _exp_df([{"amount": 2500.0, "custom_tag": "electricity"}]),
                      {}, caps, 0, 0)
    assert r1.rows[0].carry_buffer == 500
    assert r1.rows[0].overflow == 0

    # Month 2: budget_effective = 3500, actual 2000 → remaining 1500, carry 1500
    r2 = compute_month("2026-02", [rule],
                      _exp_df([{"amount": 2000.0, "custom_tag": "electricity"}]),
                      r1.next_carry, caps, r1.med_balance_out, r1.emerg_balance_out)
    assert r2.rows[0].remaining == 1500
    assert r2.rows[0].carry_buffer == 1500

    # Month 3: budget_effective = 4500, actual 200 → remaining 4300
    # carry_next = min(4300, 4000) = 4000, overflow = 300 → all to emergency (first sink)
    r3 = compute_month("2026-03", [rule],
                      _exp_df([{"amount": 200.0, "custom_tag": "electricity"}]),
                      r2.next_carry, caps, r2.med_balance_out, r2.emerg_balance_out)
    row3 = r3.rows[0]
    assert row3.remaining == 4300
    assert row3.carry_buffer == 4000
    assert row3.overflow == 300
    assert row3.to_emergency == 300
    assert row3.to_medical == 0
    assert row3.emerg_balance == 300

    # Month 4: fills emergency first (cap 5000, 300 already used → 4700 room),
    # remaining 3000 all lands in emergency.
    r4 = compute_month("2026-04", [rule],
                      _exp_df([{"amount": 0.0, "custom_tag": "electricity"}]),
                      r3.next_carry, caps, r3.med_balance_out, r3.emerg_balance_out)
    row4 = r4.rows[0]
    assert row4.overflow == 3000
    assert row4.to_emergency == 3000
    assert row4.emerg_balance == 3300


def test_overflow_lost_when_both_caps_full() -> None:
    rule = BudgetRule(category="X", monthly_budget=1000, carry_cap=0, priority=1)
    caps = CapsConfig(medical_upper_cap=100, emergency_monthly_cap=50)
    # Med starts at 100 (full), emerg at 50 (full), rule actual 0 → remaining 1000 → overflow 1000 lost
    res = compute_month("2026-01", [rule], _exp_df([]), {}, caps, 100, 50)
    assert res.rows[0].overflow == 1000
    assert res.rows[0].to_medical == 0
    assert res.rows[0].to_emergency == 0
    assert any("overflow_lost" in w for w in res.warnings)
    assert res.rows[0].notes and "overflow_lost=1000.00" in res.rows[0].notes


def test_emergency_fills_then_medical() -> None:
    # Cascade order is Emergency → Medical → lost.
    rule = BudgetRule(category="X", monthly_budget=0, carry_cap=0, priority=1)
    caps = CapsConfig(medical_upper_cap=100, emergency_monthly_cap=50)
    # emerg starts at 40 (room 10), medical empty (room 100).
    # overflow = 80 → 10 to emergency, 70 to medical, 0 lost.
    res = compute_month("2026-01", [rule], _exp_df([]), {"X": 80.0}, caps, 0, 40)
    row = res.rows[0]
    assert row.remaining == 80
    assert row.overflow == 80
    assert row.to_emergency == 10
    assert row.to_medical == 70
    assert row.emerg_balance == 50
    assert row.med_balance == 70


def test_over_budget_remaining_negative() -> None:
    rule = BudgetRule(category="food", monthly_budget=1000, carry_cap=500, priority=1)
    caps = CapsConfig()
    res = compute_month("2026-01", [rule],
                       _exp_df([{"amount": 1500.0, "custom_tag": "food"}]),
                       {}, caps, 0, 0)
    row = res.rows[0]
    assert row.remaining == -500
    assert row.carry_buffer == 0
    assert row.overflow == 0
    assert row.notes and "over_budget=500.00" in row.notes


def test_rules_sorted_by_priority_then_category() -> None:
    caps = CapsConfig()
    rules = [
        BudgetRule(category="zeta", monthly_budget=100, carry_cap=0, priority=2),
        BudgetRule(category="alpha", monthly_budget=100, carry_cap=0, priority=1),
        BudgetRule(category="beta", monthly_budget=100, carry_cap=0, priority=1),
    ]
    res = compute_month("2026-01", rules, _exp_df([]), {}, caps, 0, 0)
    assert [r.category for r in res.rows] == ["alpha", "beta", "zeta"]


@given(
    budget=st.floats(min_value=0, max_value=50000, allow_nan=False),
    carry_cap=st.floats(min_value=0, max_value=50000, allow_nan=False),
    actual=st.floats(min_value=0, max_value=100000, allow_nan=False),
    med_in=st.floats(min_value=0, max_value=20000, allow_nan=False),
    emerg_in=st.floats(min_value=0, max_value=10000, allow_nan=False),
    med_cap=st.floats(min_value=0, max_value=20000, allow_nan=False),
    emerg_cap=st.floats(min_value=0, max_value=10000, allow_nan=False),
)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow])
def test_invariants_hypothesis(
    budget: float,
    carry_cap: float,
    actual: float,
    med_in: float,
    emerg_in: float,
    med_cap: float,
    emerg_cap: float,
) -> None:
    rule = BudgetRule(category="X", monthly_budget=budget, carry_cap=carry_cap, priority=1)
    caps = CapsConfig(medical_upper_cap=med_cap, emergency_monthly_cap=emerg_cap)
    med_start = min(med_in, med_cap)
    emerg_start = min(emerg_in, emerg_cap)
    expenses = _exp_df([{"amount": actual, "custom_tag": "X"}])
    res = compute_month("2026-01", [rule], expenses, {}, caps, med_start, emerg_start)
    row = res.rows[0]

    # Invariant 1: total_routed <= remaining (when positive)
    if row.remaining > 0:
        assert row.carry_buffer + row.overflow == pytest.approx(row.remaining, abs=0.02)
        assert row.to_medical + row.to_emergency <= row.overflow + 0.02
    else:
        assert row.carry_buffer == 0
        assert row.overflow == 0

    # Invariant 2: med_balance <= cap
    assert row.med_balance <= med_cap + 0.01
    # Invariant 3: emerg_balance <= cap
    assert row.emerg_balance <= emerg_cap + 0.01
    # Invariant 4: carry_next <= carry_cap
    assert row.carry_buffer <= carry_cap + 0.01
