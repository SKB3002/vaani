"""End-to-end UI-driven Sheets setup flow.

Walks the full sequence the UI performs:
    upload creds -> PATCH url -> PATCH enable=true -> GET status
All Google network calls stay mocked via the fake gspread/google modules.
"""
from __future__ import annotations

import json
import sys
import types
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

FAKE_CREDS = {
    "type": "service_account",
    "project_id": "fake",
    "private_key_id": "k",
    "private_key": "-----BEGIN PRIVATE KEY-----\nFAKE\n-----END PRIVATE KEY-----\n",
    "client_email": "fineye@fake.iam.gserviceaccount.com",
    "client_id": "1",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}


class FakeWs:
    def __init__(self, title: str = "T") -> None:
        self.title = title

    def row_values(self, _: int) -> list[Any]:
        return []


class FakeSpreadsheet:
    title = "UI Flow Spreadsheet"

    def worksheets(self) -> list[FakeWs]:
        return [FakeWs("expenses")]


class FakeClient:
    def __init__(self, ss: FakeSpreadsheet) -> None:
        self._ss = ss
        self.http_client = types.SimpleNamespace(timeout=None)

    def open_by_key(self, key: str) -> FakeSpreadsheet:
        return self._ss


@pytest.fixture
def fake_gspread(monkeypatch: pytest.MonkeyPatch) -> FakeSpreadsheet:
    ss = FakeSpreadsheet()
    client = FakeClient(ss)

    fake_gspread = types.ModuleType("gspread")
    fake_gspread.authorize = lambda creds: client  # type: ignore[attr-defined]

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


def test_full_ui_flow(tmp_workspace: Path, fake_gspread: FakeSpreadsheet) -> None:
    from app.main import create_app

    app = create_app()
    with TestClient(app) as client:
        # 1. Initial status: nothing set up.
        r = client.get("/api/sheets/status")
        assert r.status_code == 200
        s = r.json()
        assert s["enabled"] is False
        assert s["credentials_uploaded"] is False
        assert s["spreadsheet_id"] is None
        assert s["client_email"] is None

        # 2. Upload creds.
        r = client.post(
            "/api/sheets/credentials",
            files={
                "file": (
                    "svc.json",
                    json.dumps(FAKE_CREDS).encode(),
                    "application/json",
                )
            },
        )
        assert r.status_code == 200
        assert r.json()["client_email"] == FAKE_CREDS["client_email"]

        # 3. Set spreadsheet via URL.
        r = client.patch(
            "/api/sheets/config",
            json={
                "spreadsheet_url": "https://docs.google.com/spreadsheets/d/SHEET_ABC/edit"
            },
        )
        assert r.status_code == 200
        body = r.json()
        assert body["spreadsheet_id"] == "SHEET_ABC"
        assert body["reload_performed"] is True

        # 4. Enable sync.
        r = client.patch("/api/sheets/config", json={"enabled": True})
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is True
        assert body["reload_performed"] is True

        # 5. Status reflects full setup + connected (probe hits FakeSpreadsheet).
        r = client.get("/api/sheets/status")
        s = r.json()
        assert s["enabled"] is True
        assert s["credentials_uploaded"] is True
        assert s["spreadsheet_id"] == "SHEET_ABC"
        assert s["client_email"] == FAKE_CREDS["client_email"]
        assert s["connected"] is True

        # 6. Toggle off — reload tears down.
        r = client.patch("/api/sheets/config", json={"enabled": False})
        assert r.status_code == 200
        assert r.json()["enabled"] is False

        r = client.get("/api/sheets/status")
        assert r.json()["enabled"] is False
        assert r.json()["connected"] is False
