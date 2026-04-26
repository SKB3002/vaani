"""integrations.json round-trip + env fallback resolution order."""
from __future__ import annotations

import json
from pathlib import Path

from app.config import Settings
from app.services.sheets.integrations_store import (
    load_sheets_config,
    update_sheets_config,
)


def _settings(**overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "GOOGLE_SHEETS_ENABLED": False,
        "GOOGLE_SHEETS_SPREADSHEET_ID": "",
        "GOOGLE_SHEETS_CREDENTIALS_PATH": "",
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


def test_load_returns_env_defaults_when_no_file(tmp_workspace: Path) -> None:
    s = _settings(
        GOOGLE_SHEETS_ENABLED=True,
        GOOGLE_SHEETS_SPREADSHEET_ID="ENV_ID",
        GOOGLE_SHEETS_CREDENTIALS_PATH="env/path.json",
    )
    cfg = load_sheets_config(tmp_workspace / "data", s)
    assert cfg.enabled is True
    assert cfg.spreadsheet_id == "ENV_ID"
    assert cfg.credentials_path == "env/path.json"
    assert cfg.client_email == ""


def test_integrations_json_overrides_env(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    update_sheets_config(
        data_dir,
        enabled=True,
        spreadsheet_id="FILE_ID",
        credentials_path="file/path.json",
        client_email="sa@example.iam.gserviceaccount.com",
    )
    s = _settings(
        GOOGLE_SHEETS_ENABLED=False,  # env says false
        GOOGLE_SHEETS_SPREADSHEET_ID="ENV_ID",
    )
    cfg = load_sheets_config(data_dir, s)
    assert cfg.enabled is True  # file wins
    assert cfg.spreadsheet_id == "FILE_ID"
    assert cfg.credentials_path == "file/path.json"
    assert cfg.client_email == "sa@example.iam.gserviceaccount.com"


def test_update_merges_partial_fields(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    update_sheets_config(data_dir, spreadsheet_id="A", enabled=False)
    update_sheets_config(data_dir, enabled=True)  # only enabled changes
    raw = json.loads((data_dir / "meta" / "integrations.json").read_text(encoding="utf-8"))
    assert raw["sheets"]["spreadsheet_id"] == "A"
    assert raw["sheets"]["enabled"] is True


def test_update_with_none_removes_key(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    update_sheets_config(
        data_dir, credentials_path="some/path.json", client_email="x@y.z"
    )
    update_sheets_config(data_dir, credentials_path=None, client_email=None)
    raw = json.loads((data_dir / "meta" / "integrations.json").read_text(encoding="utf-8"))
    assert "credentials_path" not in raw["sheets"]
    assert "client_email" not in raw["sheets"]


def test_credentials_uploaded_reflects_filesystem(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    path = data_dir / ".secrets" / "svc.json"
    path.parent.mkdir(parents=True)
    path.write_text("{}", encoding="utf-8")
    update_sheets_config(data_dir, credentials_path=str(path))
    cfg = load_sheets_config(data_dir, _settings())
    assert cfg.credentials_uploaded is True

    path.unlink()
    cfg2 = load_sheets_config(data_dir, _settings())
    assert cfg2.credentials_uploaded is False
