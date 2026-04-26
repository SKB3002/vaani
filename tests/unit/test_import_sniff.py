"""Sniffer tests on CSV + XLSX fixtures."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.imports import sniff


def _make_csv(path: Path) -> None:
    df = pd.DataFrame(
        {
            "Date": ["2026-04-20", "2026-04-21"],
            "Amt": ["₹1,200", "₹2,500"],
            "Description": ["Zomato", "HPCL"],
        }
    )
    df.to_csv(path, index=False, encoding="utf-8")


def _make_xlsx(path: Path) -> None:
    df = pd.DataFrame(
        {
            "Date": ["2026-04-20", "2026-04-21"],
            "Amt": [1200, 2500],
            "Description": ["Zomato", "HPCL"],
        }
    )
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df.to_excel(writer, sheet_name="Sheet1", index=False)
        df.head(1).to_excel(writer, sheet_name="Sheet2", index=False)


def test_sniff_csv(tmp_path: Path) -> None:
    csv = tmp_path / "test.csv"
    _make_csv(csv)

    sheets = sniff.list_sheets(csv)
    assert sheets == [""]

    preview, full = sniff.read_preview(csv)
    assert "Date" in preview.columns
    assert "Amt" in preview.columns
    assert len(full) == 2


def test_sniff_xlsx_multisheet(tmp_path: Path) -> None:
    xlsx = tmp_path / "test.xlsx"
    _make_xlsx(xlsx)

    sheets = sniff.list_sheets(xlsx)
    assert "Sheet1" in sheets
    assert "Sheet2" in sheets

    preview, _ = sniff.read_preview(xlsx, sheet_name="Sheet1")
    assert len(preview) == 2

    preview2, _ = sniff.read_preview(xlsx, sheet_name="Sheet2")
    assert len(preview2) == 1


def test_dtype_guess(tmp_path: Path) -> None:
    csv = tmp_path / "test.csv"
    _make_csv(csv)
    preview, _ = sniff.read_preview(csv)
    guesses = sniff.guess_dtypes(preview)
    assert set(guesses.keys()) == {"Date", "Amt", "Description"}
