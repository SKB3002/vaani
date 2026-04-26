"""Seed ~60 days of realistic INR expenses for demo screenshots.

Run: `python -m scripts.seed`
"""
from __future__ import annotations

import random
from datetime import UTC, date, datetime, timedelta
from typing import Any

import ulid

from app.bootstrap import bootstrap
from app.deps import get_balance_service, get_ledger

# (vendor, type:category, typical_range_inr, cash_prob)
# cash_prob: probability that the row is paid_cash rather than paid
VENDORS: list[tuple[str, str, tuple[int, int], float]] = [
    # Food & drinks
    ("Zomato - Biryani Blues", "Want, Food & Drinks", (280, 650), 0.05),
    ("Swiggy - Dominos", "Want, Food & Drinks", (350, 900), 0.05),
    ("Swiggy Instamart", "Need, Food & Drinks", (180, 1200), 0.05),
    ("Brotherhood Cafe", "Want, Food & Drinks", (150, 450), 0.40),
    ("BigBasket groceries", "Need, Food & Drinks", (900, 3500), 0.05),
    ("Nature's Basket", "Need, Food & Drinks", (450, 1400), 0.15),
    ("Local kirana", "Need, Food & Drinks", (80, 350), 0.85),
    ("Blue Tokai coffee", "Want, Food & Drinks", (220, 380), 0.20),
    # Travel
    ("HPCL petrol pump", "Need, Travel", (300, 900), 0.30),
    ("Indian Oil fuel", "Need, Travel", (250, 800), 0.30),
    ("Uber ride", "Need, Travel", (120, 480), 0.02),
    ("Ola auto", "Need, Travel", (60, 220), 0.40),
    ("BMTC bus pass", "Need, Travel", (1050, 1050), 0.60),
    ("Namma Metro", "Need, Travel", (30, 90), 0.50),
    # Enjoyment
    ("PVR Cinemas", "Want, Enjoyment", (320, 700), 0.05),
    ("Spotify premium", "Want, Enjoyment", (119, 119), 0.02),
    ("Netflix", "Want, Enjoyment", (199, 649), 0.02),
    ("Bar at Arbor", "Want, Enjoyment", (800, 2400), 0.10),
    ("Concert ticket", "Want, Enjoyment", (1500, 4500), 0.05),
    # Misc
    ("Electricity (BESCOM)", "Need, Miscellaneous", (1800, 3400), 0.05),
    ("Mobile recharge (Jio)", "Need, Miscellaneous", (299, 799), 0.02),
    ("Gas cylinder", "Need, Miscellaneous", (950, 1100), 0.70),
    ("Apollo Pharmacy", "Need, Miscellaneous", (120, 850), 0.30),
    ("Amazon - stationery", "Want, Miscellaneous", (250, 1200), 0.05),
    ("Urban Company", "Want, Miscellaneous", (400, 1500), 0.05),
    # Investments — per §4.1 SIP/FD/crypto live in expenses too
    ("SIP - Axis Bluechip Fund", "Investment, Miscellaneous", (5000, 5000), 0.02),
    ("SIP - Parag Parikh Flexi Cap", "Investment, Miscellaneous", (3000, 3000), 0.02),
    ("SIP - Nippon Small Cap", "Investment, Miscellaneous", (2000, 2000), 0.02),
    ("FD - HDFC 12 month", "Investment, Miscellaneous", (10000, 25000), 0.02),
    ("Crypto - BTC accumulation", "Investment, Miscellaneous", (1500, 4000), 0.02),
    ("NPS contribution", "Investment, Miscellaneous", (2500, 5000), 0.02),
    ("Wishlist: Bike down-payment jar", "Investment, Miscellaneous", (1000, 3000), 0.30),
]

PEOPLE = ["Arjun", "Priya", "Rahul", "Sneha", "Vikram"]


def _base_row(day: date, vendor: str, type_cat: str, amount: float) -> dict[str, Any]:
    return {
        "id": str(ulid.new()),
        "date": day.isoformat(),
        "created_at": datetime.now(tz=UTC).isoformat(),
        "expense_name": vendor,
        "type_category": type_cat,
        "amount": amount,
        "source": "demo",
        "raw_transcript": None,
        "notes": None,
        "import_batch_id": None,
        "paid_for_someone": False,
        "paid_by_someone": False,
        "person_name": None,
        "paid_for_method": None,
        "adjustment_type": None,
    }


def _random_expense_row(
    day: date, rng: random.Random, cash: float, online: float
) -> tuple[dict[str, Any], float, float]:
    vendor, type_cat, (lo, hi), cash_prob = rng.choice(VENDORS)
    amount = round(rng.uniform(lo, hi), 2)

    # Category selection: 90% normal, 4% paid_for, 3% paid_by, 3% adjusted
    # (adjusted handled separately; this function only emits expense rows)
    roll = rng.random()
    row = _base_row(day, vendor, type_cat, amount)

    if roll < 0.04:
        # paid_for
        row["payment_method"] = "paid_for"
        row["paid_for_method"] = rng.choice(["cash", "online"])
        row["person_name"] = rng.choice(PEOPLE)
        if row["paid_for_method"] == "cash":
            cash = max(0.0, cash - amount)
        else:
            online = max(0.0, online - amount)
    elif roll < 0.07:
        # paid_by — no balance change
        row["payment_method"] = "paid_by"
        row["person_name"] = rng.choice(PEOPLE)
    else:
        # normal: paid_cash or paid
        if rng.random() < cash_prob:
            row["payment_method"] = "paid_cash"
            cash = max(0.0, cash - amount)
        else:
            row["payment_method"] = "paid"
            online = max(0.0, online - amount)

    row["cash_balance_after"] = cash
    row["online_balance_after"] = online
    return row, cash, online


def seed(days: int = 60, seed_value: int = 42, force: bool = False) -> int:
    bootstrap()
    from app.config import get_settings
    marker = get_settings().resolved_data_dir() / ".demo_purged"
    if marker.exists() and not force:
        print(
            "Demo data was previously purged by the user. "
            "Pass --force (or delete data/.demo_purged) to re-seed."
        )
        return 0

    ledger = get_ledger()
    balances = get_balance_service()

    rng = random.Random(seed_value)

    if balances.current() is None:
        balances.seed(cash=3500.0, online=85000.0, reason="demo")
    current = balances.current() or {"cash_balance": 3500.0, "online_balance": 85000.0}
    cash = current["cash_balance"]
    online = current["online_balance"]

    today = date.today()
    count = 0
    for offset in range(days, -1, -1):
        day = today - timedelta(days=offset)
        n = rng.randint(1, 5 if day.weekday() >= 5 else 4)
        for _ in range(n):
            row, cash, online = _random_expense_row(day, rng, cash, online)
            ledger.append("expenses", row)
            count += 1

        # 3% of days get an 'adjusted' balance transfer (no expense row)
        if rng.random() < 0.03:
            direction = rng.choice(["cash_to_online", "online_to_cash"])
            adj_amount = round(rng.uniform(200, 3000), 2)
            new_balances = balances.adjust(adj_amount, direction)
            cash = new_balances["cash_balance"]
            online = new_balances["online_balance"]

    # Final balance snapshot so /api/balances/current reflects totals
    ledger.append(
        "balances",
        {
            "asof": datetime.now(tz=UTC).isoformat(),
            "cash_balance": cash,
            "online_balance": online,
            "reason": "demo",
        },
    )
    return count


def main() -> None:
    import sys
    force = "--force" in sys.argv
    n = seed(force=force)
    if n:
        print(f"Seeded {n} expense rows across ~60 days.")


if __name__ == "__main__":
    main()
