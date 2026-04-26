"""Excel/CSV import router (§4.10b)."""
from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import ulid
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

from app.config import get_settings
from app.deps import get_ledger
from app.models.imports import (
    ChecksumReportEntry,
    CommitRequest,
    CommitSummary,
    DryRunReport,
    DryRunRowError,
    MappingRequest,
    PresetListResponse,
    PresetSummary,
    UploadResponse,
)
from app.routers.demo_data import demo_data_present
from app.services.imports import committer, mapper, presets, sniff
from app.services.ledger import LedgerWriter
from app.storage.schemas import IMPORTABLE_TABLES

router = APIRouter(prefix="/api/import", tags=["import"])

MAX_UPLOAD_BYTES = 10 * 1024 * 1024  # 10 MB
ALLOWED_SUFFIXES = {".csv", ".xlsx", ".xls", ".tsv"}

# In-memory upload registry — uploads live under .tmp/ on disk.
# Key: upload_id, value: metadata dict
_UPLOADS: dict[str, dict[str, Any]] = {}


def _tmp_dir() -> Path:
    return get_settings().resolved_tmp_dir()


def _data_dir() -> Path:
    return get_settings().resolved_data_dir()


@router.post("/upload", response_model=UploadResponse)
async def upload(file: UploadFile = File(...)) -> UploadResponse:
    filename = file.filename or "upload.csv"
    suffix = Path(filename).suffix.lower()
    if suffix not in ALLOWED_SUFFIXES:
        raise HTTPException(400, f"unsupported file type: {suffix}")

    contents = await file.read()
    if len(contents) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "file too large (max 10MB)")

    upload_id = str(ulid.new())
    dest = _tmp_dir() / f"{upload_id}{suffix}"
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_bytes(contents)

    sha256 = hashlib.sha256(contents).hexdigest()

    sheets = sniff.list_sheets(dest)
    active_sheet = sheets[0] if sheets else ""
    preview_df, full_df = sniff.read_preview(dest, sheet_name=active_sheet or None)

    info = {
        "upload_id": upload_id,
        "source_filename": filename,
        "sha256": sha256,
        "path": str(dest),
        "sheet_names": sheets,
        "active_sheet": active_sheet,
        "row_count": int(len(full_df)),
    }
    _UPLOADS[upload_id] = info

    return UploadResponse(
        upload_id=upload_id,
        source_filename=filename,
        sheet_names=sheets,
        active_sheet=active_sheet,
        detected_columns=[str(c) for c in preview_df.columns],
        dtype_guesses=sniff.guess_dtypes(preview_df),
        preview=sniff.preview_to_records(preview_df),
        row_count=int(len(full_df)),
    )


@router.get("/{upload_id}/sheets")
def list_upload_sheets(upload_id: str) -> dict[str, Any]:
    info = _UPLOADS.get(upload_id)
    if info is None:
        raise HTTPException(404, "unknown upload_id")
    return {
        "upload_id": upload_id,
        "sheet_names": info.get("sheet_names", []),
        "active_sheet": info.get("active_sheet", ""),
    }


@router.get("/presets", response_model=PresetListResponse)
def list_presets() -> PresetListResponse:
    items = presets.load_presets(_data_dir())
    return PresetListResponse(
        presets=[
            PresetSummary(
                id=p.get("id", ""),
                label=p.get("label", ""),
                target_table=p.get("target_table", ""),
                date_format=p.get("date_format"),
                column_mapping=p.get("column_mapping", {}),
                row_filters=p.get("row_filters", {}),
            )
            for p in items
        ]
    )


@router.post("/{upload_id}/map", response_model=DryRunReport)
def map_and_dry_run(
    upload_id: str,
    payload: MappingRequest,
    ledger: LedgerWriter = Depends(get_ledger),  # noqa: ARG001 — future hook
) -> DryRunReport:
    info = _UPLOADS.get(upload_id)
    if info is None:
        raise HTTPException(404, "unknown upload_id")

    mapping = dict(payload.mapping)
    target_table: str = payload.target_table
    date_format = payload.date_format
    row_filters = dict(payload.row_filters)

    if payload.preset_id:
        preset = presets.get_preset(_data_dir(), payload.preset_id)
        if preset is None:
            raise HTTPException(404, f"unknown preset_id: {payload.preset_id}")
        mapping = dict(preset.get("column_mapping", {}))
        # User's explicit date_format choice overrides the preset default
        date_format = date_format or preset.get("date_format")
        row_filters = dict(preset.get("row_filters", {}))
        target_table = str(preset.get("target_table", target_table))

    if target_table not in IMPORTABLE_TABLES:
        raise HTTPException(400, f"table not importable: {target_table}")

    path = Path(info["path"])
    sheet_name = payload.sheet_name or info.get("active_sheet") or None
    _, full_df = sniff.read_preview(path, sheet_name=sheet_name)

    outcome = committer.dry_run(
        df=full_df,
        target_table=target_table,
        mapping=mapping,
        date_format=date_format,
        data_dir=_data_dir(),
        row_filters=row_filters,
    )

    info["mapping"] = mapping
    info["target_table"] = target_table
    info["sheet_name"] = sheet_name
    info["date_format"] = date_format
    info["row_filters"] = row_filters
    info["preset_id"] = payload.preset_id

    return DryRunReport(
        upload_id=upload_id,
        target_table=target_table,
        total_rows=int(len(full_df)),
        valid_rows=len(outcome.rows),
        invalid_rows=len(outcome.errors),
        duplicate_rows=len(outcome.duplicates),
        skipped_rows=len(outcome.skipped),
        balance_adjust_rows=len(outcome.balance_adjusts),
        errors=[DryRunRowError(row_index=i, errors=errs) for i, errs in outcome.errors[:100]],
        warnings=[DryRunRowError(row_index=i, errors=errs) for i, errs in outcome.warnings[:100]],
        suggested_mapping=mapping,
        checksum_report=[ChecksumReportEntry(**c.to_dict()) for c in outcome.checksum_report],
    )


