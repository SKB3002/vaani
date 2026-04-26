"""Demo-data lifecycle: status + purge seed rows produced by scripts/seed.py.

Seed rows are tagged with `source="demo"` (expenses, balances). Other tables
don't currently seed any rows but are listed for future-proofing.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel

from app.config import get_settings
from app.deps import get_ledger
from app.services.ledger import LedgerWriter

router = APIRouter(prefix="/api/demo-data", tags=["demo-data"])

# Table → (column, value) to identify demo rows
DEMO_MARKERS: dict[str, tuple[str, str]] = {
    "expenses": ("source", "demo"),
    "balances": ("reason", "demo"),
}


class PurgeRequest(BaseModel):
    tables: list[str] | None = None


def _count_demo_rows(ledger: LedgerWriter, table: str, column: str, value: str) -> int:
    df = ledger.read(table)
    if df.empty or column not in df.columns:
        return 0
    return int((df[column].astype("string") == value).sum())


@router.get("/status")
def status(ledger: LedgerWriter = Depends(get_ledger)) -> dict[str, Any]:
    counts: dict[str, int] = {}
    for table, (col, val) in DEMO_MARKERS.items():
        counts[table] = _count_demo_rows(ledger, table, col, val)
    return {
        "has_demo_data": any(v > 0 for v in counts.values()),
        "counts": counts,
        "purged_marker_present": (get_settings().resolved_data_dir() / ".demo_purged").exists(),
    }


@router.post("/purge")
def purge(
    payload: PurgeRequest | None = None,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    wanted = set(payload.tables) if payload and payload.tables else set(DEMO_MARKERS.keys())
    removed: dict[str, int] = {}
    for table, (col, val) in DEMO_MARKERS.items():
        if table not in wanted:
            continue
        n = ledger.delete_where(table, col, val)
        removed[table] = n

    # Drop the "seed" marker so re-running scripts.seed refuses unless --force.
    marker = get_settings().resolved_data_dir() / ".demo_purged"
    marker.write_text("purged\n", encoding="utf-8")

    return {"removed": removed, "total": sum(removed.values())}


def demo_data_present(ledger: LedgerWriter) -> bool:
    for table, (col, val) in DEMO_MARKERS.items():
        if _count_demo_rows(ledger, table, col, val) > 0:
            return True
    return False
