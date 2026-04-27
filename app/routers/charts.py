"""Charts API — registry listing + single-chart payload + cache refresh.

Charts are declared in `data/meta/charts.yaml`. Adding a chart = one YAML entry.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException, Response

from app.config import get_settings
from app.deps import get_ledger
from app.services.charts.aggregator import compute_chart
from app.services.charts.registry import ChartRegistry, RegistryError, load_registry
from app.services.ledger import LedgerWriter

router = APIRouter(prefix="/api/charts", tags=["charts"])


def _registry_path() -> Path:
    return get_settings().resolved_data_dir() / "meta" / "charts.yaml"


@lru_cache(maxsize=1)
def _cached_registry() -> ChartRegistry:
    return load_registry(_registry_path())


def _load_or_422() -> ChartRegistry:
    try:
        return _cached_registry()
    except RegistryError as e:
        raise HTTPException(status_code=422, detail=f"chart registry invalid: {e}") from e


def _make_loader(ledger: LedgerWriter) -> Any:
    def loader(source: str) -> pd.DataFrame:
        try:
            return ledger.read(source)
        except Exception:  # noqa: BLE001 - unknown table surfaces as empty frame
            return pd.DataFrame()

    return loader


@router.get("")
def list_charts() -> dict[str, Any]:
    reg = _load_or_422()
    return {
        "version": reg.version,
        "charts": [
            {"id": c.id, "title": c.title, "type": c.type, "source": c.source}
            for c in reg.charts
        ],
    }


@router.get("/{chart_id}")
def get_chart(
    chart_id: str,
    response: Response,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    reg = _load_or_422()
    spec = reg.get(chart_id)
    if spec is None:
        raise HTTPException(status_code=404, detail=f"chart not found: {chart_id}")
    response.headers["Cache-Control"] = "no-cache"
    payload = compute_chart(spec, _make_loader(ledger))
    return payload.model_dump()


@router.post("/refresh")
def refresh_registry() -> dict[str, Any]:
    _cached_registry.cache_clear()
    reg = _load_or_422()
    return {"ok": True, "count": len(reg.charts)}
