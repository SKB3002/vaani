"""Monthly stats aggregator for AI Insights (briefing + chat).

Produces a deterministic JSON-serialisable bundle (`MonthlyStatsBundle`) that
the narrator and planner consume. The bundle is the **only** structured input
to the LLM — every number the user sees comes from this object, not from the
LLM. Hashing the bundle (excluding `generated_at`) is the cache key that
makes warm-cache hits possible.

Design notes
------------
- Reads tables exclusively through `LedgerWriter.read(...)`. No direct CSV or
  Supabase access — both backends are transparent here.
- All month math is in Asia/Kolkata local time via `app.services.tz`. A
  `month` argument of ``"2026-04"`` always means the IST calendar month.
- Trailing windows are the N **previous** calendar months (NOT including the
  queried month). Empty months are still valid and yield zero-filled stats.
- `type` is the first segment of `type_category` before ``", "`` (matches
  `app.services.charts.derived.add_derived_columns`).
- "Real spend" excludes:
    * Adjustment rows (``adjustment_type`` is non-empty) — they are pure
      cash↔online transfers, not money leaving the user's net worth.
    * `paid_for_someone == True` — money fronted on someone else's behalf.
  Income side = `paid_by_someone == True` rows (someone owes/paid the user).
- Goals→expenses linkage: the EXPENSES schema has no `goal_id` column, so
  there is no deterministic way to attribute an expense to a goal. As a
  fallback, `monthly_contribution_avg` defaults to ``0.0`` and
  `projected_completion_date` is ``None``. When/if a `goal_id` column is
  added to EXPENSES, fill in the heuristic in `_compute_goal_progress`.
- pandas can produce `NaN` / `Inf`; Pydantic v2 rejects these by default.
  Every numeric value crossing into a Pydantic model is funneled through
  `_safe_float` (NaN/Inf → 0.0) or `_safe_pct` (NaN/Inf/zero-denominator →
  None).
"""
from __future__ import annotations

import hashlib
import json
import math
from datetime import date, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel

from app.config import get_settings
from app.services.ledger import LedgerWriter
from app.services.tz import now_utc, user_tz

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class CategoryStats(BaseModel):
    category: str
    type: str
    total: float
    txn_count: int


class PeriodStats(BaseModel):
    """Stats for one period (a calendar month or a trailing window)."""

    label: str
    start_date: str
    end_date: str  # exclusive
    net_spend: float
    txn_count: int
    by_category: list[CategoryStats]
    by_type: dict[str, float]
    by_payment_method: dict[str, float]
    top_merchants: list[dict[str, Any]]


class TrendDelta(BaseModel):
    """Per-category delta between current period and a comparison period."""

    category: str
    current: float
    previous: float
    delta_abs: float
    delta_pct: float | None


class GoalProgress(BaseModel):
    goal_id: str
    goal_name: str
    target_amount: float
    current_amount: float
    pct_complete: float
    monthly_contribution_avg: float
    projected_completion_date: str | None


class BudgetUtilisation(BaseModel):
    category: str
    budgeted: float
    actual: float
    remaining: float
    utilisation_pct: float
    overflow_to: str | None
    overflow_amount: float


class MonthlyStatsBundle(BaseModel):
    """The full deterministic stats bundle for a given month.

    Serialised → hashed → cached → fed into the narration prompt. The LLM
    only writes prose around these numbers; it never produces digits itself.
    """

    month: str
    generated_at: str
    owner_id: str
    currency: str

    current_month: PeriodStats
    previous_month: PeriodStats
    trailing_3m: PeriodStats
    trailing_12m: PeriodStats

    category_deltas_vs_prev: list[TrendDelta]
    category_deltas_vs_3m_avg: list[TrendDelta]

    budget_utilisation: list[BudgetUtilisation]
    goals: list[GoalProgress]
    net_cashflow: float
    top_n_largest_txns: list[dict[str, Any]]

    investment_total_current: float
    investment_total_prev_month: float
    investment_delta_pct: float | None


# ---------------------------------------------------------------------------
# Numeric / date helpers
# ---------------------------------------------------------------------------


def _safe_float(value: Any) -> float:
    """Coerce any pandas/python numeric to a finite float; NaN/Inf → 0.0."""
    if value is None:
        return 0.0
    try:
        f = float(value)
    except (TypeError, ValueError):
        return 0.0
    if math.isnan(f) or math.isinf(f):
        return 0.0
    return f


