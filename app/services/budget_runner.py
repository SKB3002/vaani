"""Budget runner — running-state engine.

Table C is one row per category, persistent across months. The pool
(`current_budget`) lives in `budget_state`; medical/emergency pots live in
`meta.json` under `pots`. The engine (`overflow.compute_running_state`) is
pure; all I/O lives here.
"""
from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pandas as pd

from app.models.budget import (
    BudgetRule,
    CapsConfig,
    OverflowRow,
    RunningCategoryState,
)
from app.services.ledger import LedgerWriter
from app.services.overflow import compute_running_state
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

    def _load_pots(self) -> tuple[float, float]:
        """Return (med_balance, emerg_balance) from meta.json."""
        meta_path = self.data_dir / "meta.json"
        if not meta_path.exists():
            return 0.0, 0.0
        data: dict[str, Any] = json.loads(meta_path.read_text(encoding="utf-8"))
        pots = data.get("pots") or {}
        return float(pots.get("med_balance", 0.0)), float(pots.get("emerg_balance", 0.0))

    def _save_pots(self, med: float, emerg: float) -> None:
        from app.config import get_settings
        if get_settings().STORAGE_BACKEND == "supabase":
            return  # filesystem is read-only on Vercel; pots not persisted
        meta_path = self.data_dir / "meta.json"
        data: dict[str, Any] = {}
        if meta_path.exists():
            data = json.loads(meta_path.read_text(encoding="utf-8"))
        data["pots"] = {
            "med_balance": round(float(med), 2),
            "emerg_balance": round(float(emerg), 2),
        }
        meta_path.write_text(json.dumps(data, indent=2), encoding="utf-8")

    def _load_state(self) -> dict[str, RunningCategoryState]:
        df = self.ledger.read("budget_state")
        if df.empty:
            return {}
        out: dict[str, RunningCategoryState] = {}
        for _, row in df.iterrows():
            cat = str(row["category"])
            cb = row["current_budget"]
            lr = row["last_rolled_month"]
            ua = row["updated_at"]
            out[cat] = RunningCategoryState(
                category=cat,
                current_budget=float(cb) if pd.notna(cb) else 0.0,
                last_rolled_month=str(lr) if pd.notna(lr) else "",
                updated_at=str(ua) if pd.notna(ua) else "",
            )
        return out

    def _load_expenses(self) -> pd.DataFrame:
        return self.ledger.read("expenses")

    # ---------- month utilities ----------
    def _current_month(self) -> str:
        tz = ZoneInfo(self.timezone)
        now_local = datetime.now(tz).date()
        return f"{now_local.year:04d}-{now_local.month:02d}"

    def _now_iso(self) -> str:
        tz = ZoneInfo(self.timezone)
        return datetime.now(tz).isoformat()

    # ---------- main entry ----------
    def recompute_all(self) -> RunSummary:
        with _RUN_LOCK:
            return self._recompute_locked()

    def _recompute_locked(self) -> RunSummary:
        rules = self._load_rules()
        caps = self._load_caps()
        expenses = self._load_expenses()
        prior_state = self._load_state()
        med_in, emerg_in = self._load_pots()

        current_month = self._current_month()
        result = compute_running_state(
            current_month=current_month,
            rules=rules,
            expenses=expenses,
            prior_state=prior_state,
            caps=caps,
            med_in=med_in,
            emerg_in=emerg_in,
            now_iso=self._now_iso(),
        )

        self._write_table_c(result.rows)
        self._write_state(result.new_state)
        self._save_pots(result.med_balance_out, result.emerg_balance_out)

        return RunSummary(
            months_computed=1,
            warnings=result.warnings,
            last_month_snapshot=[r.model_dump() for r in result.rows],
        )

    # ---------- writers ----------
    def _write_table_c(self, rows: list[OverflowRow]) -> None:
        """Replace-all write of budget_table_c — via direct CSV/supabase."""
        from app.config import get_settings
        supabase_mode = get_settings().STORAGE_BACKEND == "supabase"

        schema = SCHEMAS["budget_table_c"]
        records = [r.model_dump() for r in rows] if rows else []

        if supabase_mode:
            from app.config import get_settings as _gs
            from app.storage.supabase_store import _delete_where, _upsert
            uid = _gs().OWNER_ID
            _delete_where("budget_table_c", "user_id", uid)
            for rec in records:
                _upsert("budget_table_c", rec)
        else:
            df = (
                pd.DataFrame(records, columns=schema["columns"])
                if records
                else pd.DataFrame({col: [] for col in schema["columns"]})
            )
            path = Path(table_path(str(self.data_dir), "budget_table_c"))
            with file_lock(path):
                atomic_write_csv(df, path)

    def _write_state(self, states: list[RunningCategoryState]) -> None:
        """Replace-all write of budget_state."""
        from app.config import get_settings
        supabase_mode = get_settings().STORAGE_BACKEND == "supabase"

        schema = SCHEMAS["budget_state"]
        records = [s.model_dump() for s in states] if states else []

        if supabase_mode:
            from app.config import get_settings as _gs
            from app.storage.supabase_store import _delete_where, _upsert
            uid = _gs().OWNER_ID
            _delete_where("budget_state", "user_id", uid)
            for rec in records:
                _upsert("budget_state", rec)
        else:
            df = (
                pd.DataFrame(records, columns=schema["columns"])
                if records
                else pd.DataFrame({col: [] for col in schema["columns"]})
            )
            path = Path(table_path(str(self.data_dir), "budget_state"))
            with file_lock(path):
                atomic_write_csv(df, path)

    # ---------- reads ----------
    def read_table_c(self, month: str | None = None) -> list[dict[str, Any]]:
        """Return Table C rows. `month` is accepted for API compat but ignored —
        Table C is now per-category running state, not month-stamped history."""
        from app.config import get_settings
        if get_settings().STORAGE_BACKEND == "supabase":
            from app.storage.supabase_store import read_table
            df = read_table("budget_table_c")
        else:
            path = Path(table_path(str(self.data_dir), "budget_table_c"))
            df = read_csv_typed(path, SCHEMAS["budget_table_c"])
        if df.empty:
            return []
        # If multiple months somehow exist (legacy data), keep latest
        if month is None:
            months = sorted(df["month"].dropna().astype("string").unique().tolist())
            if not months:
                return []
            month = months[-1]
        rows = df[df["month"].astype("string") == month]
        safe = rows.astype(object).where(rows.notna(), None)
        return safe.to_dict(orient="records")  # type: ignore[no-any-return]

    # ---------- adjustments (button-driven) ----------
    def apply_adjustment(self, category: str, amount: float, kind: str, note: str | None = None) -> RunningCategoryState:
        """Apply an Add/Set adjustment to a category's current_budget pool.

        Logs to `budget_adjustments` and updates `budget_state` directly.
        Caller should call `recompute_all()` afterwards to refresh Table C.
        """
        if kind not in ("add", "set"):
            raise ValueError(f"unknown adjustment kind: {kind}")
        if amount < 0:
            raise ValueError("amount must be non-negative")

        # Audit log
        import ulid
        adj_id = str(ulid.new())
        self.ledger.append("budget_adjustments", {
            "id": adj_id,
            "timestamp": self._now_iso(),
            "category": category,
            "amount": float(amount),
            "kind": kind,
            "note": note,
        })

        # Update state
        prior = self._load_state()
        existing = prior.get(category)
        current_month = self._current_month()
        if existing is None:
            new_pool = float(amount) if kind in ("add", "set") else 0.0
            new_state = RunningCategoryState(
                category=category,
                current_budget=round(new_pool, 2),
                last_rolled_month=current_month,
                updated_at=self._now_iso(),
            )
        else:
            new_pool = (
                float(existing.current_budget) + float(amount)
                if kind == "add"
                else float(amount)
            )
            new_state = RunningCategoryState(
                category=category,
                current_budget=round(new_pool, 2),
                last_rolled_month=existing.last_rolled_month or current_month,
                updated_at=self._now_iso(),
            )

        # Upsert into budget_state
        df = self.ledger.read("budget_state")
        exists = not df.empty and (df["category"].astype("string") == category).any()
        row = new_state.model_dump()
        if exists:
            self.ledger.update("budget_state", category, row)
        else:
            self.ledger.append("budget_state", row)
        return new_state
