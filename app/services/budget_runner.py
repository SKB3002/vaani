"""Budget runner — orchestrates loading CSVs, iterating months, and writing Table C.

The overflow engine (`overflow.compute_month`) is pure. All I/O lives here.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.models.budget import BudgetRule, CapsConfig, OverflowRow
from app.services.ledger import LedgerWriter
from app.services.overflow import compute_month
from app.storage.csv_store import atomic_write_csv, file_lock, read_csv_typed
from app.storage.schemas import SCHEMAS, table_path


@dataclass
class RunSummary:
    months_computed: int
    warnings: list[str]
    last_month_snapshot: list[dict[str, Any]]


_RUN_LOCK = threading.Lock()


class BudgetRunner:
    def __init__(self, ledger: LedgerWriter, data_dir: Path, timezone: str = "Asia/Kolkata") -> None:
        self.ledger = ledger
        self.data_dir = Path(data_dir)
        self.timezone = timezone

    # ---------- loaders ----------
    def _load_rules(self) -> list[BudgetRule]:
        df = self.ledger.read("budget_rules")
        if df.empty:
            return []
        rules: list[BudgetRule] = []
        for _, row in df.iterrows():
            mb = row["monthly_budget"]
            cc = row["carry_cap"]
            rules.append(
                BudgetRule(
                    category=str(row["category"]),
                    monthly_budget=float(mb) if pd.notna(mb) else 0.0,
                    carry_cap=float(cc) if pd.notna(cc) else 0.0,
                    priority=int(row["priority"]) if pd.notna(row["priority"]) else 100,
                )
            )
        return rules

    def _load_caps(self) -> CapsConfig:
        meta_path = self.data_dir / "meta.json"
        if not meta_path.exists():
            return CapsConfig()
        data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        caps_raw = data.get("caps", {})
        return CapsConfig(**caps_raw)

    def _load_expenses(self) -> pd.DataFrame:
        return self.ledger.read("expenses")

    # ---------- month utilities ----------
    def _month_range(self, expenses: pd.DataFrame) -> list[str]:
        """Produce chronological list of months from earliest expense to current local month."""
        tz = ZoneInfo(self.timezone)
        from datetime import datetime as _dt

        now_local = _dt.now(tz).date()
        current_month = f"{now_local.year:04d}-{now_local.month:02d}"

        if expenses.empty:
            return [current_month]

        dates = pd.to_datetime(expenses["date"], errors="coerce").dropna()
        if dates.empty:
            return [current_month]
        start = dates.min().date()
        end = now_local
        if start > end:
            start = end

        months: list[str] = []
        y, m = start.year, start.month
        while (y, m) <= (end.year, end.month):
            months.append(f"{y:04d}-{m:02d}")
            m += 1
            if m > 12:
                m = 1
                y += 1
        return months

    @staticmethod
    def _filter_month(expenses: pd.DataFrame, month: str) -> pd.DataFrame:
        if expenses.empty:
            return expenses
        mask = expenses["date"].astype("string").fillna("").str.startswith(month)
        return expenses.loc[mask]

    # ---------- main entry ----------
    def recompute_all(self) -> RunSummary:
        with _RUN_LOCK:
            return self._recompute_locked()

    def _recompute_locked(self) -> RunSummary:
        rules = self._load_rules()
        caps = self._load_caps()
        expenses = self._load_expenses()

        months = self._month_range(expenses)
        all_rows: list[OverflowRow] = []
        all_warnings: list[str] = []

        prior_carry: dict[str, float] = {}
        med_balance = 0.0
        emerg_balance = 0.0

        for month in months:
            month_expenses = self._filter_month(expenses, month)
            result = compute_month(
                month=month,
                rules=rules,
                expenses=month_expenses,
                prior_carry=prior_carry,
                caps=caps,
                med_in=med_balance,
                emerg_in=emerg_balance,
            )
            all_rows.extend(result.rows)
            all_warnings.extend(result.warnings)
            prior_carry = result.next_carry
            med_balance = result.med_balance_out
            emerg_balance = result.emerg_balance_out

        self._write_table_c(all_rows)

        last_month = months[-1] if months else ""
        last_snapshot = [
            r.model_dump() for r in all_rows if r.month == last_month
        ]
        return RunSummary(
            months_computed=len(months),
            warnings=all_warnings,
            last_month_snapshot=last_snapshot,
        )

    def _write_table_c(self, rows: list[OverflowRow]) -> None:
        """Replace-all write of budget_table_c.csv (atomic)."""
        schema = SCHEMAS["budget_table_c"]
        path = Path(table_path(str(self.data_dir), "budget_table_c"))
        if rows:
            records = [r.model_dump() for r in rows]
            df = pd.DataFrame(records, columns=schema["columns"])
        else:
            df = pd.DataFrame({col: [] for col in schema["columns"]})
        with file_lock(path):
            atomic_write_csv(df, path)

    # ---------- reads ----------
    def read_table_c(self, month: str | None = None) -> list[dict[str, Any]]:
        path = Path(table_path(str(self.data_dir), "budget_table_c"))
        df = read_csv_typed(path, SCHEMAS["budget_table_c"])
        if df.empty:
            return []
        if month is None:
            # latest month present
            months = sorted(df["month"].dropna().astype("string").unique().tolist())
            if not months:
                return []
            month = months[-1]
        rows = df[df["month"].astype("string") == month]
        safe = rows.astype(object).where(rows.notna(), None)
        return safe.to_dict(orient="records")  # type: ignore[no-any-return]
