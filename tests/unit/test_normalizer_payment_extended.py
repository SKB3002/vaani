"""Extended payment-method normalizer cases (v2 — 5 values)."""
from __future__ import annotations

from app.services.imports import normalizer as n


def test_exact_paid_cash() -> None:
    assert n.normalize_payment_method("Paid Cash") == "paid_cash"
    assert n.normalize_payment_method("paid_cash") == "paid_cash"


def test_exact_paid() -> None:
    assert n.normalize_payment_method("Paid") == "paid"
    assert n.normalize_payment_method("PAID") == "paid"


def test_exact_paid_by() -> None:
    assert n.normalize_payment_method("Paid By") == "paid_by"
    assert n.normalize_payment_method("paid_by") == "paid_by"


def test_exact_paid_for() -> None:
    assert n.normalize_payment_method("Paid For") == "paid_for"
    assert n.normalize_payment_method("paid_for") == "paid_for"


def test_exact_adjusted_variants() -> None:
    assert n.normalize_payment_method("Adjusted") == "adjusted"
    assert n.normalize_payment_method("adjustment") == "adjusted"
    assert n.normalize_payment_method("adj") == "adjusted"


def test_contains_heuristic_upi_and_similar() -> None:
    assert n.normalize_payment_method("UPI") == "paid"
    assert n.normalize_payment_method("GPay") == "paid"
    assert n.normalize_payment_method("PhonePe") == "paid"
    assert n.normalize_payment_method("net banking") == "paid"
    assert n.normalize_payment_method("card") == "paid"


def test_contains_heuristic_cash_alone_is_paid_cash() -> None:
    assert n.normalize_payment_method("cash") == "paid_cash"


def test_unknown_returns_none() -> None:
    assert n.normalize_payment_method("gibberish") is None


def test_parse_payment_dual_maps_all_variants() -> None:
    assert n.parse_payment_dual("Paid Cash") == "paid_cash"
    assert n.parse_payment_dual("Paid") == "paid"
    assert n.parse_payment_dual("Paid By") == "paid_by"
    assert n.parse_payment_dual("Paid For") == "paid_for"
    assert n.parse_payment_dual("Adjusted") == "adjusted"
    assert n.parse_payment_dual("Total") == n.PAYMENT_TOTAL_SENTINEL
    assert n.parse_payment_dual("") is None
