"""Goals A (overview) + Goals B (source breakdown) CRUD + contribute."""
from __future__ import annotations

from typing import Any

import pandas as pd
import ulid
from fastapi import APIRouter, Depends, HTTPException, Query

from app.deps import get_ledger
from app.models.goals import ContributeIn, GoalAIn, GoalAPatch, GoalBIn, GoalBPatch
from app.services.goals import enrich_goal_a, enrich_goal_b
from app.services.ledger import LedgerWriter

router = APIRouter(prefix="/api/goals", tags=["goals"])


def _df_records(df: Any) -> list[dict[str, Any]]:
    safe = df.astype(object).where(df.notna(), None)
    return safe.to_dict(orient="records")  # type: ignore[no-any-return]


def _sanitize(row: dict[str, Any]) -> dict[str, Any]:
    """Coerce pandas NA / NaN to None so FastAPI can JSON-encode."""
    import math

    out: dict[str, Any] = {}
    for k, v in row.items():
        if v is None or isinstance(v, float) and math.isnan(v):
            out[k] = None
        else:
            try:
                if pd.isna(v):
                    out[k] = None
                    continue
            except (TypeError, ValueError):
                pass
            out[k] = v
    return out


# ---------- Overview (Table A) ----------


@router.get("/overview")
def list_overview(ledger: LedgerWriter = Depends(get_ledger)) -> list[dict[str, Any]]:
    df = ledger.read("goals_a")
    rows = _df_records(df)
    return [enrich_goal_a(r) for r in rows]


@router.post("/overview", status_code=201)
def create_overview(
    payload: GoalAIn,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "goal_id": str(ulid.new()),
        "goal_name": payload.goal_name,
        "target_amount": float(payload.target_amount),
        "current_amount": float(payload.current_amount),
        "monthly_contribution": float(payload.monthly_contribution),
        "pct_complete": 0.0,
        "months_left": None,
        "status": "just_started",
        "import_batch_id": None,
    }
    enrich_goal_a(row)
    # persist derived fields too (computed fresh on read; stored for convenience)
    ledger.append("goals_a", row)
    return row


@router.patch("/overview/{goal_id}")
def patch_overview(
    goal_id: str,
    patch: GoalAPatch,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    updates = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(400, "no updates provided")
    current = ledger.update("goals_a", goal_id, updates)
    if current is None:
        raise HTTPException(404, "goal not found")
    # Recompute derived
    current = _sanitize(current)
    enrich_goal_a(current)
    ledger.update(
        "goals_a",
        goal_id,
        {
            "pct_complete": current["pct_complete"],
            "months_left": current["months_left"],
            "status": current["status"],
        },
    )
    return current


@router.delete("/overview/{goal_id}", status_code=204)
def delete_overview(
    goal_id: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> None:
    if not ledger.delete("goals_a", goal_id):
        raise HTTPException(404, "goal not found")


# ---------- Sources (Table B) ----------


@router.get("/sources")
def list_sources(ledger: LedgerWriter = Depends(get_ledger)) -> list[dict[str, Any]]:
    df = ledger.read("goals_b")
    rows = _df_records(df)
    return [enrich_goal_b(r) for r in rows]


@router.post("/sources", status_code=201)
def create_source(
    payload: GoalBIn,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "goal_id": str(ulid.new()),
        "goal_name": payload.goal_name,
        "target_amount": float(payload.target_amount),
        "manual_saved": float(payload.manual_saved),
        "auto_added": float(payload.auto_added),
        "total_saved": 0.0,
        "monthly_contribution": float(payload.monthly_contribution),
        "pct_complete": 0.0,
        "months_left": None,
        "status": "just_started",
        "import_batch_id": None,
    }
    enrich_goal_b(row)
    ledger.append("goals_b", row)
    return row


def _sync_a_from_b(
    ledger: LedgerWriter, b_row: dict[str, Any]
) -> None:
    """Find a row in goals_a with matching goal_name and sync current_amount."""
    name = b_row.get("goal_name")
    if not name:
        return
    df = ledger.read("goals_a")
    if df.empty:
        return
    mask = df["goal_name"].astype("string") == str(name)
    if not mask.any():
        return
    goal_id = str(df.loc[mask].iloc[0]["goal_id"])
    new_current = float(b_row.get("total_saved") or 0)
    updated = ledger.update("goals_a", goal_id, {"current_amount": new_current})
    if updated is not None:
        updated = _sanitize(updated)
        enrich_goal_a(updated)
        ledger.update(
            "goals_a",
            goal_id,
            {
                "pct_complete": updated["pct_complete"],
                "months_left": updated["months_left"],
                "status": updated["status"],
            },
        )


@router.patch("/sources/{goal_id}")
def patch_source(
    goal_id: str,
    patch: GoalBPatch,
    sync_to_overview: bool = Query(False),
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    updates = {k: v for k, v in patch.model_dump(exclude_unset=True).items() if v is not None}
    if not updates:
        raise HTTPException(400, "no updates provided")
    current = ledger.update("goals_b", goal_id, updates)
    if current is None:
        raise HTTPException(404, "goal not found")
    current = _sanitize(current)
    enrich_goal_b(current)
    ledger.update(
        "goals_b",
        goal_id,
        {
            "total_saved": current["total_saved"],
            "pct_complete": current["pct_complete"],
            "months_left": current["months_left"],
            "status": current["status"],
        },
    )
    if sync_to_overview:
        _sync_a_from_b(ledger, current)
    return current


@router.delete("/sources/{goal_id}", status_code=204)
def delete_source(
    goal_id: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> None:
    if not ledger.delete("goals_b", goal_id):
        raise HTTPException(404, "goal not found")


@router.post("/sources/{goal_id}/contribute")
def contribute(
    goal_id: str,
    payload: ContributeIn,
    sync_to_overview: bool = Query(False),
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    df = ledger.read("goals_b")
    if df.empty:
        raise HTTPException(404, "goal not found")
    mask = df["goal_id"].astype("string") == str(goal_id)
    if not mask.any():
        raise HTTPException(404, "goal not found")
    row = _sanitize(df.loc[mask].iloc[0].to_dict())
    if payload.kind == "manual":
        row["manual_saved"] = float(row.get("manual_saved") or 0) + float(payload.amount)
    else:
        row["auto_added"] = float(row.get("auto_added") or 0) + float(payload.amount)
    enrich_goal_b(row)
    updates = {
        "manual_saved": float(row["manual_saved"]),
        "auto_added": float(row["auto_added"]),
        "total_saved": float(row["total_saved"]),
        "pct_complete": float(row["pct_complete"]),
        "months_left": row["months_left"],
        "status": row["status"],
    }
    updated = ledger.update("goals_b", goal_id, updates)
    if updated is None:
        raise HTTPException(500, "update failed")
    updated = _sanitize(updated)
    enrich_goal_b(updated)
    if sync_to_overview:
        _sync_a_from_b(ledger, updated)
    return updated
