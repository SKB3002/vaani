"""Budget rules, caps, and Table C endpoints."""
from __future__ import annotations

import errno
import json
import logging
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from app.config import get_settings
from app.deps import get_budget_runner, get_ledger
from app.models.budget import (
    BudgetAdjustIn,
    BudgetRuleIn,
    BudgetRulePatch,
    CapsPatch,
    TagCreateIn,
)
from app.services import uniques as uniques_store
from app.services.budget_runner import BudgetRunner, RunSummary
from app.services.ledger import LedgerWriter

logger = logging.getLogger(__name__)

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
    """Persist meta.json to disk.

    Previously this short-circuited whenever STORAGE_BACKEND==supabase,
    assuming any supabase deployment ran on Vercel's read-only filesystem.
    That broke caps persistence for local supabase setups. Now we always
    attempt the write and only swallow EROFS (the actual Vercel symptom).
    """
    path = _meta_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    except OSError as e:
        if getattr(e, "errno", None) == errno.EROFS:
            logger.warning("meta.json not persisted (read-only filesystem): %s", path)
            return
        raise


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
    # Persist the Need/Want/Investment type for custom-tag rules so the grouped
    # Table C view can roll them up. Built-in "Type, Category" rules carry their
    # type in the prefix, so leave it NULL for them.
    is_bare_tag = ", " not in payload.category
    row = {
        "category": payload.category,
        "monthly_budget": float(payload.monthly_budget),
        "carry_cap": float(payload.carry_cap),
        "priority": int(payload.priority),
        "type": payload.type if (payload.type and is_bare_tag) else None,
    }
    if exists:
        ledger.update("budget_rules", payload.category, row)
    else:
        ledger.append("budget_rules", row)
    uniques_store.add_tag(payload.category)
    if payload.type and is_bare_tag:
        uniques_store.set_tag_type(payload.category, payload.type)
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
    uniques_store.remove_tag(category)  # also drops the tag_types entry
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


# ---------- tags (auto-derived from budget_rules) ----------


def _bare_tag_rules(ledger: LedgerWriter) -> list[dict[str, Any]]:
    """Custom-tag rows from budget_rules: category is a bare tag (no ", "),
    with its persisted `type`. budget_rules is the durable, dual-mode source
    (Supabase in hosted mode, CSV locally) — unlike uniques.json which is on
    Vercel's read-only filesystem and can't persist there."""
    df = ledger.read("budget_rules")
    if df.empty:
        return []
    out: list[dict[str, Any]] = []
    has_type = "type" in df.columns
    for _, row in df.iterrows():
        cat = str(row["category"])
        if ", " in cat:  # built-in "Type, Category" — not a custom tag
            continue
        t = row["type"] if has_type else None
        type_val = str(t) if (t is not None and str(t) not in ("", "nan", "<NA>")) else None
        out.append({"name": cat, "type": type_val})
    return out


@router.get("/tags")
def list_tags(ledger: LedgerWriter = Depends(get_ledger)) -> dict[str, Any]:
    """Known custom tags with their Need/Want/Investment type.

    Sourced from budget_rules (durable in both storage modes), merged with any
    uniques.json tags that don't yet have a rule (CSV-mode legacy). Used by the
    expense grid tag dropdown and the LLM categorizer. `tags` is the flat name
    list (back-compat); `items` carries each tag's type (null if unclassified)."""
    items: list[dict[str, Any]] = _bare_tag_rules(ledger)
    seen = {it["name"].lower() for it in items}
    # Fold in uniques tags that have no rule yet (local CSV mode), enriching
    # type from uniques.tag_types where available.
    for u in uniques_store.list_tags_with_types():
        if u["name"].lower() not in seen:
            items.append({"name": u["name"], "type": u.get("type")})
            seen.add(u["name"].lower())
    return {"tags": [it["name"] for it in items], "items": items}


@router.post("/tags", status_code=201)
def create_tag(
    payload: TagCreateIn,
    ledger: LedgerWriter = Depends(get_ledger),
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    """Create a custom spend tag classified as Need / Want / Investment.

    Three effects, all idempotent on the tag name:
      1. registers the tag in uniques (so the LLM sees it and never invents one);
      2. records the tag -> type mapping (so the grouped Table C view rolls it up);
      3. auto-creates a budget_rules row keyed by the tag string, giving the tag
         its own Table C line. The overflow matcher already picks up expenses
         whose `custom_tag` equals this category, so spend flows through with no
         engine change. Recompute refreshes Table C immediately.
    """
    name = payload.name.strip()
    if not name:
        raise HTTPException(400, "tag name cannot be blank")

    # uniques is the CSV-mode store; in supabase mode it's read-only and these
    # are best-effort (the durable copy is the budget_rules.type column below).
    uniques_store.add_tag(name)
    uniques_store.set_tag_type(name, payload.type)

    df = ledger.read("budget_rules")
    exists = not df.empty and (df["category"].astype("string") == name).any()
    rule = {
        "category": name,
        "monthly_budget": float(payload.monthly_budget),
        "carry_cap": float(payload.carry_cap),
        "priority": int(payload.priority),
        "type": payload.type,  # durable home for the tag's type
    }
    if exists:
        ledger.update("budget_rules", name, rule)
    else:
        ledger.append("budget_rules", rule)

    runner.recompute_all()
    return {"name": name, "type": payload.type, "rule": rule}


# ---------- adjustments (manual Add/Set buttons) ----------


@router.post("/adjust")
def adjust_budget(
    payload: BudgetAdjustIn,
    ledger: LedgerWriter = Depends(get_ledger),
    runner: BudgetRunner = Depends(get_budget_runner),
) -> dict[str, Any]:
    rules_df = ledger.read("budget_rules")
    if rules_df.empty or not (rules_df["category"].astype("string") == payload.category).any():
        raise HTTPException(404, f"category '{payload.category}' not found in budget_rules")

    state = runner.apply_adjustment(
        category=payload.category,
        amount=float(payload.amount),
        kind=payload.kind,
        note=payload.note,
    )
    runner.recompute_all()
    return {
        "category": state.category,
        "current_budget": state.current_budget,
        "last_rolled_month": state.last_rolled_month,
        "updated_at": state.updated_at,
    }
