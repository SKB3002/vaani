"""Budget rules, caps, and Table C endpoints."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings
from app.deps import get_budget_runner, get_ledger
from app.models.budget import BudgetRuleIn, BudgetRulePatch, CapsPatch
from app.services.budget_runner import BudgetRunner, RunSummary
from app.services.ledger import LedgerWriter

router = APIRouter(prefix="/api/budgets", tags=["budgets"])


def _meta_path() -> Path:
    return get_settings().resolved_data_dir() / "meta.json"


def _load_meta() -> dict[str, Any]:
    path = _meta_path()
    if not path.exists():
        return {
            "caps": {"medical_upper_cap": 10000, "emergency_monthly_cap": 5000},
        }
    return json.loads(path.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def _save_meta(data: dict[str, Any]) -> None:
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _df_records(df: Any) -> list[dict[str, Any]]:
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]


def _run_summary_dict(s: RunSummary) -> dict[str, Any]:
    return {
        "months_computed": s.months_computed,
        "warnings": s.warnings,
        "last_month_snapshot": s.last_month_snapshot,
    }


# ---------- rules ----------


@router.get("/rules")
def list_rules(
    ledger: LedgerWriter = Depends(get_ledger),
) -> list[dict[str, Any]]:
    df = ledger.read("budget_rules")
    if df.empty:
        return []
    df = df.sort_values(["priority", "category"])
    return _df_records(df)


@router.post("/rules", status_code=201)
def upsert_rule(
    payload: BudgetRuleIn,
    ledger: LedgerWriter = Depends(get_ledger),
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    df = ledger.read("budget_rules")
    exists = not df.empty and (df["category"].astype("string") == payload.category).any()
    row = {
        "category": payload.category,
        "monthly_budget": float(payload.monthly_budget),
        "carry_cap": float(payload.carry_cap),
        "priority": int(payload.priority),
    }
    if exists:
        ledger.update("budget_rules", payload.category, row)
    else:
        ledger.append("budget_rules", row)
    runner.recompute_all()
    return row


@router.patch("/rules/{category}")
def patch_rule(
    category: str,
    patch: BudgetRulePatch,
    ledger: LedgerWriter = Depends(get_ledger),
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    updates = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(400, "no updates provided")
    result = ledger.update("budget_rules", category, updates)
    if result is None:
        raise HTTPException(404, "rule not found")
    runner.recompute_all()
    return result


@router.delete("/rules/{category}", status_code=204)
def delete_rule(
    category: str,
    ledger: LedgerWriter = Depends(get_ledger),
    runner: BudgetRunner = Depends(get_budget_runner),
) -> None:
    if not ledger.delete("budget_rules", category):
        raise HTTPException(404, "rule not found")
    runner.recompute_all()


# ---------- caps ----------


@router.get("/caps")
def get_caps() -> dict[str, Any]:
    meta = _load_meta()
    caps = meta.get("caps", {})
    return {
        "medical_upper_cap": float(caps.get("medical_upper_cap", 10000)),
        "emergency_monthly_cap": float(caps.get("emergency_monthly_cap", 5000)),
    }


@router.patch("/caps")
def patch_caps(
    patch: CapsPatch,
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    meta = _load_meta()
    caps = dict(meta.get("caps") or {})
    if patch.medical_upper_cap is not None:
        caps["medical_upper_cap"] = float(patch.medical_upper_cap)
    if patch.emergency_monthly_cap is not None:
        caps["emergency_monthly_cap"] = float(patch.emergency_monthly_cap)
    meta["caps"] = caps
    _save_meta(meta)
    runner.recompute_all()
    return {
        "medical_upper_cap": float(caps.get("medical_upper_cap", 10000)),
        "emergency_monthly_cap": float(caps.get("emergency_monthly_cap", 5000)),
    }


# ---------- Table C ----------


@router.get("/table-c")
def get_table_c(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    rows = runner.read_table_c(month)
    return {"month": month, "rows": rows}


@router.post("/recompute")
def recompute(
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    summary = runner.recompute_all()
    return _run_summary_dict(summary)
