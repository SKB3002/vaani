"""Budget Overflow Engine — pure deterministic function.

Implements §7.2 of docs/PLAN-fineye-finance-ai.md. No I/O; callers load CSVs
and pass dataframes + dicts. See `budget_runner.py` for the orchestrator.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from app.models.budget import (
    BudgetRule,
    CapsConfig,
    OverflowResult,
    OverflowRow,
    RunningCategoryState,
    RunningStateResult,
)


def _is_full_tc(rule_category: str) -> bool:
    """A rule string is a full `type_category` when it uses the canonical
    ", " separator between type and category (e.g. "Need, Travel")."""
    return ", " in rule_category


def _category_matches(rule_category: str, row: dict[str, Any]) -> bool:
    """Rule category matching (case-insensitive):

    - If `rule_category` uses the ", " separator, treat as full `type_category` match.
    - Else if any expense's `type_category` endswith ", <rule_category>", match by suffix.
    - Otherwise, match against the `custom_tag` column.
    """
    rc = rule_category.lower()
    tc = str(row.get("type_category") or "").lower()
    if _is_full_tc(rule_category):
        return tc == rc
    # suffix match on category half
    if tc.endswith(f", {rc}"):
        return True
    # custom_tag fallback
    ct = row.get("custom_tag")
    if ct is None or (isinstance(ct, float) and pd.isna(ct)):
        return False
    return str(ct).lower() == rc


def _actual_for_rule(rule: BudgetRule, expenses: pd.DataFrame) -> float:
    """Sum amounts of expenses matching this rule. Case-insensitive."""
    if expenses.empty:
        return 0.0
    cat = rule.category.lower()
    if _is_full_tc(rule.category):
        tc_lower = expenses["type_category"].astype("string").fillna("").str.lower()
        mask = tc_lower == cat
    else:
        tc_lower = expenses["type_category"].astype("string").fillna("").str.lower()
        suffix_mask = tc_lower.str.endswith(f", {cat}")
        if "custom_tag" in expenses.columns:
            tag_lower = expenses["custom_tag"].astype("string").fillna("").str.lower()
            tag_mask = tag_lower == cat
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


# ----------------------------------------------------------------------------
# Running-state engine — one row per category, persists across months.
# ----------------------------------------------------------------------------


def _months_between(start: str, end: str) -> list[str]:
    """List of months we're "leaving" when transitioning from `start` to `end`.

    Returns months in [start, end) — i.e. start inclusive, end exclusive. Each
    iteration represents one rollover (cascade leftover → add monthly_budget
    for the *next* month). Empty if start >= end or start is empty.
    """
    if not start or start >= end:
        return []
    sy, sm = int(start[:4]), int(start[5:7])
    ey, em = int(end[:4]), int(end[5:7])
    out: list[str] = []
    y, m = sy, sm
    while (y, m) < (ey, em):
        out.append(f"{y:04d}-{m:02d}")
        m += 1
        if m > 12:
            m = 1
            y += 1
    return out


def _filter_month(expenses: pd.DataFrame, month: str) -> pd.DataFrame:
    if expenses.empty:
        return expenses
    mask = expenses["date"].astype("string").fillna("").str.startswith(month)
    return expenses.loc[mask]


def _cascade_overflow(
    overflow: float,
    caps: CapsConfig,
    med_balance: float,
    emerg_balance: float,
) -> tuple[float, float, float, float, float]:
    """Cascade overflow → Emergency → Medical → lost.

    Returns (to_emergency, to_medical, lost, new_med_balance, new_emerg_balance).
    """
    if overflow <= 0:
        return 0.0, 0.0, 0.0, med_balance, emerg_balance
    emerg_room = float(caps.emergency_monthly_cap) - emerg_balance
    to_emergency = min(overflow, max(emerg_room, 0.0))
    emerg_balance += to_emergency
    residual = overflow - to_emergency

    to_medical = 0.0
    lost = 0.0
    if residual > 0:
        med_room = float(caps.medical_upper_cap) - med_balance
        to_medical = min(residual, max(med_room, 0.0))
        med_balance += to_medical
        lost = residual - to_medical

    return to_emergency, to_medical, lost, med_balance, emerg_balance


def compute_running_state(
    current_month: str,
    rules: list[BudgetRule],
    expenses: pd.DataFrame,
    prior_state: dict[str, RunningCategoryState],
    caps: CapsConfig,
    med_in: float,
    emerg_in: float,
    now_iso: str,
) -> RunningStateResult:
    """Pure function: compute current per-category running state + Table C rows.

    Algorithm per rule:
      1. Look up prior state. If `last_rolled_month` < `current_month`, perform
         month rollover for each gap month: cascade leftover (carry_cap →
         Emergency → Medical), then add `monthly_budget` for the next month.
      2. Compute `actual` = sum of current-month expenses matching this rule.
      3. `remaining = current_budget − actual`.
      4. Emit a Table-C row showing the live state.

    `current_budget` is the gross pool before this month's spending — manual
    adjustments (Add/Set buttons) modify it directly outside this function.
    """
    rows: list[OverflowRow] = []
    new_states: list[RunningCategoryState] = []
    warnings: list[str] = []
    rolled: list[str] = []

    med_balance = float(med_in)
    emerg_balance = float(emerg_in)

    sorted_rules = sorted(rules, key=lambda r: (r.priority, r.category))

    for rule in sorted_rules:
        cat = rule.category
        state = prior_state.get(cat) or RunningCategoryState(category=cat)
        current_budget = float(state.current_budget)
        last_rolled = state.last_rolled_month or ""

        notes_parts: list[str] = []
        # Aggregate cascade outcomes so the displayed row reflects the most
        # recent rollover (last gap month). This is what the user sees as
        # "this month's overflow / →emergency / →medical".
        last_carry = 0.0
        last_overflow = 0.0
        last_to_emerg = 0.0
        last_to_med = 0.0

        # ---------------- rollover (one-or-more months) ----------------
        # Roll forward each missing month: subtract that month's actual,
        # cascade leftover, then add monthly_budget for the next month.
        gap_months = _months_between(last_rolled, current_month)
        if last_rolled == "" and len(rules) and current_month:
            # Never rolled before → seed by adding monthly_budget for current month
            # (no cascade happens because there's no prior pool).
            current_budget += float(rule.monthly_budget)
            rolled.append(cat)

        for gap_month in gap_months:
            # Subtract that month's spending
            month_exp = _filter_month(expenses, gap_month)
            spent_in_gap = _actual_for_rule(rule, month_exp)
            leftover = current_budget - spent_in_gap
            if leftover < 0:
                # Over-spent: pool goes to 0, no cascade
                current_budget = 0.0
                last_carry = 0.0
                last_overflow = 0.0
                last_to_emerg = 0.0
                last_to_med = 0.0
            else:
                carry_kept = min(leftover, float(rule.carry_cap))
                overflow = leftover - carry_kept
                to_e, to_m, lost, med_balance, emerg_balance = _cascade_overflow(
                    overflow, caps, med_balance, emerg_balance
                )
                if lost > 0:
                    warnings.append(f"{gap_month}:{cat} overflow_lost={lost:.2f}")
                current_budget = carry_kept
                last_carry = carry_kept
                last_overflow = overflow
                last_to_emerg = to_e
                last_to_med = to_m
            # Top up for the next month
            current_budget += float(rule.monthly_budget)
            rolled.append(cat)

        new_last_rolled = current_month

        # ---------------- this month's display ----------------
        actual = _actual_for_rule(rule, _filter_month(expenses, current_month))
        remaining = current_budget - actual

        if remaining < 0:
            notes_parts.append(f"over_budget={-remaining:.2f}")

        rows.append(
            OverflowRow(
                month=current_month,
                category=cat,
                budget=round(current_budget, 2),
                actual=round(actual, 2),
                remaining=round(remaining, 2),
                carry_buffer=round(last_carry, 2),
                overflow=round(last_overflow, 2),
                to_medical=round(last_to_med, 2),
                to_emergency=round(last_to_emerg, 2),
                med_balance=round(med_balance, 2),
                emerg_balance=round(emerg_balance, 2),
                notes="; ".join(notes_parts) if notes_parts else None,
            )
        )
        new_states.append(
            RunningCategoryState(
                category=cat,
                current_budget=round(current_budget, 2),
                last_rolled_month=new_last_rolled,
                updated_at=now_iso,
            )
        )

    return RunningStateResult(
        rows=rows,
        new_state=new_states,
        med_balance_out=round(med_balance, 2),
        emerg_balance_out=round(emerg_balance, 2),
        warnings=warnings,
        rolled_categories=sorted(set(rolled)),
    )
