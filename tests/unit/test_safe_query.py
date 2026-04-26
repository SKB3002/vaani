"""Adversarial tests for the filter sanitiser."""
from __future__ import annotations

import pytest

from app.services.charts.safe_query import UnsafeFilterError, validate_filter


@pytest.mark.parametrize(
    "expr",
    [
        "type == 'Need'",
        "amount > 100",
        "date >= '2026-04-01' and category == 'Travel'",
        "category in ['Travel', 'Food & Drinks']",
        "amount >= 10 and amount <= 1000",
        "not (amount < 0)",
        "type != 'Want' or category == 'Travel'",
    ],
)
def test_valid(expr: str) -> None:
    assert validate_filter(expr) == expr


@pytest.mark.parametrize(
    "expr",
    [
        "__import__('os').system('rm -rf /')",
        "df.drop()",
        "eval('1+1')",
        "os.system('x')",
        "open('/etc/passwd').read()",
        "amount.__class__",
        "amount + 5 == 10",            # arithmetic not allowed
        "[x for x in range(10)]",      # comprehension
        "lambda x: x",                 # lambda
        "(a := 5)",                    # walrus
        "amount[0] > 0",               # subscript
        "type == 'Need' ; import os",  # multiple statements / syntax error
    ],
)
def test_rejected(expr: str) -> None:
    with pytest.raises(UnsafeFilterError):
        validate_filter(expr)


def test_empty() -> None:
    with pytest.raises(UnsafeFilterError):
        validate_filter("")
