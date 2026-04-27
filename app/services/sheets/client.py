"""Thin gspread wrapper — only used when GOOGLE_SHEETS_ENABLED.

Imports gspread lazily so the rest of the app works even when the lib is
missing or creds are not configured.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import gspread

logger = logging.getLogger("vaani.sheets.client")

SHEETS_SCOPES = ("https://www.googleapis.com/auth/spreadsheets",)
REQUEST_TIMEOUT_S = 15.0


class SheetsClientError(Exception):
    """Raised when the Sheets client cannot fulfil a request."""


class SheetsClient:
    """Service-account backed gspread wrapper.

    Lazy init: no network calls in __init__. `_ensure_opened()` does the auth
    + `open_by_key` on first use.
    """

    def __init__(
        self,
        credentials_path: str | Path,
        spreadsheet_id: str,
        *,
        timeout: float = REQUEST_TIMEOUT_S,
    ) -> None:
        self.credentials_path = Path(credentials_path)
        self.spreadsheet_id = spreadsheet_id
        self.timeout = timeout
        self._client: gspread.Client | None = None
        self._spreadsheet: gspread.Spreadsheet | None = None

    # ---------- auth ----------
    def _ensure_opened(self) -> gspread.Spreadsheet:
        if self._spreadsheet is not None:
            return self._spreadsheet
        try:
            import gspread
            from google.oauth2.service_account import Credentials
        except ImportError as exc:  # pragma: no cover - import guard
            raise SheetsClientError(
                "gspread / google-auth not installed; pip install gspread google-auth"
            ) from exc

        if not self.credentials_path.exists():
            raise SheetsClientError(
                f"service account credentials not found at {self.credentials_path}"
            )
        if not self.spreadsheet_id:
            raise SheetsClientError("spreadsheet_id is empty")

        creds = Credentials.from_service_account_file(  # type: ignore[no-untyped-call]
            str(self.credentials_path), scopes=list(SHEETS_SCOPES)
        )
        client = gspread.authorize(creds)
        # gspread uses an httplib2 transport; set per-request timeout where possible.
        try:
            client.http_client.timeout = self.timeout
        except AttributeError:
            pass
        self._client = client
        self._spreadsheet = client.open_by_key(self.spreadsheet_id)
        return self._spreadsheet

    # ---------- introspection ----------
    def spreadsheet_title(self) -> str:
        return str(self._ensure_opened().title)

    def list_tabs(self) -> list[str]:
        return [ws.title for ws in self._ensure_opened().worksheets()]

    # ---------- tab management ----------
    def ensure_tab(self, name: str, headers: list[str]) -> Any:
        """Ensure a tab exists with the given headers.

        - If missing: create, write headers, freeze row 1.
        - If present: add any missing columns at the end (never deletes).
        """
        ss = self._ensure_opened()
        try:
            ws = ss.worksheet(name)
        except Exception:  # noqa: BLE001 - gspread.WorksheetNotFound varies by version
            ws = ss.add_worksheet(title=name, rows=1000, cols=max(len(headers), 10))
            ws.update("A1", [headers])  # type: ignore[arg-type]
            try:
                ws.freeze(rows=1)
            except Exception:  # noqa: BLE001
                logger.debug("freeze(rows=1) failed for tab %s", name)
            return ws

        existing = ws.row_values(1)
        missing = [h for h in headers if h not in existing]
        if missing:
            merged = [*existing, *missing]
            ws.update("A1", [merged])  # type: ignore[arg-type]
        return ws

    # ---------- row ops ----------
    def _find_row_index(self, ws: Any, key_column: str, key_value: str) -> int | None:
        header = ws.row_values(1)
        if key_column not in header:
            return None
        col_idx = header.index(key_column) + 1
        cell = ws.find(str(key_value), in_column=col_idx)
        if cell is None:
            return None
        # Skip header if the find happened to match row 1 (defensive).
        return int(cell.row) if int(cell.row) > 1 else None

    def upsert_row(
        self,
        tab: str,
        row_dict: dict[str, Any],
        key_column: str = "id",
    ) -> None:
        ws = self._ensure_opened().worksheet(tab)
        header = ws.row_values(1)
        # Add any columns present in row_dict but missing in header (forward-only).
        extras = [k for k in row_dict if k not in header]
        if extras:
            header = [*header, *extras]
            ws.update("A1", [header])  # type: ignore[arg-type]
        row_values = [_serialise(row_dict.get(col)) for col in header]

        key_value = row_dict.get(key_column)
        if key_value is None:
            ws.append_row(row_values, value_input_option="USER_ENTERED")  # type: ignore[arg-type]
            return

        row_idx = self._find_row_index(ws, key_column, str(key_value))
        if row_idx is None:
            ws.append_row(row_values, value_input_option="USER_ENTERED")  # type: ignore[arg-type]
        else:
            end_col = _col_letter(len(header))
            ws.update(f"A{row_idx}:{end_col}{row_idx}", [row_values])  # type: ignore[arg-type]

    def delete_row(self, tab: str, key_value: str, key_column: str = "id") -> bool:
        ws = self._ensure_opened().worksheet(tab)
        row_idx = self._find_row_index(ws, key_column, str(key_value))
        if row_idx is None:
            return False
        ws.delete_rows(row_idx)
        return True

    def read_all(self, tab: str) -> list[dict[str, Any]]:
        ws = self._ensure_opened().worksheet(tab)
        return list(ws.get_all_records())

    def batch_upsert(
        self,
        tab: str,
        rows: list[dict[str, Any]],
        key_column: str = "id",
    ) -> int:
        """Simple loop-based batch upsert. Returns count written."""
        count = 0
        for row in rows:
            self.upsert_row(tab, row, key_column)
            count += 1
        return count


def _serialise(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "True" if value else "False"
    return value


def _col_letter(n: int) -> str:
    """1-indexed column number -> A1 letter (1=A, 27=AA)."""
    out = ""
    while n > 0:
        n, r = divmod(n - 1, 26)
        out = chr(ord("A") + r) + out
    return out
