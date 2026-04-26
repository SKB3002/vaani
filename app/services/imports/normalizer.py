"""Row-level value cleaners for imports.

- INR currency (strip and commas, including Indian 1,23,456 grouping)
- Enum coercion (Need/Want/Investment, categories)
- Date parsing (dateutil + user-supplied format)
- Payment-method heuristic
- Combined "type:category" handling
- Preset-aware helpers:
    - `parse_combined_tags(value)` — parses "Travel, Needs" / "Wants, Miscellaneous"
      / single-tag variants. Canonicalises plural->singular for types and accepts
      category aliases (food, transport, fun, misc, other). Returns
      (type_category_string | None, warnings).
    - `parse_payment_dual(value)` — maps "Paid Cash" -> "cash", "Paid" -> "paid",
      "Total" -> "__total__" (sentinel for summary-row skip), blank -> None.

Synthetic mapping target names used by import presets (handled by the committer,
NOT real schema columns):
    __payment_dual     — value is "Paid" / "Paid Cash" / "Total"
    __tags_combined    — value is "Travel, Needs" / "Wants" / etc.
    __cash_snapshot    — daily cash-balance snapshot (used for checksum)
    __online_snapshot  — daily online-balance snapshot (used for checksum)
"""
from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from dateutil import parser as dateutil_parser

from app.models.common import VALID_CATEGORIES, VALID_TYPES

_CURRENCY_STRIP_RE = re.compile(r"[₹$€£,\s]")
_PAID_HINTS_RE = re.compile(r"\b(upi|online|card|net[\s-]?banking|imps|neft|rtgs)\b", re.IGNORECASE)
_CASH_HINTS_RE = re.compile(r"\bcash\b", re.IGNORECASE)


def clean_amount(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, int | float):
        return float(value)
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    s = _CURRENCY_STRIP_RE.sub("", s)
    try:
        return float(s)
    except ValueError:
        return None


def clean_string(value: Any) -> str | None:
    if value is None:
        return None
    s = str(value).strip()
    if not s or s.lower() in {"nan", "none", "null"}:
        return None
    return s


# Aliases used by both combine_type_category and parse_combined_tags
_TYPE_ALIASES_EARLY: dict[str, str] = {
    "need": "Need", "needs": "Need",
    "want": "Want", "wants": "Want",
    "investment": "Investment", "investments": "Investment", "invest": "Investment",
}
_CATEGORY_ALIASES_EARLY: dict[str, str] = {
    "food & drinks": "Food & Drinks", "food and drinks": "Food & Drinks",
    "food": "Food & Drinks", "drinks": "Food & Drinks",
    "travel": "Travel", "transport": "Travel", "transportation": "Travel",
    "enjoyment": "Enjoyment", "fun": "Enjoyment", "entertainment": "Enjoyment",
    "miscellaneous": "Miscellaneous", "misc": "Miscellaneous",
    "other": "Miscellaneous", "others": "Miscellaneous",
}


def _resolve_type(token: str) -> str | None:
    lower = token.strip().lower()
    if lower in _TYPE_ALIASES_EARLY:
        return _TYPE_ALIASES_EARLY[lower]
    for t in VALID_TYPES:
        if t.lower() == lower:
            return t
    return None


def _resolve_category(token: str) -> str | None:
    lower = token.strip().lower()
    if lower in _CATEGORY_ALIASES_EARLY:
        return _CATEGORY_ALIASES_EARLY[lower]
    for c in VALID_CATEGORIES:
        if c.lower() == lower:
            return c
    return None


def normalize_type(value: Any) -> str | None:
    s = clean_string(value)
    return _resolve_type(s) if s else None


def normalize_category(value: Any) -> str | None:
    s = clean_string(value)
    return _resolve_category(s) if s else None


def combine_type_category(
    value: Any, type_col: Any = None, cat_col: Any = None
) -> str | None:
    """Parse a combined 'Type, Category' string (any order, aliases accepted).

    Accepts:
      - "Need, Food & Drinks"      (canonical)
      - "Food & Drinks, Needs"     (reversed + plural alias)
      - "Need:Food & Drinks"       (legacy colon)
      - separate type_col + cat_col args as fallback
    """
    combined = clean_string(value)
    if combined:
        sep = ", " if ", " in combined else (":" if ":" in combined else None)
        if sep is not None:
            parts = [p.strip() for p in combined.split(sep, 1)]
            if len(parts) == 2:
                left, right = parts[0], parts[1]
                # Try both orderings with full alias support
                t = _resolve_type(left) or _resolve_type(right)
                c = _resolve_category(left) or _resolve_category(right)
                if t and c:
                    return f"{t}, {c}"
            return None

    # Fallback: separate columns
    t = _resolve_type(clean_string(type_col) or "") if type_col is not None else None
    c = _resolve_category(clean_string(cat_col) or "") if cat_col is not None else None
    if t and c:
        return f"{t}, {c}"
    return None


