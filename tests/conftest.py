"""Test fixtures — isolated data/ + .wal/ per test."""
from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest

from app.bootstrap import bootstrap_for
from app.services.ledger import LedgerWriter


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--live",
        action="store_true",
        default=False,
        help="Run tests marked @pytest.mark.live (hit real external APIs).",
    )


def pytest_configure(config: pytest.Config) -> None:
    config.addinivalue_line("markers", "live: hits real external APIs (skipped by default)")


def pytest_collection_modifyitems(
    config: pytest.Config, items: list[pytest.Item]
) -> None:
    if config.getoption("--live"):
        return
    skip_live = pytest.mark.skip(reason="live tests require --live")
    for item in items:
        if "live" in item.keywords:
            item.add_marker(skip_live)


@pytest.fixture
def tmp_workspace(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    data = tmp_path / "data"
    wal = tmp_path / ".wal"
    tmp_dir = tmp_path / ".tmp"
    data.mkdir()
    wal.mkdir()
    tmp_dir.mkdir()

    monkeypatch.setenv("FINEYE_DATA_DIR", str(data))
    monkeypatch.setenv("FINEYE_WAL_DIR", str(wal))
    monkeypatch.setenv("FINEYE_TMP_DIR", str(tmp_dir))

    # Force re-init of cached settings + deps
    from app import config as _cfg
    from app import deps as _deps
    from app.services import tz as _tz

    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()
    _deps.get_budget_runner.cache_clear()
    _tz.invalidate_cache()

    bootstrap_for(data, wal, tmp_dir)
    # chdir so any relative template paths still resolve if tests spawn a client
    cwd = os.getcwd()
    try:
        yield tmp_path
    finally:
        os.chdir(cwd)
        _cfg.get_settings.cache_clear()
        _deps.get_ledger.cache_clear()
        _deps.get_balance_service.cache_clear()
        _tz.invalidate_cache()


@pytest.fixture
def ledger(tmp_workspace: Path) -> LedgerWriter:
    return LedgerWriter(tmp_workspace / "data", tmp_workspace / ".wal")


@pytest.fixture
def personal_ledger_xlsx(tmp_path: Path) -> Path:
    """Generate a tiny Excel workbook matching the user's real personal-ledger layout.

    Columns (left to right):
        Date | Month | Vendor | Payment | Tags | (person helper blank) | Item | Amount |
        Cash balance | Online balance

    Contents include:
      - 14/04/2026 : an expense row with comma tag pair "Travel, Needs"
      - 14/04/2026 : a second expense row with "Food & Drinks, Wants"
      - 14/04/2026 : a "Total" row (daily summary) — must be SKIPPED on import
      - 15/04/2026 : "Wants" single-tag row (vendor "Croma - Kharghar")
      - 15/04/2026 : "Wants, Miscellaneous" row
      - 15/04/2026 : a "Total" row
      - 19/04/2026 : a balance-adjust row (empty payment, zero amount, cash +1000)
    """
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    assert ws is not None
    ws.title = "Ledger"

    headers = [
        "Date",
        "Month",
        "Vendor",
        "Payment",
        "Tags",
        "",
        "Item",
        "Amount",
        "Cash balance",
        "Online balance",
    ]
    ws.append(headers)

    rows = [
        ["14/04/2026", "Apr-2026", "HPCL", "Paid", "Travel, Needs", "", "Petrol", 500.0, None, None],
        ["14/04/2026", "Apr-2026", "Zomato", "Paid", "Food & Drinks, Wants", "", "Dinner", 450.0, None, None],
        ["14/04/2026", "Apr-2026", "", "Total", "", "", "", 950.0, 500.0, 44050.0],
        ["15/04/2026", "Apr-2026", "Croma - Kharghar", "Paid Cash", "Wants", "", "Ear Buds", 2199.0, None, None],
        ["15/04/2026", "Apr-2026", "Amazon", "Paid", "Wants, Miscellaneous", "", "XSR Guard", 1200.0, None, None],
        ["15/04/2026", "Apr-2026", "", "Total", "", "", "", 3399.0, 498.0, 42851.0],
        ["19/04/2026", "Apr-2026", "", "", "", "", "", 0.0, 1498.0, 42851.0],
    ]
    for r in rows:
        ws.append(r)

    dest = tmp_path / "personal_ledger_sample.xlsx"
    wb.save(dest)
    return dest
