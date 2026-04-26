"""Goals derived-field helpers — pct_complete, months_left, status."""
from __future__ import annotations

import math
from typing import Any


def derive_pct(target: float, current: float) -> float:
    if target <= 0:
        return 0.0
    pct = (current / target) * 100.0
    return round(pct, 2)


def derive_months_left(target: float, current: float, monthly: float) -> int | None:
    remaining = target - current
    if remaining <= 0:
        return 0
    if monthly <= 0:
        return None  # infinite — represented as None (→ null in JSON)
    return int(math.ceil(remaining / monthly))


def derive_status(pct: float) -> str:
    if pct >= 100.0:
        return "achieved"
    if pct >= 80.0:
        return "nearing_goal"
    if pct >= 10.0:
        return "in_progress"
    return "just_started"


def enrich_goal_a(row: dict[str, Any]) -> dict[str, Any]:
    target = float(row.get("target_amount") or 0)
    current = float(row.get("current_amount") or 0)
    monthly = float(row.get("monthly_contribution") or 0)
    pct = derive_pct(target, current)
    row["pct_complete"] = pct
    row["months_left"] = derive_months_left(target, current, monthly)
    row["status"] = derive_status(pct)
    return row


def enrich_goal_b(row: dict[str, Any]) -> dict[str, Any]:
    manual = float(row.get("manual_saved") or 0)
    auto = float(row.get("auto_added") or 0)
    total = round(manual + auto, 2)
    target = float(row.get("target_amount") or 0)
    monthly = float(row.get("monthly_contribution") or 0)
    pct = derive_pct(target, total)
    row["total_saved"] = total
    row["pct_complete"] = pct
    row["months_left"] = derive_months_left(target, total, monthly)
    row["status"] = derive_status(pct)
    return row
