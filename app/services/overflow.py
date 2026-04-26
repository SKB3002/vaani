"""Budget Overflow Engine — pure deterministic function.

Implements §7.2 of docs/PLAN-fineye-finance-ai.md. No I/O; callers load CSVs
and pass dataframes + dicts. See `budget_runner.py` for the orchestrator.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.models.budget import BudgetRule, CapsConfig, OverflowResult, OverflowRow


def _is_full_tc(rule_category: str) -> bool:
    """A rule string is a full `type_category` when it uses the canonical
    ", " separator between type and category (e.g. "Need, Travel")."""
    return ", " in rule_category


def _category_matches(rule_category: str, row: dict[str, Any]) -> bool:
    """Rule category matching:

    - If `rule_category` uses the ", " separator, treat as full `type_category` match.
    - Else if any expense's `type_category` endswith ", <rule_category>", match by suffix.
    - Otherwise, match against the `custom_tag` column.
    """
    tc = row.get("type_category") or ""
    if _is_full_tc(rule_category):
        return str(tc) == rule_category
    # suffix match on category half
    if isinstance(tc, str) and tc.endswith(f", {rule_category}"):
        return True
    # custom_tag fallback
    ct = row.get("custom_tag")
    if ct is None or (isinstance(ct, float) and pd.isna(ct)):
        return False
    return str(ct) == rule_category


def _actual_for_rule(rule: BudgetRule, expenses: pd.DataFrame) -> float:
    if expenses.empty:
        return 0.0
    cat = rule.category
    if _is_full_tc(cat):
        mask = expenses["type_category"].astype("string") == cat
    else:
        tc = expenses["type_category"].astype("string").fillna("")
        suffix_mask = tc.str.endswith(f", {cat}")
        if "custom_tag" in expenses.columns:
            tag_mask = expenses["custom_tag"].astype("string").fillna("") == cat
            mask = suffix_mask | tag_mask
        else:
            mask = suffix_mask
    total = float(expenses.loc[mask, "amount"].fillna(0).sum())
    return total


def compute_month(
    month: str,
    rules: list[BudgetRule],
    expenses: pd.DataFrame,
    prior_carry: dict[str, float],
    caps: CapsConfig,
    med_in: float,
    emerg_in: float,
) -> OverflowResult:
    """Pure function: compute one month of Table C.

    See §7.2 for the algorithm. No I/O, no side effects.
    """
    rows: list[OverflowRow] = []
    next_carry: dict[str, float] = {}
    warnings: list[str] = []

    med_balance = float(med_in)
    emerg_balance = float(emerg_in)

    sorted_rules = sorted(rules, key=lambda r: (r.priority, r.category))

    for rule in sorted_rules:
        cat = rule.category
        carried_in = float(prior_carry.get(cat, 0.0))
        budget_effective = float(rule.monthly_budget) + carried_in
        actual = _actual_for_rule(rule, expenses)
        remaining = budget_effective - actual

        notes_parts: list[str] = []
        if carried_in > 0:
            notes_parts.append(f"carried_in={carried_in:.2f}")

        if remaining <= 0:
            carry_next = 0.0
            overflow = 0.0
            to_medical = 0.0
            to_emergency = 0.0
            if remaining < 0:
                notes_parts.append(f"over_budget={-remaining:.2f}")
        else:
            carry_next = min(remaining, float(rule.carry_cap))
            overflow = remaining - carry_next

            # Cascade order: self carry → Emergency → Medical → lost.
            to_medical = 0.0
            to_emergency = 0.0
            if overflow > 0:
                emerg_room = float(caps.emergency_monthly_cap) - emerg_balance
                to_emergency = min(overflow, max(emerg_room, 0.0))
                emerg_balance += to_emergency
                residual = overflow - to_emergency

                if residual > 0:
                    med_room = float(caps.medical_upper_cap) - med_balance
                    to_medical = min(residual, max(med_room, 0.0))
                    med_balance += to_medical
                    lost = residual - to_medical
                    if lost > 0:
                        notes_parts.append(f"overflow_lost={lost:.2f}")
                        warnings.append(
                            f"{month}:{cat} overflow_lost={lost:.2f}"
                        )

        rows.append(
            OverflowRow(
                month=month,
                category=cat,
                budget=float(rule.monthly_budget),
                actual=round(actual, 2),
                remaining=round(remaining, 2),
                carry_buffer=round(carry_next, 2),
                overflow=round(overflow, 2),
                to_medical=round(to_medical, 2),
                to_emergency=round(to_emergency, 2),
                med_balance=round(med_balance, 2),
                emerg_balance=round(emerg_balance, 2),
                notes="; ".join(notes_parts) if notes_parts else None,
            )
        )
        next_carry[cat] = round(carry_next, 2)

    return OverflowResult(
        rows=rows,
        next_carry=next_carry,
        med_balance_out=round(med_balance, 2),
        emerg_balance_out=round(emerg_balance, 2),
        warnings=warnings,
    )
