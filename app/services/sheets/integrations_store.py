"""Runtime integrations config (`data/meta/integrations.json`).

Authoritative at runtime for the Sheets setup UI. `.env` / settings remain
first-boot defaults; any UI-driven edit goes here and overrides the env.

Resolution order for every read:
    1. `data/meta/integrations.json` (if present)
    2. `settings.GOOGLE_SHEETS_*` env vars (fallback)

We never store the service-account JSON content in this file — only the
filesystem path to it, and the extracted `client_email` for the UI hint.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from app.config import Settings

logger = logging.getLogger("vaani.sheets.integrations")

_FILE_LOCK = threading.Lock()


@dataclass
class SheetsIntegrationConfig:
    enabled: bool
    spreadsheet_id: str
    credentials_path: str
    client_email: str

    @property
    def credentials_uploaded(self) -> bool:
        if not self.credentials_path:
            return False
        return Path(self.credentials_path).exists()

    @property
    def is_complete(self) -> bool:
        return bool(
            self.enabled
            and self.spreadsheet_id
            and self.credentials_uploaded
        )


def _integrations_path(data_dir: Path) -> Path:
    return data_dir / "meta" / "integrations.json"


def _read_raw(data_dir: Path) -> dict[str, Any]:
    path = _integrations_path(data_dir)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("integrations.json unreadable (%s); ignoring", exc)
        return {}


def _write_raw(data_dir: Path, data: dict[str, Any]) -> None:
    path = _integrations_path(data_dir)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    with _FILE_LOCK:
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)


def load_sheets_config(data_dir: Path, settings: Settings) -> SheetsIntegrationConfig:
    """Resolve the current Sheets config. integrations.json overrides env."""
    raw = _read_raw(data_dir).get("sheets", {})
    return SheetsIntegrationConfig(
        enabled=bool(raw.get("enabled", settings.GOOGLE_SHEETS_ENABLED)),
        spreadsheet_id=str(
            raw.get("spreadsheet_id", settings.GOOGLE_SHEETS_SPREADSHEET_ID) or ""
        ),
        credentials_path=str(
            raw.get("credentials_path", settings.GOOGLE_SHEETS_CREDENTIALS_PATH) or ""
        ),
        client_email=str(raw.get("client_email", "") or ""),
    )


def update_sheets_config(data_dir: Path, **updates: Any) -> dict[str, Any]:
    """Merge-update the sheets section. Returns the new sheets dict."""
    with _FILE_LOCK:
        all_data = _read_raw(data_dir)
        sheets = dict(all_data.get("sheets", {}))
        for key, value in updates.items():
            if value is None:
                sheets.pop(key, None)
            else:
                sheets[key] = value
        all_data["sheets"] = sheets
        path = _integrations_path(data_dir)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(all_data, indent=2, sort_keys=True), encoding="utf-8")
        tmp.replace(path)
        return sheets


__all__ = [
    "SheetsIntegrationConfig",
    "load_sheets_config",
    "update_sheets_config",
]
