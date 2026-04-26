"""Goals derived fields — pct_complete, months_left, status at boundaries."""
from __future__ import annotations

from app.services.goals import derive_months_left, derive_pct, derive_status


def test_pct_zero() -> None:
    assert derive_pct(1000, 0) == 0.0


def test_pct_99() -> None:
    assert derive_pct(100, 99) == 99.0


def test_pct_100() -> None:
    assert derive_pct(100, 100) == 100.0


def test_pct_over_100() -> None:
    assert derive_pct(100, 150) == 150.0


def test_pct_target_zero() -> None:
    assert derive_pct(0, 50) == 0.0


def test_months_left_simple() -> None:
    assert derive_months_left(1000, 0, 100) == 10


def test_months_left_ceil() -> None:
    assert derive_months_left(1000, 0, 300) == 4  # ceil(1000/300)=4


def test_months_left_achieved() -> None:
    assert derive_months_left(100, 100, 50) == 0
    assert derive_months_left(100, 150, 50) == 0


def test_months_left_zero_monthly() -> None:
    assert derive_months_left(1000, 0, 0) is None


def test_status_thresholds() -> None:
    assert derive_status(0) == "just_started"
    assert derive_status(9.9) == "just_started"
    assert derive_status(10) == "in_progress"
    assert derive_status(79.9) == "in_progress"
    assert derive_status(80) == "nearing_goal"
    assert derive_status(99.9) == "nearing_goal"
    assert derive_status(100) == "achieved"
    assert derive_status(150) == "achieved"
