"""Chart registry: loads + validates `charts.yaml`.

A ChartSpec is a pure config object. Validation rules:
- `id` must be unique and URL-safe.
- `source` must be one of the known tables.
- `type` must be one of the supported chart types.
- `pie` / `donut` require `group_by`.
- `bar` / `line` / `area` / `horizontal_bar` require `x`.
- `stacked_bar` requires both `x` and `series`.
- Numeric aggregation (`y`) defaults to `amount`; `agg=count` allows `y` to be None.
- Invalid filter strings (fail `safe_query.validate_filter`) reject at load time.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

import yaml
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator

from app.services.charts.safe_query import UnsafeFilterError, validate_filter

ChartType = Literal[
    "pie", "donut", "bar", "stacked_bar", "line", "horizontal_bar", "area"
]
ChartSource = Literal[
    "expenses",
    "investments",
    "wishlist",
    "goals_a",
    "goals_b",
    "budget_table_c",
    "balances",
]
AggOp = Literal["sum", "count", "mean", "min", "max"]
OrderBy = Literal["value_desc", "value_asc", "x_asc", "x_desc"]
TimeBucket = Literal["day", "week", "month", "year"]
Format = Literal["currency", "number", "percent"]


class RegistryError(ValueError):
    """Raised when `charts.yaml` fails validation."""


class ChartSpec(BaseModel):
    id: str
    title: str
    type: ChartType
    source: ChartSource

    filter: str | None = None
    x: str | None = None
    y: str | None = None
    group_by: str | None = None
    series: str | list[str] | None = None
    agg: AggOp = "sum"
    order_by: OrderBy | None = None
    top_n: int | None = None
    top_n_other: bool = True
    time_bucket: TimeBucket | None = None
    format: Format = "currency"
    palette: list[str] | None = None

    @field_validator("id")
    @classmethod
    def _id_url_safe(cls, v: str) -> str:
        if not v or not all(c.isalnum() or c in "-_" for c in v):
            raise ValueError("id must be URL-safe (alnum, '-', '_')")
        return v

    @field_validator("filter")
    @classmethod
    def _filter_safe(cls, v: str | None) -> str | None:
        if v is None:
            return None
        try:
            return validate_filter(v)
        except UnsafeFilterError as e:
            raise ValueError(f"unsafe filter: {e}") from e

    @field_validator("top_n")
    @classmethod
    def _top_n_positive(cls, v: int | None) -> int | None:
        if v is not None and v <= 0:
            raise ValueError("top_n must be positive")
        return v

    @model_validator(mode="after")
    def _shape_rules(self) -> ChartSpec:
        t = self.type
        if t in ("pie", "donut"):
            if not self.group_by:
                raise ValueError(f"{t} requires group_by")
        elif t == "stacked_bar":
            if not self.x:
                raise ValueError("stacked_bar requires x")
            if not self.series:
                raise ValueError("stacked_bar requires series")
        elif t in ("bar", "line", "area", "horizontal_bar") and not self.x:
            raise ValueError(f"{t} requires x")

        # Default y for sum/mean/min/max aggregations (skip for count).
        if self.agg != "count" and not self.y and self.series is None:
            # y defaults to amount for expenses, but we leave blank here — the aggregator
            # will fall back to "amount" at runtime (most common numeric column).
            pass

        # Default ordering heuristic.
        if self.order_by is None:
            if t in ("pie", "donut", "bar", "horizontal_bar"):
                object.__setattr__(self, "order_by", "value_desc")
            elif t in ("line", "area", "stacked_bar"):
                object.__setattr__(self, "order_by", "x_asc")
        return self


class ChartRegistry(BaseModel):
    version: int = 1
    charts: list[ChartSpec] = Field(default_factory=list)

    @model_validator(mode="after")
    def _unique_ids(self) -> ChartRegistry:
        seen: set[str] = set()
        for c in self.charts:
            if c.id in seen:
                raise ValueError(f"duplicate chart id: {c.id}")
            seen.add(c.id)
        return self

    def get(self, chart_id: str) -> ChartSpec | None:
        for c in self.charts:
            if c.id == chart_id:
                return c
        return None


def load_registry(path: str | Path) -> ChartRegistry:
    p = Path(path)
    if not p.exists():
        raise RegistryError(f"charts.yaml not found at {p}")
    try:
        raw: Any = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise RegistryError(f"invalid YAML: {e}") from e
    if not isinstance(raw, dict):
        raise RegistryError("charts.yaml must be a mapping at the top level")
    try:
        return ChartRegistry.model_validate(raw)
    except ValidationError as e:
        raise RegistryError(str(e)) from e
