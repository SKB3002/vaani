"""Normalizer cleaners."""
from __future__ import annotations

from datetime import date

from app.services.imports import normalizer as n


def test_clean_amount_strips_inr_and_commas() -> None:
    assert n.clean_amount("₹1,23,456.50") == 123456.50
    assert n.clean_amount("₹ 1,200 ") == 1200.0
    assert n.clean_amount(" 500") == 500.0
    assert n.clean_amount(None) is None
    assert n.clean_amount("abc") is None


def test_normalize_type_case_insensitive() -> None:
    assert n.normalize_type("need") == "Need"
    assert n.normalize_type(" NEED ") == "Need"
    assert n.normalize_type("investment") == "Investment"
    assert n.normalize_type("random") is None


def test_normalize_category_case_insensitive() -> None:
    assert n.normalize_category("food & drinks") == "Food & Drinks"
    assert n.normalize_category("TRAVEL") == "Travel"


def test_combine_type_category_from_combined_string() -> None:
    assert n.combine_type_category("Need, Food & Drinks") == "Need, Food & Drinks"
    assert n.combine_type_category("need : food & drinks") == "Need, Food & Drinks"


def test_combine_type_category_from_separate_columns() -> None:
    assert (
        n.combine_type_category(None, type_col="Want", cat_col="Enjoyment")
        == "Want, Enjoyment"
    )


def test_payment_method_heuristic() -> None:
    assert n.normalize_payment_method("cash") == "paid_cash"
    assert n.normalize_payment_method("UPI") == "paid"
    assert n.normalize_payment_method("Net Banking") == "paid"
    assert n.normalize_payment_method("credit card") == "paid"
    assert n.normalize_payment_method("random") is None


def test_parse_date_multiple_formats() -> None:
    assert n.parse_date("2026-04-23") == date(2026, 4, 23)
    assert n.parse_date("23/04/2026") == date(2026, 4, 23)
    assert n.parse_date("23-Apr-2026") == date(2026, 4, 23)
    assert n.parse_date("23/04/2026", user_format="%d/%m/%Y") == date(2026, 4, 23)
    assert n.parse_date("not a date") is None


def test_coerce_bool() -> None:
    assert n.coerce_bool(True) is True
    assert n.coerce_bool("yes") is True
    assert n.coerce_bool("True") is True
    assert n.coerce_bool("no") is False
    assert n.coerce_bool(None) is False