def _safe_pct(numerator: float, denominator: float) -> float | None:
    """Compute numerator/denominator as a percentage; return None on zero/NaN."""
    if denominator == 0 or math.isnan(denominator) or math.isinf(denominator):
        return None
    pct = (numerator / denominator) * 100.0
    if math.isnan(pct) or math.isinf(pct):
        return None
    return pct


def _parse_month(month: str) -> tuple[date, date]:
    """Return (start, end_exclusive) for a ``YYYY-MM`` calendar month."""
    try:
        year_s, mon_s = month.split("-", 1)
        year = int(year_s)
        mon = int(mon_s)
    except (ValueError, AttributeError) as exc:
        raise ValueError(f"invalid month format, expected YYYY-MM, got {month!r}") from exc
    if not (1 <= mon <= 12):
        raise ValueError(f"invalid month value: {month!r}")
    start = date(year, mon, 1)
    end = date(year + 1, 1, 1) if mon == 12 else date(year, mon + 1, 1)
    return start, end


def _shift_month(month: str, delta: int) -> str:
    """Shift a ``YYYY-MM`` string by ``delta`` months."""
    start, _ = _parse_month(month)
    total = start.year * 12 + (start.month - 1) + delta
    new_year, new_mon0 = divmod(total, 12)
    return f"{new_year:04d}-{new_mon0 + 1:02d}"


