"""Common enums, regex validators, and shared types."""
from __future__ import annotations

import re
from enum import StrEnum

TYPE_CATEGORY_RE = re.compile(
    r"^(Need|Want|Investment), (Food & Drinks|Travel|Enjoyment|Miscellaneous)$"
)
TYPE_CATEGORY_SEP = ", "

VALID_TYPES = ("Need", "Want", "Investment")
VALID_CATEGORIES = ("Food & Drinks", "Travel", "Enjoyment", "Miscellaneous")


class PaymentMethod(StrEnum):
    paid = "paid"
    paid_cash = "paid_cash"
    paid_by = "paid_by"
    paid_for = "paid_for"
    adjusted = "adjusted"


class Source(StrEnum):
    voice = "voice"
    manual = "manual"
    atm_transfer = "atm_transfer"
    import_ = "import"


def is_valid_type_category(value: str) -> bool:
    return bool(TYPE_CATEGORY_RE.match(value))
