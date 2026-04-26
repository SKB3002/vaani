"""SheetsClient — verified against a mocked gspread layer."""
from __future__ import annotations

import sys
import types
from pathlib import Path
from typing import Any

import pytest


class FakeCell:
    def __init__(self, row: int) -> None:
        self.row = row


class FakeWorksheet:
    def __init__(self, title: str, *, header: list[str] | None = None) -> None:
        self.title = title
        self._rows: list[list[Any]] = [header or []]
        self.updates: list[tuple[str, list[list[Any]]]] = []
        self.appended: list[list[Any]] = []
        self.deleted: list[int] = []
        self.frozen = False

    def row_values(self, row_idx: int) -> list[Any]:
        if row_idx > len(self._rows):
            return []
        return list(self._rows[row_idx - 1])

    def update(self, range_name: str, values: list[list[Any]]) -> None:
        self.updates.append((range_name, values))
        if range_name == "A1":
            self._rows[0] = list(values[0])

    def append_row(self, values: list[Any], value_input_option: str = "RAW") -> None:
        self.appended.append(list(values))
        self._rows.append(list(values))

    def find(self, text: str, in_column: int | None = None) -> FakeCell | None:
        col = (in_column or 1) - 1
        for i, row in enumerate(self._rows[1:], start=2):
            if col < len(row) and str(row[col]) == text:
                return FakeCell(i)
        return None

    def delete_rows(self, row_idx: int) -> None:
        self.deleted.append(row_idx)
        if row_idx - 1 < len(self._rows):
            self._rows.pop(row_idx - 1)

    def freeze(self, rows: int = 0) -> None:
        self.frozen = rows >= 1

    def get_all_records(self) -> list[dict[str, Any]]:
        header = self._rows[0]
        return [dict(zip(header, row, strict=False)) for row in self._rows[1:]]


class FakeSpreadsheet:
    def __init__(self) -> None:
        self.title = "Mocked Spreadsheet"
        self._sheets: dict[str, FakeWorksheet] = {}

    def worksheet(self, name: str) -> FakeWorksheet:
        if name not in self._sheets:
            raise _WorksheetNotFoundError(name)
        return self._sheets[name]

    def add_worksheet(self, title: str, rows: int, cols: int) -> FakeWorksheet:
        ws = FakeWorksheet(title)
        self._sheets[title] = ws
        return ws

    def worksheets(self) -> list[FakeWorksheet]:
        return list(self._sheets.values())


class FakeClient:
    def __init__(self, ss: FakeSpreadsheet) -> None:
        self._ss = ss
        self.http_client = types.SimpleNamespace(timeout=None)

    def open_by_key(self, key: str) -> FakeSpreadsheet:
        return self._ss


class _WorksheetNotFoundError(Exception):
    pass


@pytest.fixture
def fake_gspread(monkeypatch: pytest.MonkeyPatch) -> FakeSpreadsheet:
    ss = FakeSpreadsheet()
    client = FakeClient(ss)

    fake_gspread = types.ModuleType("gspread")
    fake_gspread.authorize = lambda creds: client  # type: ignore[attr-defined]
    fake_gspread.Client = FakeClient  # type: ignore[attr-defined]
    fake_gspread.Spreadsheet = FakeSpreadsheet  # type: ignore[attr-defined]

    fake_oauth = types.ModuleType("google.oauth2.service_account")

    class FakeCreds:
        @classmethod
        def from_service_account_file(cls, path: str, scopes: Any = None) -> FakeCreds:
            return cls()

    fake_oauth.Credentials = FakeCreds  # type: ignore[attr-defined]

    fake_google = types.ModuleType("google")
    fake_oauth2 = types.ModuleType("google.oauth2")

    monkeypatch.setitem(sys.modules, "gspread", fake_gspread)
    monkeypatch.setitem(sys.modules, "google", fake_google)
    monkeypatch.setitem(sys.modules, "google.oauth2", fake_oauth2)
    monkeypatch.setitem(sys.modules, "google.oauth2.service_account", fake_oauth)

    return ss


def test_ensure_tab_creates_and_freezes(fake_gspread: FakeSpreadsheet, tmp_path: Path) -> None:
    from app.services.sheets.client import SheetsClient

    creds = tmp_path / "svc.json"
    creds.write_text("{}", encoding="utf-8")
    client = SheetsClient(creds, "SHEET_ID")
    ws = client.ensure_tab("expenses", ["id", "date", "amount"])
    assert ws.frozen
    assert fake_gspread.worksheet("expenses").row_values(1) == ["id", "date", "amount"]


def test_ensure_tab_adds_missing_columns(fake_gspread: FakeSpreadsheet, tmp_path: Path) -> None:
    from app.services.sheets.client import SheetsClient

    fake_gspread.add_worksheet("expenses", 10, 5)
    fake_gspread.worksheet("expenses").update("A1", [["id", "date"]])

    creds = tmp_path / "svc.json"
    creds.write_text("{}", encoding="utf-8")
    client = SheetsClient(creds, "SHEET_ID")
    client.ensure_tab("expenses", ["id", "date", "amount", "notes"])
    header = fake_gspread.worksheet("expenses").row_values(1)
    assert header == ["id", "date", "amount", "notes"]


def test_upsert_appends_new_row(fake_gspread: FakeSpreadsheet, tmp_path: Path) -> None:
    from app.services.sheets.client import SheetsClient

    creds = tmp_path / "svc.json"
    creds.write_text("{}", encoding="utf-8")
    client = SheetsClient(creds, "SHEET_ID")
    client.ensure_tab("expenses", ["id", "amount"])
    client.upsert_row("expenses", {"id": "A", "amount": 10.0})

    ws = fake_gspread.worksheet("expenses")
    assert ws.appended == [["A", 10.0]]


def test_upsert_updates_existing_row(fake_gspread: FakeSpreadsheet, tmp_path: Path) -> None:
    from app.services.sheets.client import SheetsClient

    creds = tmp_path / "svc.json"
    creds.write_text("{}", encoding="utf-8")
    client = SheetsClient(creds, "SHEET_ID")
    client.ensure_tab("expenses", ["id", "amount"])
    client.upsert_row("expenses", {"id": "A", "amount": 10.0})
    client.upsert_row("expenses", {"id": "A", "amount": 20.0})

    ws = fake_gspread.worksheet("expenses")
    assert ws.appended == [["A", 10.0]]  # only one append
    # Should have at least one update into row 2.
    assert any("A2" in rng for rng, _ in ws.updates)


def test_delete_row(fake_gspread: FakeSpreadsheet, tmp_path: Path) -> None:
    from app.services.sheets.client import SheetsClient

    creds = tmp_path / "svc.json"
    creds.write_text("{}", encoding="utf-8")
    client = SheetsClient(creds, "SHEET_ID")
    client.ensure_tab("expenses", ["id", "amount"])
    client.upsert_row("expenses", {"id": "A", "amount": 10.0})
    assert client.delete_row("expenses", "A") is True