def _format_month(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


# ---------------------------------------------------------------------------
# Expense slicing
# ---------------------------------------------------------------------------


def _split_type_category(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    """Return (type, category) Series from a `type_category` Series.

    Mirrors `charts.derived.add_derived_columns` — splits on the first ``", "``.
    Empty / NA inputs map to empty strings.
    """
    s = series.fillna("").astype("string")
    parts = s.str.split(", ", n=1, expand=True)
    type_col = parts[0] if parts.shape[1] >= 1 else pd.Series([""] * len(s), index=s.index)
    if parts.shape[1] >= 2:
        cat_col = parts[1].fillna("")
    else:
        cat_col = pd.Series([""] * len(s), index=s.index, dtype="string")
    return type_col.fillna(""), cat_col.fillna("")


def _is_truthy_bool(series: pd.Series) -> pd.Series:
    """Return a boolean mask treating NA / non-True values as False."""
    if series.dtype == "boolean":
        return series.fillna(False).astype(bool)
    return series.astype("string").str.lower().isin({"true", "1", "yes"}).fillna(False)


def _filter_real_spend(expenses: pd.DataFrame) -> pd.DataFrame:
    """Drop rows that are not real outgoing spend.

    Excluded:
      - Adjustment rows (``adjustment_type`` non-empty)
      - ``paid_for_someone`` rows (fronted for someone else)
      - ``paid_by_someone`` rows (incoming, accounted for separately)
    """
    if expenses.empty:
        return expenses

    df = expenses.copy()
    if "adjustment_type" in df.columns:
        adj = df["adjustment_type"].fillna("").astype("string").str.strip()
        df = df[adj == ""]
    if "paid_for_someone" in df.columns:
        df = df[~_is_truthy_bool(df["paid_for_someone"])]
    if "paid_by_someone" in df.columns:
        df = df[~_is_truthy_bool(df["paid_by_someone"])]
    return df


def _filter_income(expenses: pd.DataFrame) -> pd.DataFrame:
    """Rows representing money owed to / paid back to the user.

    Adjustment rows are excluded (they're pure cash↔online transfers).
    """
    if expenses.empty:
        return expenses
    df = expenses.copy()
    if "adjustment_type" in df.columns:
        adj = df["adjustment_type"].fillna("").astype("string").str.strip()
        df = df[adj == ""]
    if "paid_by_someone" not in df.columns:
        return df.iloc[0:0]
    return df[_is_truthy_bool(df["paid_by_someone"])]


def _slice_by_date(
    expenses: pd.DataFrame, start: date, end: date
) -> pd.DataFrame:
    """Return rows where ``start <= date < end`` (end exclusive)."""
    if expenses.empty or "date" not in expenses.columns:
        return expenses
    parsed = pd.to_datetime(expenses["date"], errors="coerce", utc=False)
    mask = parsed.notna() & (parsed.dt.date >= start) & (parsed.dt.date < end)
    return expenses[mask]


# ---------------------------------------------------------------------------
# Period stats
# ---------------------------------------------------------------------------


def compute_period_stats(
    expenses_df: pd.DataFrame,
    *,
    label: str,
    start: date,
    end: date,
) -> PeriodStats:
    """Compute deterministic stats for one period over a pre-loaded expenses df.

    `start` is inclusive, `end` is exclusive. The caller is responsible for
    passing the full expenses table — slicing happens here.
    """
    sliced = _slice_by_date(expenses_df, start, end)
    real = _filter_real_spend(sliced)

    if real.empty:
        return PeriodStats(
            label=label,
            start_date=start.isoformat(),
            end_date=end.isoformat(),
            net_spend=0.0,
            txn_count=0,
            by_category=[],
            by_type={},
            by_payment_method={},
            top_merchants=[],
        )

    work = real.copy()
    type_col, cat_col = _split_type_category(work["type_category"])
    work["_type"] = type_col
    work["_category"] = cat_col
    work["_amount"] = pd.to_numeric(work["amount"], errors="coerce").fillna(0.0)

    net_spend = _safe_float(work["_amount"].sum())
    txn_count = int(len(work))

    # by_category — use `type_category` as the canonical category label so
    # the LLM sees the human-readable "Need, Food & Drinks" form.
    by_category: list[CategoryStats] = []
    for tc, group in work.groupby("type_category", dropna=False):
        if pd.isna(tc) or str(tc).strip() == "":
            continue
        first_type = str(group["_type"].iloc[0]) if not group.empty else ""
        by_category.append(
            CategoryStats(
                category=str(tc),
                type=first_type,
                total=_safe_float(group["_amount"].sum()),
                txn_count=int(len(group)),
            )
        )
    by_category.sort(key=lambda c: c.total, reverse=True)

    # by_type
    by_type_raw = work.groupby("_type")["_amount"].sum()
    by_type: dict[str, float] = {
        str(k): _safe_float(v) for k, v in by_type_raw.items() if str(k).strip() != ""
    }

    # by_payment_method — `payment_method` is a free-form column. Empty/NA
    # is bucketed as "unknown".
    by_payment_method: dict[str, float] = {}
    if "payment_method" in work.columns:
        pm = work["payment_method"].fillna("unknown").astype("string").replace("", "unknown")
        for k, v in work.assign(_pm=pm).groupby("_pm")["_amount"].sum().items():
            by_payment_method[str(k)] = _safe_float(v)

    # top_merchants — group by expense_name (no `vendor` column in EXPENSES).
    top_merchants: list[dict[str, Any]] = []
    if "expense_name" in work.columns:
        names = work["expense_name"].fillna("").astype("string").str.strip()
        merch = (
            work.assign(_name=names)
            .loc[lambda d: d["_name"] != ""]
            .groupby("_name")
            .agg(total=("_amount", "sum"), count=("_amount", "size"))
            .reset_index()
            .sort_values("total", ascending=False)
            .head(5)
        )
        for _, row in merch.iterrows():
            top_merchants.append(
                {
                    "name": str(row["_name"]),
                    "total": _safe_float(row["total"]),
                    "count": int(row["count"]),
                }
            )

    return PeriodStats(
        label=label,
        start_date=start.isoformat(),
        end_date=end.isoformat(),
        net_spend=net_spend,
        txn_count=txn_count,
        by_category=by_category,
        by_type=by_type,
        by_payment_method=by_payment_method,
        top_merchants=top_merchants,
    )


# ---------------------------------------------------------------------------
# Trend deltas
# ---------------------------------------------------------------------------


def _category_totals(period: PeriodStats) -> dict[str, float]:
    return {c.category: c.total for c in period.by_category}


def _build_deltas(
    current: dict[str, float],
    baseline: dict[str, float],
) -> list[TrendDelta]:
    cats = sorted(set(current) | set(baseline))
    out: list[TrendDelta] = []
    for cat in cats:
        cur = _safe_float(current.get(cat, 0.0))
        prev = _safe_float(baseline.get(cat, 0.0))
        out.append(
            TrendDelta(
                category=cat,
                current=cur,
                previous=prev,
                delta_abs=_safe_float(cur - prev),
                delta_pct=_safe_pct(cur - prev, prev),
            )
        )
    out.sort(key=lambda d: abs(d.delta_abs), reverse=True)
    return out


# ---------------------------------------------------------------------------
# Budgets, goals, investments, cashflow, largest txns
# ---------------------------------------------------------------------------


def _compute_budget_utilisation(
    budget_rules: pd.DataFrame,
    table_c: pd.DataFrame,
    period: PeriodStats,
) -> list[BudgetUtilisation]:
    """Marry `budget_rules` (planned) with `budget_table_c` (running state).

    `table_c` is the running pool — `actual`, `remaining`, `overflow`,
    `to_medical`, `to_emergency` for each category. We map those onto the
    period's spend so the narrator can talk about utilisation. If the
    running-state row is missing, we fall back to the slice from
    ``period.by_category``.
    """
    spend_by_cat: dict[str, float] = {c.category.split(", ", 1)[-1]: c.total for c in period.by_category}
    # Also key by full type_category in case rules use the qualified form
    full_spend: dict[str, float] = {c.category: c.total for c in period.by_category}

    out: list[BudgetUtilisation] = []
    if budget_rules.empty:
        return out

    table_c_lookup: dict[str, dict[str, Any]] = {}
    if not table_c.empty and "category" in table_c.columns:
        for _, row in table_c.iterrows():
            cat = str(row.get("category") or "").strip()
            if cat:
                table_c_lookup[cat] = row.to_dict()

    for _, rule in budget_rules.iterrows():
        cat = str(rule.get("category") or "").strip()
        if not cat:
            continue
        budgeted = _safe_float(rule.get("monthly_budget"))
        tc_row = table_c_lookup.get(cat)
        if tc_row is not None:
            actual = _safe_float(tc_row.get("actual"))
            remaining = _safe_float(tc_row.get("remaining"))
            to_med = _safe_float(tc_row.get("to_medical"))
            to_emerg = _safe_float(tc_row.get("to_emergency"))
        else:
            actual = _safe_float(spend_by_cat.get(cat) or full_spend.get(cat) or 0.0)
            remaining = _safe_float(budgeted - actual)
            to_med = 0.0
            to_emerg = 0.0

        utilisation_pct = _safe_pct(actual, budgeted) or 0.0
        overflow_to: str | None
        if to_med > 0 and to_med >= to_emerg:
            overflow_to = "Medical"
            overflow_amount = to_med
        elif to_emerg > 0:
            overflow_to = "Emergency"
            overflow_amount = to_emerg
        else:
            overflow_to = None
            overflow_amount = 0.0

        out.append(
            BudgetUtilisation(
                category=cat,
                budgeted=budgeted,
                actual=actual,
                remaining=remaining,
                utilisation_pct=utilisation_pct,
                overflow_to=overflow_to,
                overflow_amount=overflow_amount,
            )
        )
    return out


def _compute_goal_progress(
    goals_a: pd.DataFrame,
    expenses: pd.DataFrame,  # noqa: ARG001 — reserved for future goal-link heuristic
    month: str,  # noqa: ARG001
) -> list[GoalProgress]:
    """Compute progress for each goal in ``goals_a``.

    NOTE: The EXPENSES schema has **no** ``goal_id`` column at the time of
    writing, so there is no deterministic link between a transaction and a
    savings goal. ``monthly_contribution_avg`` therefore defaults to
    ``0.0`` and ``projected_completion_date`` to ``None``. A future revision
    that adds ``goal_id`` to expenses can populate these from a 3-month
    rolling sum.
    """
    if goals_a.empty:
        return []

    out: list[GoalProgress] = []
    for _, row in goals_a.iterrows():
        goal_id = str(row.get("goal_id") or "").strip()
        if not goal_id:
            continue
        target = _safe_float(row.get("target_amount"))
        current = _safe_float(row.get("current_amount"))
        pct = _safe_pct(current, target) or 0.0
        monthly_avg = 0.0  # see docstring fallback
        out.append(
            GoalProgress(
                goal_id=goal_id,
                goal_name=str(row.get("goal_name") or ""),
                target_amount=target,
                current_amount=current,
                pct_complete=pct,
                monthly_contribution_avg=monthly_avg,
                projected_completion_date=None,
            )
        )
    return out


def _compute_net_cashflow(
    balances: pd.DataFrame,
    expenses: pd.DataFrame,
    start: date,
    end: date,
) -> float:
    """Net cash movement over [start, end) = end_total - start_total.

    Uses the `balances` table — `cash_balance + online_balance` snapshot at
    the last asof < start, vs the last asof < end. This captures all real
    movement (spend, income, ATM transfers, manual adjusts) in one number.

    Falls back to (income - spend) from the expenses table if balances has
    no rows covering the window — e.g. brand-new account with no balance
    history yet.
    """
    if not balances.empty and "asof" in balances.columns:
        b = balances.copy()
        b["_asof"] = pd.to_datetime(b["asof"], errors="coerce", utc=True)
        b = b.dropna(subset=["_asof"]).sort_values("_asof")
        if not b.empty:
            cash = pd.to_numeric(b["cash_balance"], errors="coerce").fillna(0.0)
            online = pd.to_numeric(b["online_balance"], errors="coerce").fillna(0.0)
            total = (cash + online).reset_index(drop=True)
            asof = b["_asof"].reset_index(drop=True)

            start_ts = pd.Timestamp(start, tz="UTC")
            end_ts = pd.Timestamp(end, tz="UTC")

            before_start = asof < start_ts
            before_end = asof < end_ts
            start_total = float(total[before_start].iloc[-1]) if before_start.any() else 0.0
            end_total = float(total[before_end].iloc[-1]) if before_end.any() else start_total

            if before_end.any():
                return _safe_float(end_total - start_total)

    # Fallback: income vs spend from expenses
    sliced = _slice_by_date(expenses, start, end)
    if sliced.empty:
        return 0.0
    income_df = _filter_income(sliced)
    spend_df = _filter_real_spend(sliced)
    income = _safe_float(pd.to_numeric(income_df["amount"], errors="coerce").fillna(0.0).sum()) if not income_df.empty else 0.0
    spend = _safe_float(pd.to_numeric(spend_df["amount"], errors="coerce").fillna(0.0).sum()) if not spend_df.empty else 0.0
    return _safe_float(income - spend)


def _compute_largest_txns(expenses: pd.DataFrame, start: date, end: date, n: int = 5) -> list[dict[str, Any]]:
    sliced = _slice_by_date(expenses, start, end)
    if sliced.empty:
        return []
    work = sliced.copy()
    if "adjustment_type" in work.columns:
        adj = work["adjustment_type"].fillna("").astype("string").str.strip()
        work = work[adj == ""]
    if work.empty:
        return []
    work = work.assign(
        _abs_amount=pd.to_numeric(work["amount"], errors="coerce").abs().fillna(0.0)
    )
    top = work.sort_values("_abs_amount", ascending=False).head(n)

    out: list[dict[str, Any]] = []
    for _, row in top.iterrows():
        out.append(
            {
                "expense_name": str(row.get("expense_name") or ""),
                "amount": _safe_float(row.get("amount")),
                "date": str(row.get("date") or ""),
                "type_category": str(row.get("type_category") or ""),
            }
        )
    return out


def _compute_investments(investments: pd.DataFrame, month: str) -> tuple[float, float, float | None]:
    """Return (current_total, prev_month_total, delta_pct)."""
    if investments.empty or "month" not in investments.columns:
        return 0.0, 0.0, None
    prev_month = _shift_month(month, -1)
    months = investments["month"].fillna("").astype("string")

    def _row_total(target: str) -> float:
        rows = investments[months == target]
        if rows.empty:
            return 0.0
        if "total" in rows.columns:
            return _safe_float(pd.to_numeric(rows["total"], errors="coerce").fillna(0.0).iloc[-1])
        # fallback: sum the numeric component columns
        numeric_cols = [c for c in rows.columns if c not in {"month", "import_batch_id"}]
        return _safe_float(pd.to_numeric(rows[numeric_cols].iloc[-1], errors="coerce").fillna(0.0).sum())

    current_total = _row_total(month)
    prev_total = _row_total(prev_month)
    delta_pct = _safe_pct(current_total - prev_total, prev_total)
    return current_total, prev_total, delta_pct


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def build_monthly_bundle(
    month: str,
    ledger: LedgerWriter,
    *,
    owner_id: str | None = None,
    currency: str = "INR",
) -> MonthlyStatsBundle:
    """Build the deterministic stats bundle for a calendar month.

    `month` is a ``YYYY-MM`` string interpreted in IST. All tables are read
    once and reused across the period computations.
    """
    cur_start, cur_end = _parse_month(month)
    prev_month = _shift_month(month, -1)
    prev_start, prev_end = _parse_month(prev_month)

    # Trailing windows: 3 / 12 calendar months immediately BEFORE `month`.
    t3_start, _ = _parse_month(_shift_month(month, -3))
    t3_end = cur_start
    t12_start, _ = _parse_month(_shift_month(month, -12))
    t12_end = cur_start

    # Single read per table.
    expenses = ledger.read("expenses")
    budget_rules = ledger.read("budget_rules")
    table_c = ledger.read("budget_table_c")
    goals_a = ledger.read("goals_a")
    investments = ledger.read("investments")
    balances = ledger.read("balances")

    current_period = compute_period_stats(
        expenses, label=month, start=cur_start, end=cur_end
    )
    previous_period = compute_period_stats(
        expenses, label=prev_month, start=prev_start, end=prev_end
    )
    trailing_3 = compute_period_stats(
        expenses, label="trailing_3m", start=t3_start, end=t3_end
    )
    trailing_12 = compute_period_stats(
        expenses, label="trailing_12m", start=t12_start, end=t12_end
    )

    cur_totals = _category_totals(current_period)
    prev_totals = _category_totals(previous_period)
    deltas_vs_prev = _build_deltas(cur_totals, prev_totals)

    # Per-category 3-month average (over the trailing window, NOT including current).
    t3_totals = _category_totals(trailing_3)
    t3_avg = {k: v / 3.0 for k, v in t3_totals.items()}
    deltas_vs_3m = _build_deltas(cur_totals, t3_avg)

    budget_util = _compute_budget_utilisation(budget_rules, table_c, current_period)
    goals = _compute_goal_progress(goals_a, expenses, month)
    net_cashflow = _compute_net_cashflow(balances, expenses, cur_start, cur_end)
    top_txns = _compute_largest_txns(expenses, cur_start, cur_end, n=5)
    inv_cur, inv_prev, inv_delta = _compute_investments(investments, month)

    if owner_id is not None:
        resolved_owner = owner_id
    else:
        from app.context import current_user_id

        resolved_owner = current_user_id()

    return MonthlyStatsBundle(
        month=month,
        generated_at=_iso_now(),
        owner_id=resolved_owner,
        currency=currency,
        current_month=current_period,
        previous_month=previous_period,
        trailing_3m=trailing_3,
        trailing_12m=trailing_12,
        category_deltas_vs_prev=deltas_vs_prev,
        category_deltas_vs_3m_avg=deltas_vs_3m,
        budget_utilisation=budget_util,
        goals=goals,
        net_cashflow=net_cashflow,
        top_n_largest_txns=top_txns,
        investment_total_current=inv_cur,
        investment_total_prev_month=inv_prev,
        investment_delta_pct=inv_delta,
    )


def bundle_hash(bundle: MonthlyStatsBundle) -> str:
    """Deterministic sha256 of the bundle, excluding `generated_at`.

    Serialises via Pydantic's ``model_dump(mode="json")`` (so all values are
    JSON-native), then re-encodes with ``json.dumps(sort_keys=True,
    separators=(",", ":"))`` to produce a canonical byte string. Without
    ``sort_keys`` the hash is non-deterministic across Python versions.
    """
    payload = bundle.model_dump(mode="json", exclude={"generated_at"})
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# Internal — kept at the bottom because it's the only side-effect-y helper
# ---------------------------------------------------------------------------


def _iso_now() -> str:
    """ISO timestamp in IST (matches the rest of the app's tz semantics)."""
    # `now_utc()` gives a tz-aware UTC datetime; convert to user_tz for display.
    try:
        return datetime.now(tz=user_tz()).isoformat()
    except Exception:  # noqa: BLE001 — tz lookup failure must not break aggregation
        return now_utc().isoformat()


__all__ = [
    "BudgetUtilisation",
    "CategoryStats",
    "GoalProgress",
    "MonthlyStatsBundle",
    "PeriodStats",
    "TrendDelta",
    "build_monthly_bundle",
    "bundle_hash",
    "compute_period_stats",
]