@router.get("/{upload_id}/suggest")
def suggest_mapping(upload_id: str, target_table: str) -> dict[str, Any]:
    info = _UPLOADS.get(upload_id)
    if info is None:
        raise HTTPException(404, "unknown upload_id")
    if target_table not in IMPORTABLE_TABLES:
        raise HTTPException(400, "table not importable")
    path = Path(info["path"])
    preview, _ = sniff.read_preview(path, sheet_name=info.get("active_sheet") or None, nrows=1)
    cols = [str(c) for c in preview.columns]
    mapping, conf = mapper.suggest_mapping(cols, target_table)
    return {"suggestions": mapping, "confidence": conf}


@router.post("/{upload_id}/commit", response_model=CommitSummary)
def commit(
    upload_id: str,
    payload: CommitRequest,
    ledger: LedgerWriter = Depends(get_ledger),
) -> CommitSummary:
    info = _UPLOADS.get(upload_id)
    if info is None:
        raise HTTPException(404, "unknown upload_id")
    if "mapping" not in info or "target_table" not in info:
        raise HTTPException(400, "must call /map before /commit")

    path = Path(info["path"])
    target_table = info["target_table"]
    mapping = info["mapping"]
    sheet_name = info.get("sheet_name")
    date_format = info.get("date_format")
    row_filters = info.get("row_filters") or {}

    _, full_df = sniff.read_preview(path, sheet_name=sheet_name)

    outcome = committer.dry_run(
        df=full_df,
        target_table=target_table,
        mapping=mapping,
        date_format=date_format,
        data_dir=_data_dir(),
        row_filters=row_filters,
    )

    batch_id = str(ulid.new())
    try:
        counts = committer.commit(
            outcome=outcome,
            target_table=target_table,
            on_invalid=payload.on_invalid,
            batch_id=batch_id,
            ledger=ledger,
            data_dir=_data_dir(),
        )
    except ValueError as e:
        raise HTTPException(422, str(e)) from e

    row_counts = {
        "total": int(len(full_df)),
        "inserted": counts["inserted"],
        "duplicates": counts["duplicates"],
        "drafted": counts["drafted"],
        "errors": counts["errors"],
        "skipped": counts.get("skipped", 0),
        "balance_adjusts": counts.get("balance_adjusts", 0),
    }
    committer.write_batch_meta(
        data_dir=_data_dir(),
        batch_id=batch_id,
        source_filename=info["source_filename"],
        file_sha256=info["sha256"],
        sheet_name=sheet_name,
        target_table=target_table,
        mapping=mapping,
        row_counts=row_counts,
    )

    # cleanup upload scratch file
    try:
        Path(info["path"]).unlink(missing_ok=True)
    except OSError:
        pass
    _UPLOADS.pop(upload_id, None)

    return CommitSummary(
        batch_id=batch_id,
        target_table=target_table,
        total=row_counts["total"],
        inserted=row_counts["inserted"],
        duplicates=row_counts["duplicates"],
        drafted=row_counts["drafted"],
        errors=row_counts["errors"],
        skipped=row_counts.get("skipped", 0),
        balance_adjusts=row_counts.get("balance_adjusts", 0),
        demo_data_present=demo_data_present(ledger),
    )


@router.delete("/{batch_id}")
def rollback(
    batch_id: str,
    ledger: LedgerWriter = Depends(get_ledger),
) -> dict[str, Any]:
    result = committer.rollback_batch(ledger, _data_dir(), batch_id)
    if result["removed"] == 0 and committer.load_batch_meta(_data_dir(), batch_id) is None:
        raise HTTPException(404, f"batch {batch_id} not found")
    return result


@router.get("/batches")
def list_batches() -> list[dict[str, Any]]:
    imports_dir = _data_dir() / "imports"
    if not imports_dir.exists():
        return []
    out: list[dict[str, Any]] = []
    for p in sorted(imports_dir.glob("*.meta.json")):
        try:
            out.append(committer.load_batch_meta(_data_dir(), p.stem.replace(".meta", "")) or {})
        except (ValueError, OSError):
            continue
    return [b for b in out if b]


