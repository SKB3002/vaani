"""Category matching modes: suffix / full type_category / custom_tag."""
from __future__ import annotations

import pandas as pd

from app.models.budget import BudgetRule, CapsConfig
from app.services.overflow import compute_month


def _df(rows: list[dict]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for col in ("type_category", "custom_tag"):
        if col not in df.columns:
            df[col] = None
    return df


def test_suffix_match_need_and_want() -> None:
    """Rule 'Food & Drinks' matches Need, Food & Drinks AND Want, Food & Drinks."""
    rule = BudgetRule(category="Food & Drinks", monthly_budget=5000, carry_cap=0, priority=1)
    expenses = _df([
        {"amount": 100, "type_category": "Need, Food & Drinks"},
        {"amount": 200, "type_category": "Want, Food & Drinks"},
        {"amount": 500, "type_category": "Need, Travel"},  # not matched
    ])
    res = compute_month("2026-01", [rule], expenses, {}, CapsConfig(), 0, 0)
    assert res.rows[0].actual == 300.0


def test_full_type_category_match() -> None:
    """Rule 'Need, Travel' matches only Need, Travel (not Want, Travel)."""
    rule = BudgetRule(category="Need, Travel", monthly_budget=2000, carry_cap=0, priority=1)
    expenses = _df([
        {"amount": 400, "type_category": "Need, Travel"},
        {"amount": 600, "type_category": "Want, Travel"},
    ])
    res = compute_month("2026-01", [rule], expenses, {}, CapsConfig(), 0, 0)
    assert res.rows[0].actual == 400.0


def test_custom_tag_match() -> None:
    """Rule 'electricity' falls through to custom_tag when not matched elsewhere."""
    rule = BudgetRule(category="electricity", monthly_budget=3000, carry_cap=0, priority=1)
    expenses = _df([
        {"amount": 1000, "type_category": "Need, Miscellaneous", "custom_tag": "electricity"},
        {"amount": 500, "type_category": "Need, Miscellaneous", "custom_tag": "water"},
    ])
    res = compute_month("2026-01", [rule], expenses, {}, CapsConfig(), 0, 0)
    assert res.rows[0].actual == 1000.0


def test_suffix_also_picks_up_custom_tag() -> None:
    """When rule is suffix-form, BOTH suffix matches and custom_tag matches aggregate."""
    rule = BudgetRule(category="utilities", monthly_budget=3000, carry_cap=0, priority=1)
    expenses = _df([
        {"amount": 300, "type_category": "Need, Miscellaneous", "custom_tag": "utilities"},
        # no expense ends in ':utilities' since utilities isn't a valid category
        {"amount": 700, "type_category": "Need, Food & Drinks", "custom_tag": None},
    ])
    res = compute_month("2026-01", [rule], expenses, {}, CapsConfig(), 0, 0)
    assert res.rows[0].actual == 300.0
