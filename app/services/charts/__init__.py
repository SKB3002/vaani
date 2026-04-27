"""M5 — Rule-driven charts: registry loader + pandas aggregator + safe filter."""
from __future__ import annotations

from app.services.charts.aggregator import ChartPayload, Dataset, compute_chart
from app.services.charts.registry import (
    ChartRegistry,
    ChartSpec,
    RegistryError,
    load_registry,
)
from app.services.charts.safe_query import UnsafeFilterError, validate_filter

__all__ = [
    "ChartPayload",
    "ChartRegistry",
    "ChartSpec",
    "Dataset",
    "RegistryError",
    "UnsafeFilterError",
    "compute_chart",
    "load_registry",
    "validate_filter",
]
