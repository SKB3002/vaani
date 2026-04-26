"""Import-wizard models (§4.10b)."""
from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field


class UploadResponse(BaseModel):
    upload_id: str
    source_filename: str
    sheet_names: list[str]
    active_sheet: str
    detected_columns: list[str]
    dtype_guesses: dict[str, str]
    preview: list[dict[str, Any]] = Field(default_factory=list, description="First 20 rows")
    row_count: int


class MappingRequest(BaseModel):
    target_table: Literal["expenses", "investments", "wishlist", "goals_a", "goals_b"]
    mapping: dict[str, str] = Field(
        default_factory=dict,
        description="source column name -> target schema column name (ignored if preset_id given)",
    )
    sheet_name: str | None = None
    date_format: str | None = None
    preset_id: str | None = Field(
        default=None,
        description="If set, overrides mapping / date_format / row_filters from the preset.",
    )
    row_filters: dict[str, Any] = Field(
        default_factory=dict,
        description="Optional row filter config (skip_when_payment_equals, detect_balance_adjust).",
    )
    auto_create_columns: bool = Field(
        default=False,
        description="When true, unmapped source columns auto-register as user columns on the target.",
    )


class DryRunRowError(BaseModel):
    row_index: int
    errors: list[str]


class ChecksumReportEntry(BaseModel):
    day: str
    computed_total: float
    declared_total: float
    match: bool
    delta: float


class DryRunReport(BaseModel):
    upload_id: str
    target_table: str
    total_rows: int
    valid_rows: int
    invalid_rows: int
    duplicate_rows: int
    skipped_rows: int = 0
    balance_adjust_rows: int = 0
    errors: list[DryRunRowError] = Field(default_factory=list)
    warnings: list[DryRunRowError] = Field(default_factory=list)
    suggested_mapping: dict[str, str] = Field(default_factory=dict)
    checksum_report: list[ChecksumReportEntry] = Field(default_factory=list)


class CommitRequest(BaseModel):
    on_invalid: Literal["skip", "abort", "draft"] = "skip"


class CommitSummary(BaseModel):
    batch_id: str
    target_table: str
    total: int
    inserted: int
    duplicates: int
    drafted: int
    errors: int
    skipped: int = 0
    balance_adjusts: int = 0
    demo_data_present: bool = False


class PresetSummary(BaseModel):
    id: str
    label: str
    target_table: str
    date_format: str | None = None
    column_mapping: dict[str, str]
    row_filters: dict[str, Any] = Field(default_factory=dict)


class PresetListResponse(BaseModel):
    presets: list[PresetSummary]


class BatchMeta(BaseModel):
    batch_id: str
    source_filename: str
    sha256: str
    sheet_name: str | None = None
    target_table: str
    mapping: dict[str, str]
    row_counts: dict[str, int]
    imported_at: str


class SuggestMappingResponse(BaseModel):
    suggestions: dict[str, str]
    confidence: dict[str, float]
