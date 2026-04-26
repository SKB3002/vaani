"""Chart registry loader validation tests."""
from __future__ import annotations

from pathlib import Path

import pytest

from app.services.charts.registry import RegistryError, load_registry


def _write(p: Path, text: str) -> Path:
    p.write_text(text, encoding="utf-8")
    return p


def test_default_registry_loads(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "charts.yaml",
        """
version: 1
charts:
  - id: cumulative_types_pie
    title: "Types"
    source: expenses
    type: pie
    group_by: type
  - id: monthly_stack
    title: "Monthly"
    source: expenses
    type: stacked_bar
    x: date
    series: type
    time_bucket: month
  - id: category_donut
    title: "Cat"
    source: expenses
    type: donut
    group_by: category
  - id: goal_progress
    title: "Goals"
    source: goals_a
    type: horizontal_bar
    x: goal_name
    series: [current_amount, target_amount]
  - id: daily_spend_line
    title: "Daily"
    source: expenses
    type: line
    x: date
    time_bucket: day
  - id: top_vendors
    title: "Vendors"
    source: expenses
    type: bar
    x: expense_name
    top_n: 10
""",
    )
    reg = load_registry(p)
    assert len(reg.charts) == 6
    assert reg.get("top_vendors") is not None


def test_unknown_source_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.yaml",
        """
version: 1
charts:
  - id: bad
    title: X
    source: not_a_table
    type: pie
    group_by: type
""",
    )
    with pytest.raises(RegistryError):
        load_registry(p)


def test_invalid_type_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.yaml",
        """
version: 1
charts:
  - id: bad
    title: X
    source: expenses
    type: radar
    group_by: type
""",
    )
    with pytest.raises(RegistryError):
        load_registry(p)


def test_duplicate_id_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.yaml",
        """
version: 1
charts:
  - id: a
    title: A
    source: expenses
    type: pie
    group_by: type
  - id: a
    title: B
    source: expenses
    type: donut
    group_by: type
""",
    )
    with pytest.raises(RegistryError):
        load_registry(p)


def test_pie_requires_group_by(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.yaml",
        """
version: 1
charts:
  - id: bad_pie
    title: Bad
    source: expenses
    type: pie
""",
    )
    with pytest.raises(RegistryError):
        load_registry(p)


def test_stacked_bar_requires_x_and_series(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.yaml",
        """
version: 1
charts:
  - id: bad_stack
    title: Bad
    source: expenses
    type: stacked_bar
    x: date
""",
    )
    with pytest.raises(RegistryError):
        load_registry(p)


def test_unsafe_filter_rejected(tmp_path: Path) -> None:
    p = _write(
        tmp_path / "c.yaml",
        """
version: 1
charts:
  - id: bad_filter
    title: Bad
    source: expenses
    type: pie
    group_by: type
    filter: "__import__('os').system('x')"
""",
    )
    with pytest.raises(RegistryError):
        load_registry(p)


def test_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RegistryError):
        load_registry(tmp_path / "nope.yaml")
