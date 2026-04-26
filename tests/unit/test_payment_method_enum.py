"""Pydantic payment_method enum: accepts v2 values, rejects v1."""
from __future__ import annotations

from datetime import date

import pytest
from pydantic import ValidationError

from app.models.common import PaymentMethod
from app.models.expense import ExpenseIn


def _base(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "date": date(2026, 4, 23),
        "expense_name": "Zomato",
        "type_category": "Want, Food & Drinks",
        "payment_method": "paid",
        "amount": 250.0,
    }
    row.update(overrides)
    return row


def test_enum_has_exact_five_values() -> None:
    names = {m.value for m in PaymentMethod}
    assert names == {"paid", "paid_cash", "paid_by", "paid_for", "adjusted"}


@pytest.mark.parametrize(
    "value,extra",
    [
        ("paid", {}),
        ("paid_cash", {}),
        ("paid_by", {"person_name": "Arjun", "paid_by_someone": True}),
        ("paid_for", {"paid_for_method": "online", "person_name": "Priya", "paid_for_someone": True}),
        ("adjusted", {"adjustment_type": "cash_to_online"}),
    ],
)
def test_accepts_new_values(value: str, extra: dict[str, object]) -> None:
    m = ExpenseIn(**_base(payment_method=value, **extra))
    assert m.payment_method.value == value


@pytest.mark.parametrize("legacy", ["cash", "online"])
def test_rejects_legacy_values(legacy: str) -> None:
    with pytest.raises(ValidationError):
        ExpenseIn(**_base(payment_method=legacy))


def test_paid_for_defaults_method_to_online() -> None:
    # Grid UI doesn't surface paid_for_method — default kicks in.
    m = ExpenseIn(**_base(payment_method="paid_for", person_name="X", paid_for_someone=True))
    assert m.paid_for_method == "online"


def test_adjusted_defaults_direction_to_cash_to_online() -> None:
    m = ExpenseIn(**_base(payment_method="adjusted"))
    assert m.adjustment_type == "cash_to_online"


def test_paid_for_method_cleared_on_other_pm() -> None:
    # Sub-field silently cleared when irrelevant instead of raising.
    m = ExpenseIn(**_base(payment_method="paid", paid_for_method="cash"))
    assert m.paid_for_method is None


def test_adjustment_type_cleared_on_other_pm() -> None:
    m = ExpenseIn(**_base(payment_method="paid", adjustment_type="cash_to_online"))
    assert m.adjustment_type is None
