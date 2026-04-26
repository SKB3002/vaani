"""Pure-function pandas aggregator: ChartSpec + DataFrame -> Chart.js-ready payload."""
from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pandas as pd
from pydantic import BaseModel, Field

from app.services.charts.derived import add_derived_columns
from app.services.charts.registry import ChartSpec

DataLoader = Callable[[str], pd.DataFrame]

_FREQ_MAP = {"day": "D", "week": "W-MON", "month": "MS", "year": "YS"}


class Dataset(BaseModel):
    label: str
    data: list[float]
    backgroundColor: list[str] | str | None = None  # noqa: N815 - Chart.js JSON key
    borderColor: list[str] | str | None = None  # noqa: N815 - Chart.js JSON key


class ChartPayload(BaseModel):
    chart_id: str
    type: str
    title: str
    labels: list[str] = Field(default_factory=list)
    datasets: list[Dataset] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


def compute_chart(spec: ChartSpec, data_loader: DataLoader) -> ChartPayload:
    """Compute a chart payload from a spec + injected data loader."""
    df_raw = data_loader(spec.source)
    df = add_derived_columns(df_raw, spec.source)

    if df.empty:
        return _empty_payload(spec, reason="no_rows")

    if spec.filter:
        try:
            df = df.query(spec.filter, engine="python")
        except Exception as e:  # noqa: BLE001 - pandas raises many subclasses
            return _empty_payload(spec, reason=f"filter_error: {e}")
        if df.empty:
            return _empty_payload(spec, reason="filter_empty")

    y_col = _resolve_y(spec, df)

    if spec.type in ("pie", "donut", "bar", "horizontal_bar"):
        if spec.type in ("bar", "horizontal_bar") and isinstance(spec.series, list):
            return _multi_series(spec, df)
        return _single_group(spec, df, y_col)

    if spec.type in ("line", "area"):
        return _time_series(spec, df, y_col)

    if spec.type == "stacked_bar":
        return _stacked_bar(spec, df, y_col)

    return _empty_payload(spec, reason=f"unsupported_type:{spec.type}")


# --------------------------------------------------------------------------- helpers


def _resolve_y(spec: ChartSpec, df: pd.DataFrame) -> str:
    if spec.agg == "count":
        return spec.y or spec.group_by or spec.x or df.columns[0]
    if spec.y and spec.y in df.columns:
        return spec.y
    if "amount" in df.columns:
        return "amount"
    # fall back to first numeric column
    for col in df.columns:
        if pd.api.types.is_numeric_dtype(df[col]):
            return str(col)
    return str(df.columns[0])


def _agg_series(series: pd.Series, op: str) -> float:
    numeric = pd.to_numeric(series, errors="coerce")
    if op == "sum":
        return float(numeric.sum())
    if op == "mean":
        return float(numeric.mean()) if len(numeric) else 0.0
    if op == "min":
        return float(numeric.min()) if len(numeric) else 0.0
    if op == "max":
        return float(numeric.max()) if len(numeric) else 0.0
    if op == "count":
        return float(len(series))
    return float(numeric.sum())


def _single_group(spec: ChartSpec, df: pd.DataFrame, y_col: str) -> ChartPayload:
    key_col = spec.group_by or spec.x
    if not key_col or key_col not in df.columns:
        return _empty_payload(spec, reason=f"missing_column:{key_col}")

    grouped: dict[str, float] = {}
    for key, sub in df.groupby(key_col, dropna=False):
        label = "" if pd.isna(key) else str(key)
        grouped[label] = _agg_series(sub[y_col], spec.agg)

    items = list(grouped.items())
    items = _order(items, spec.order_by)
    items = _apply_top_n(items, spec)

    labels = [k for k, _ in items]
    values = [v for _, v in items]
    total = sum(values)

    dataset = Dataset(
        label=spec.title,
        data=values,
        backgroundColor=spec.palette,
    )
    return ChartPayload(
        chart_id=spec.id,
        type=spec.type,
        title=spec.title,
        labels=labels,
        datasets=[dataset],
        meta=_meta(spec, row_count=int(len(df)), total=total),
    )


def _multi_series(spec: ChartSpec, df: pd.DataFrame) -> ChartPayload:
    """Bar / horizontal_bar with `series: [col_a, col_b]` — multiple y columns per x label."""
    assert isinstance(spec.series, list)
    x_col = spec.x
    if not x_col or x_col not in df.columns:
        return _empty_payload(spec, reason=f"missing_column:{x_col}")

    missing = [s for s in spec.series if s not in df.columns]
    if missing:
        return _empty_payload(spec, reason=f"missing_series:{missing}")

    labels = [str(v) for v in df[x_col].astype("string").tolist()]
    datasets: list[Dataset] = []
    palette = spec.palette or []
    for i, col in enumerate(spec.series):
        values = [float(v) for v in pd.to_numeric(df[col], errors="coerce").fillna(0.0)]
        color = palette[i] if i < len(palette) else None
        datasets.append(
            Dataset(label=col, data=values, backgroundColor=color, borderColor=color)
        )

    total = sum(sum(d.data) for d in datasets)
    return ChartPayload(
        chart_id=spec.id,
        type=spec.type,
        title=spec.title,
        labels=labels,
        datasets=datasets,
        meta=_meta(spec, row_count=int(len(df)), total=total),
    )


