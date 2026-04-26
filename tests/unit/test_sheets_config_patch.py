"""PATCH /api/sheets/config — URL parsing + reload trigger."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_patch_accepts_full_sheets_url(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.patch(
            "/api/sheets/config",
            json={
                "spreadsheet_url": "https://docs.google.com/spreadsheets/d/ABC123xyz/edit#gid=0"
            },
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["spreadsheet_id"] == "ABC123xyz"


def test_patch_accepts_bare_id(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.patch(
            "/api/sheets/config", json={"spreadsheet_id": "SomeBareId_42"}
        )
    assert r.status_code == 200
    assert r.json()["spreadsheet_id"] == "SomeBareId_42"


def test_patch_rejects_garbage_url(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.patch(
            "/api/sheets/config",
            json={"spreadsheet_url": "https://example.com/no-id-here"},
        )
    assert r.status_code == 400


def test_patch_rejects_string_with_slash_but_no_id(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.patch("/api/sheets/config", json={"spreadsheet_id": "foo/bar"})
    assert r.status_code == 400


def test_patch_enabled_triggers_reload(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.patch("/api/sheets/config", json={"enabled": True})
    assert r.status_code == 200
    assert r.json()["reload_performed"] is True


def test_patch_no_change_does_not_reload(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        # No-op patch: no fields provided.
        r = client.patch("/api/sheets/config", json={})
    assert r.status_code == 200
    assert r.json()["reload_performed"] is False


def test_patch_spreadsheet_change_triggers_reload(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r1 = client.patch("/api/sheets/config", json={"spreadsheet_id": "AAA"})
        assert r1.json()["reload_performed"] is True
        # Same id again -> no reload.
        r2 = client.patch("/api/sheets/config", json={"spreadsheet_id": "AAA"})
        assert r2.json()["reload_performed"] is False