def normalize_payment_method(value: Any) -> str | None:
    """Normalize to the 5-value enum: paid | paid_cash | paid_by | paid_for | adjusted.

    Exact matches first, then contains-heuristics. Returns None if nothing matches.
    """
    s = clean_string(value)
    if s is None:
        return None
    low = " ".join(s.lower().split())

    # Exact matches (priority)
    exact_map = {
        "paid cash": "paid_cash",
        "paid_cash": "paid_cash",
        "paid by": "paid_by",
        "paid_by": "paid_by",
        "paid for": "paid_for",
        "paid_for": "paid_for",
        "adjusted": "adjusted",
        "adjustment": "adjusted",
        "adj": "adjusted",
        "paid": "paid",
    }
    if low in exact_map:
        return exact_map[low]

    # Contains heuristics (fall-through)
    if _CASH_HINTS_RE.search(low):
        return "paid_cash"
    if _PAID_HINTS_RE.search(low) or any(
        tok in low for tok in ("gpay", "phonepe", "phone pe")
    ):
        return "paid"
    return None


def parse_date(value: Any, user_format: str | None = None) -> date | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    s = clean_string(value)
    if s is None:
        return None
    if user_format:
        try:
            return datetime.strptime(s, user_format).date()
        except ValueError:
            pass
    try:
        return dateutil_parser.parse(s, dayfirst=True).date()
    except (ValueError, OverflowError, dateutil_parser.ParserError):
        return None


# ---------- preset-aware helpers ----------

PAYMENT_TOTAL_SENTINEL = "__total__"

# Aliases re-exported under the old names so parse_combined_tags can use shared helpers
_canonical_type = _resolve_type
_canonical_category = _resolve_category


def parse_combined_tags(value: Any) -> tuple[str | None, list[str]]:
    """Parse the user's "Tags" column value into a canonical ``Type, Category``.

    Returns ``(type_category_str | None, warnings)``. Unknown tokens yield
    ``(None, [error])``; single-tag shortcuts yield a warning.
    """
    s = clean_string(value)
    warnings: list[str] = []
    if s is None:
        return None, ["missing tags"]

    parts = [p.strip() for p in s.split(",") if p.strip()]
    if not parts:
        return None, ["missing tags"]

    if len(parts) == 1:
        token = parts[0]
        t = _canonical_type(token)
        c = _canonical_category(token)
        if t and not c:
            warnings.append(f"single-tag '{token}' is a type; defaulting category to Miscellaneous")
            return f"{t}, Miscellaneous", warnings
        if c and not t:
            warnings.append(f"single-tag '{token}' is a category; defaulting type to Need")
            return f"Need, {c}", warnings
        return None, [f"unknown tag: '{token}'"]

    if len(parts) > 2:
        warnings.append(f"more than two tags; using first two: {parts[:2]}")
        parts = parts[:2]

    left, right = parts[0], parts[1]
    t = _canonical_type(left) or _canonical_type(right)
    c = _canonical_category(left) or _canonical_category(right)

    if t and c:
        return f"{t}, {c}", warnings
    if t and not c:
        warnings.append(f"unknown category alongside type '{t}'; defaulting to Miscellaneous")
        return f"{t}, Miscellaneous", warnings
    if c and not t:
        warnings.append(f"unknown type alongside category '{c}'; defaulting to Need")
        return f"Need, {c}", warnings
    return None, [f"unknown tags: {parts}"]


def parse_payment_dual(value: Any) -> str | None:
    """Map the user's "Payment" column to the 5-value enum.

    - "Paid Cash"              -> "paid_cash"
    - "Paid"                   -> "paid"
    - "Paid By"                -> "paid_by"
    - "Paid For"               -> "paid_for"
    - "Adjusted"/"Adjustment"  -> "adjusted"
    - "Total"                  -> sentinel (``PAYMENT_TOTAL_SENTINEL``) — caller skips the row
    - blank/None               -> None (may indicate a balance-adjust row)
    """
    s = clean_string(value)
    if s is None:
        return None
    low = " ".join(s.lower().split())
    if "total" in low:
        return PAYMENT_TOTAL_SENTINEL
    return normalize_payment_method(s)


def coerce_bool(value: Any) -> bool:
    if isinstance(value, bool):
        return value
    s = clean_string(value)
    if s is None:
        return False
    return s.lower() in {"true", "yes", "y", "1"}
