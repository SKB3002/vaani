"""Sheets API — when disabled, endpoints respond safely and have no side effects."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_status_when_disabled(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.get("/api/sheets/status")
        assert r.status_code == 200
        body = r.json()
        assert body["enabled"] is False
        assert body["connected"] is False
        assert body["queue_depth"] == 0


def test_test_connection_rejected_when_disabled(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/sheets/test-connection")
        assert r.status_code == 400


def test_sync_all_rejected_when_disabled(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post("/api/sheets/sync-all")
        assert r.status_code == 400
