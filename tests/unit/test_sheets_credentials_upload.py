"""POST /api/sheets/credentials — validation + safe storage."""
from __future__ import annotations

import json
from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app

FAKE_CREDS = {
    "type": "service_account",
    "project_id": "fake-project",
    "private_key_id": "abc",
    "private_key": "-----BEGIN PRIVATE KEY-----\nFAKEKEY\n-----END PRIVATE KEY-----\n",
    "client_email": "fineye-sync@fake-project.iam.gserviceaccount.com",
    "client_id": "123",
    "auth_uri": "https://accounts.google.com/o/oauth2/auth",
    "token_uri": "https://oauth2.googleapis.com/token",
}


def test_upload_valid_creds_saves_file_and_extracts_email(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        payload = json.dumps(FAKE_CREDS).encode("utf-8")
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.json", payload, "application/json")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["ok"] is True
    assert body["client_email"] == FAKE_CREDS["client_email"]
    assert "Share" in body["hint"]

    dest = tmp_workspace / "data" / ".secrets" / "service_account.json"
    assert dest.exists()
    assert json.loads(dest.read_text(encoding="utf-8"))["client_email"] == FAKE_CREDS["client_email"]

    integrations = tmp_workspace / "data" / "meta" / "integrations.json"
    raw = json.loads(integrations.read_text(encoding="utf-8"))
    assert raw["sheets"]["client_email"] == FAKE_CREDS["client_email"]
    assert raw["sheets"]["credentials_path"].endswith("service_account.json")
    # Must NOT auto-enable sync.
    assert raw["sheets"].get("enabled", False) is False


def test_upload_rejects_non_json(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.txt", b"not json", "text/plain")},
        )
    assert r.status_code == 400
    assert "json" in r.json()["detail"].lower()


def test_upload_rejects_invalid_json_payload(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.json", b"{not valid json", "application/json")},
        )
    assert r.status_code == 400
    assert "invalid JSON" in r.json()["detail"]


def test_upload_rejects_missing_client_email(tmp_workspace: Path) -> None:
    bad = {**FAKE_CREDS}
    del bad["client_email"]
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.json", json.dumps(bad).encode(), "application/json")},
        )
    assert r.status_code == 400
    assert "client_email" in r.json()["detail"]


def test_upload_rejects_missing_private_key(tmp_workspace: Path) -> None:
    bad = {**FAKE_CREDS}
    del bad["private_key"]
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.json", json.dumps(bad).encode(), "application/json")},
        )
    assert r.status_code == 400
    assert "private_key" in r.json()["detail"]


def test_upload_rejects_wrong_type(tmp_workspace: Path) -> None:
    bad = {**FAKE_CREDS, "type": "authorized_user"}
    app = create_app()
    with TestClient(app) as client:
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.json", json.dumps(bad).encode(), "application/json")},
        )
    assert r.status_code == 400
    assert "service_account" in r.json()["detail"]


def test_delete_credentials_clears_file_and_config(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as client:
        payload = json.dumps(FAKE_CREDS).encode("utf-8")
        r = client.post(
            "/api/sheets/credentials",
            files={"file": ("svc.json", payload, "application/json")},
        )
        assert r.status_code == 200

        r = client.delete("/api/sheets/credentials")
    assert r.status_code == 200
    assert r.json()["ok"] is True
    dest = tmp_workspace / "data" / ".secrets" / "service_account.json"
    assert not dest.exists()
    raw = json.loads(
        (tmp_workspace / "data" / "meta" / "integrations.json").read_text(encoding="utf-8")
    )
    assert raw["sheets"].get("credentials_path") is None or "credentials_path" not in raw["sheets"]
    assert raw["sheets"].get("enabled") is False