def _time_series(spec: ChartSpec, df: pd.DataFrame, y_col: str) -> ChartPayload:
    x_col = spec.x
    if not x_col or x_col not in df.columns:
        return _empty_payload(spec, reason=f"missing_column:{x_col}")

    bucket = spec.time_bucket or "month"
    freq = _FREQ_MAP.get(bucket, "MS")
    dates = pd.to_datetime(df[x_col], errors="coerce")
    work = df.assign(_ts=dates).dropna(subset=["_ts"])
    if work.empty:
        return _empty_payload(spec, reason="no_parseable_dates")

    grouper = pd.Grouper(key="_ts", freq=freq)
    if spec.agg == "count":
        grouped = work.groupby(grouper).size().astype("float64")
    else:
        grouped = work.groupby(grouper)[y_col].agg(spec.agg).astype("float64")

    items = [
        (_fmt_bucket(idx, bucket), float(val))
        for idx, val in grouped.items()
        if not pd.isna(val)
    ]
    items = _order(items, spec.order_by or "x_asc")

    labels = [k for k, _ in items]
    values = [v for _, v in items]
    total = sum(values)

    dataset = Dataset(label=spec.title, data=values, backgroundColor=spec.palette)
    return ChartPayload(
        chart_id=spec.id,
        type=spec.type,
        title=spec.title,
        labels=labels,
        datasets=[dataset],
        meta=_meta(spec, row_count=int(len(df)), total=total, time_bucket=bucket),
    )


def _stacked_bar(spec: ChartSpec, df: pd.DataFrame, y_col: str) -> ChartPayload:
    x_col = spec.x
    if not x_col or x_col not in df.columns:
        return _empty_payload(spec, reason=f"missing_column:{x_col}")

    series = spec.series
    if isinstance(series, list):
        # Treat as multi-series aligned with x labels.
        return _multi_series(spec, df)
    if not isinstance(series, str) or series not in df.columns:
        return _empty_payload(spec, reason=f"missing_series:{series}")

    work = df.copy()
    if spec.time_bucket:
        ts = pd.to_datetime(work[x_col], errors="coerce")
        work = work.assign(_ts=ts).dropna(subset=["_ts"])
        x_key = "_ts"
    else:
        x_key = x_col

    if work.empty:
        return _empty_payload(spec, reason="no_rows_after_bucket")

    if spec.time_bucket:
        pivot = work.pivot_table(
            index=pd.Grouper(key=x_key, freq=_FREQ_MAP.get(spec.time_bucket, "MS")),
            columns=series,
            values=y_col,
            aggfunc=spec.agg if spec.agg != "count" else "count",
            fill_value=0.0,
        )
        labels = [_fmt_bucket(idx, spec.time_bucket) for idx in pivot.index]
    else:
        pivot = work.pivot_table(
            index=x_key,
            columns=series,
            values=y_col,
            aggfunc=spec.agg if spec.agg != "count" else "count",
            fill_value=0.0,
        )
        labels = [str(idx) for idx in pivot.index]

    datasets: list[Dataset] = []
    palette = spec.palette or []
    for i, col in enumerate(pivot.columns):
        values = [float(v) for v in pivot[col].tolist()]
        color = palette[i] if i < len(palette) else None
        datasets.append(
            Dataset(label=str(col), data=values, backgroundColor=color, borderColor=color)
        )

    total = float(pivot.values.sum()) if pivot.size else 0.0
    return ChartPayload(
        chart_id=spec.id,
        type=spec.type,
        title=spec.title,
        labels=labels,
        datasets=datasets,
        meta=_meta(spec, row_count=int(len(df)), total=total, time_bucket=spec.time_bucket),
    )


# --------------------------------------------------------------------------- util


def _fmt_bucket(ts: Any, bucket: str | None) -> str:
    if pd.isna(ts):
        return ""
    if isinstance(ts, pd.Timestamp):
        if bucket == "year":
            return str(ts.year)
        if bucket == "month":
            return str(ts.strftime("%Y-%m"))
        if bucket == "week":
            return str(ts.strftime("%Y-W%V"))
        return str(ts.strftime("%Y-%m-%d"))
    return str(ts)


def _order(items: list[tuple[str, float]], mode: str | None) -> list[tuple[str, float]]:
    if mode == "value_desc":
        return sorted(items, key=lambda kv: kv[1], reverse=True)
    if mode == "value_asc":
        return sorted(items, key=lambda kv: kv[1])
    if mode == "x_desc":
        return sorted(items, key=lambda kv: kv[0], reverse=True)
    if mode == "x_asc":
        return sorted(items, key=lambda kv: kv[0])
    return items


def _apply_top_n(
    items: list[tuple[str, float]], spec: ChartSpec
) -> list[tuple[str, float]]:
    if not spec.top_n or spec.top_n >= len(items):
        return items
    head = items[: spec.top_n]
    tail = items[spec.top_n :]
    if spec.top_n_other and tail:
        head.append(("Other", sum(v for _, v in tail)))
    return head


def _meta(spec: ChartSpec, row_count: int, total: float, **extra: Any) -> dict[str, Any]:
    out: dict[str, Any] = {
        "row_count": row_count,
        "total": total,
        "format": spec.format,
        "agg": spec.agg,
        "source": spec.source,
        "generated_at": datetime.now(UTC).isoformat(),
    }
    out.update(extra)
    return out


def _empty_payload(spec: ChartSpec, reason: str) -> ChartPayload:
    return ChartPayload(
        chart_id=spec.id,
        type=spec.type,
        title=spec.title,
        labels=[],
        datasets=[],
        meta={
            "empty": True,
            "reason": reason,
            "format": spec.format,
            "source": spec.source,
            "generated_at": datetime.now(UTC).isoformat(),
        },
    )
