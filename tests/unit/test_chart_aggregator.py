"""Aggregator tests — pure functions over injected DataFrames."""
from __future__ import annotations

import pandas as pd

from app.services.charts.aggregator import compute_chart
from app.services.charts.registry import ChartSpec


def _expenses() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "id": [str(i) for i in range(6)],
            "date": [
                "2026-01-05",
                "2026-01-20",
                "2026-02-10",
                "2026-02-15",
                "2026-03-01",
                "2026-03-02",
            ],
            "expense_name": ["A", "B", "C", "D", "E", "F"],
            "type_category": [
                "Need, Travel",
                "Want, Food & Drinks",
                "Need, Travel",
                "Investment, Miscellaneous",
                "Want, Enjoyment",
                "Need, Food & Drinks",
            ],
            "amount": [100.0, 200.0, 150.0, 300.0, 50.0, 80.0],
        }
    )


def _loader_from(df: pd.DataFrame):  # noqa: ANN202
    def f(source: str) -> pd.DataFrame:
        return df.copy()

    return f


def test_pie_over_expense_types() -> None:
    spec = ChartSpec(
        id="p", title="Types", source="expenses", type="pie", group_by="type", y="amount"
    )
    out = compute_chart(spec, _loader_from(_expenses()))
    d = dict(zip(out.labels, out.datasets[0].data, strict=False))
    assert d["Need"] == 100.0 + 150.0 + 80.0
    assert d["Want"] == 200.0 + 50.0
    assert d["Investment"] == 300.0
    assert out.meta["total"] == 880.0
    assert out.meta.get("empty") is not True


def test_donut_category_breakdown() -> None:
    spec = ChartSpec(
        id="d", title="Cat", source="expenses", type="donut", group_by="category", y="amount"
    )
    out = compute_chart(spec, _loader_from(_expenses()))
    d = dict(zip(out.labels, out.datasets[0].data, strict=False))
    assert d["Travel"] == 250.0
    assert d["Food & Drinks"] == 280.0
    assert d["Miscellaneous"] == 300.0
    assert d["Enjoyment"] == 50.0


def test_monthly_stacked_bar() -> None:
    spec = ChartSpec(
        id="m",
        title="Monthly",
        source="expenses",
        type="stacked_bar",
        x="date",
        series="type",
        time_bucket="month",
        y="amount",
    )
    out = compute_chart(spec, _loader_from(_expenses()))
    assert out.labels == ["2026-01", "2026-02", "2026-03"]
    series_labels = sorted(d.label for d in out.datasets)
    assert series_labels == ["Investment", "Need", "Want"]


def test_filter_restricts_to_need() -> None:
    spec = ChartSpec(
        id="f",
        title="Need only",
        source="expenses",
        type="pie",
        group_by="category",
        y="amount",
        filter="type == 'Need'",
    )
    out = compute_chart(spec, _loader_from(_expenses()))
    total = sum(out.datasets[0].data)
    assert total == 100.0 + 150.0 + 80.0


def test_top_n_with_other() -> None:
    df = pd.DataFrame(
        {
            "expense_name": ["a", "b", "c", "d", "e"],
            "type_category": ["Need, Travel"] * 5,
            "amount": [100.0, 50.0, 25.0, 10.0, 5.0],
            "date": ["2026-01-01"] * 5,
        }
    )
    spec = ChartSpec(
        id="t",
        title="Top",
        source="expenses",
        type="bar",
        x="expense_name",
        y="amount",
        top_n=2,
        top_n_other=True,
        order_by="value_desc",
    )
    out = compute_chart(spec, _loader_from(df))
    assert out.labels == ["a", "b", "Other"]
    assert out.datasets[0].data == [100.0, 50.0, 25.0 + 10.0 + 5.0]


def test_empty_dataframe_yields_empty_meta() -> None:
    spec = ChartSpec(
        id="e", title="Empty", source="expenses", type="pie", group_by="type"
    )
    out = compute_chart(spec, _loader_from(pd.DataFrame()))
    assert out.meta.get("empty") is True
    assert out.labels == []
    assert out.datasets == []


def test_time_bucket_day() -> None:
    spec = ChartSpec(
        id="day",
        title="Daily",
        source="expenses",
        type="line",
        x="date",
        time_bucket="day",
        y="amount",
    )
    out = compute_chart(spec, _loader_from(_expenses()))
    # Daily bucket spans Jan 5 to Mar 2 inclusive (all days filled by pandas)
    assert out.labels[0] == "2026-01-05"
    assert out.labels[-1] == "2026-03-02"
    assert out.labels == sorted(out.labels)
    # Sum across all days must match total
    assert sum(out.datasets[0].data) == 880.0


def test_time_bucket_year() -> None:
    df = _expenses().copy()
    df.loc[0, "date"] = "2025-06-01"
    spec = ChartSpec(
        id="yr", title="Year", source="expenses", type="line", x="date", time_bucket="year", y="amount"
    )
    out = compute_chart(spec, _loader_from(df))
    assert "2025" in out.labels
    assert "2026" in out.labels


def test_horizontal_bar_multi_series() -> None:
    df = pd.DataFrame(
        {
            "goal_name": ["Car", "House"],
            "current_amount": [10000.0, 200000.0],
            "target_amount": [50000.0, 500000.0],
        }
    )
    spec = ChartSpec(
        id="g",
        title="Goals",
        source="goals_a",
        type="horizontal_bar",
        x="goal_name",
        series=["current_amount", "target_amount"],
    )
    out = compute_chart(spec, _loader_from(df))
    assert out.labels == ["Car", "House"]
    assert len(out.datasets) == 2
    labels = [d.label for d in out.datasets]
    assert "current_amount" in labels and "target_amount" in labels
