"""Settings endpoints — GET/PATCH meta.json."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, HTTPException

from app.config import get_settings
from app.models.settings import Caps, SettingsPatch, SettingsRead
from app.services import tz as tz_service

router = APIRouter(prefix="/api/settings", tags=["settings"])


def _meta_path() -> Path:
    return get_settings().resolved_data_dir() / "meta.json"


def _load_meta() -> dict[str, Any]:
    path = _meta_path()
    if not path.exists():
        return {
            "currency": get_settings().default_currency,
            "timezone": get_settings().default_timezone,
            "caps": {"medical_upper_cap": 10000, "emergency_monthly_cap": 5000},
        }
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return data


def _save_meta(data: dict[str, Any]) -> None:
    path = _meta_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tz_service.invalidate_cache()


@router.get("", response_model=SettingsRead)
def get_settings_endpoint() -> SettingsRead:
    data = _load_meta()
    return SettingsRead(
        currency=data.get("currency", "INR"),
        timezone=data.get("timezone", "Asia/Kolkata"),
        caps=Caps(**data.get("caps", {})),
    )


@router.patch("")
def patch_settings(patch: SettingsPatch) -> dict[str, Any]:
    meta = _load_meta()
    tz_changed = False
    if patch.timezone is not None and patch.timezone != meta.get("timezone"):
        if not tz_service.validate_tz(patch.timezone):
            raise HTTPException(400, f"invalid timezone: {patch.timezone}")
        meta["timezone"] = patch.timezone
        tz_changed = True
    if patch.currency is not None:
        meta["currency"] = patch.currency
    if patch.caps is not None:
        meta["caps"] = patch.caps.model_dump()
    _save_meta(meta)
    return {
        "currency": meta["currency"],
        "timezone": meta["timezone"],
        "caps": meta["caps"],
        "tz_changed": tz_changed,
    }
