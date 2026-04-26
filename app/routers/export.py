"""CSV export — download a single table or a full backup zip.

Users can always `Download CSV` or `Download everything (zip)` regardless of
whether Google Sheets is configured. This is the always-available backup path.
"""
from __future__ import annotations

import io
import zipfile
from datetime import UTC, datetime
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response, StreamingResponse

from app.config import get_settings
from app.deps import get_ledger
from app.storage.schemas import SCHEMAS

router = APIRouter(prefix="/api/export", tags=["export"])

# Tables a user would want to download. All live CSVs in data/.
EXPORTABLE_TABLES: tuple[str, ...] = (
    "expenses",
    "balances",
    "investments",
    "wishlist",
    "goals_a",
    "goals_b",
    "budget_rules",
    "budget_table_c",
)


def _data_dir() -> Path:
    return get_settings().resolved_data_dir()


def _table_csv_bytes(table: str) -> bytes:
    """Render the current table state to CSV bytes via the ledger (respects user columns)."""
    if table not in SCHEMAS:
        raise HTTPException(404, f"unknown table: {table}")
    ledger = get_ledger()
    df = ledger.read(table)
    buf = io.StringIO()
    df.to_csv(buf, index=False)
    return buf.getvalue().encode("utf-8")


@router.get("/tables")
def list_tables() -> dict[str, list[str]]:
    """Which tables are available for download."""
    return {"tables": list(EXPORTABLE_TABLES)}


@router.get("/{table}.csv")
def download_table_csv(table: str) -> Response:
    if table not in EXPORTABLE_TABLES:
        raise HTTPException(404, f"table not exportable: {table}")
    payload = _table_csv_bytes(table)
    stamp = datetime.now(tz=UTC).strftime("%Y%m%d")
    return Response(
        content=payload,
        media_type="text/csv; charset=utf-8",
        headers={
            "Content-Disposition": f'attachment; filename="fineye-{table}-{stamp}.csv"',
            "Cache-Control": "no-store",
        },
    )


@router.get("/all.zip")
def download_all_zip() -> StreamingResponse:
    """Zip of every exportable table + meta.json + uniques.json + charts.yaml.

    This is a durable "snapshot" of the entire FinEye state that can be
    re-imported (per-table CSVs) or stashed anywhere safe.
    """
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
        for table in EXPORTABLE_TABLES:
            try:
                zf.writestr(f"{table}.csv", _table_csv_bytes(table))
            except HTTPException:
                continue
        # Also include side-car configs so a restore reproduces the exact state.
        for extra in ("meta.json", "uniques.json", "meta/charts.yaml", "meta/import_presets.json"):
            p = _data_dir() / extra
            if p.exists():
                zf.writestr(f"meta/{p.name}", p.read_bytes())
    buf.seek(0)

    stamp = datetime.now(tz=UTC).strftime("%Y%m%d-%H%M%S")
    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="fineye-backup-{stamp}.zip"',
            "Cache-Control": "no-store",
        },
    )
