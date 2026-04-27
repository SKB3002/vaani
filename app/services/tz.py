"""Timezone service (§4.10a).

Reads `meta.json.timezone` with a small in-process TTL cache so every request
doesn't re-read the file, but a SettingsPatch is visible within a few seconds.
"""
from __future__ import annotations

import json
import time
from datetime import UTC, date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo, available_timezones

from app.config import get_settings

_CACHE: dict[str, tuple[float, str]] = {}
_TTL_SECONDS = 5.0


def _meta_path() -> Path:
    return get_settings().resolved_data_dir() / "meta.json"


def _read_timezone_name() -> str:
    settings = get_settings()
    key = str(_meta_path())
    now = time.time()
    cached = _CACHE.get(key)
    if cached and (now - cached[0] < _TTL_SECONDS):
        return cached[1]

    path = _meta_path()
    tz_name = settings.default_timezone
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            tz_name = data.get("timezone") or settings.default_timezone
        except (json.JSONDecodeError, OSError):
            tz_name = settings.default_timezone

    if tz_name not in available_timezones():
        tz_name = settings.default_timezone
    _CACHE[key] = (now, tz_name)
    return tz_name


def invalidate_cache() -> None:
    """Call after writing meta.json so the next call picks up the new tz."""
    _CACHE.clear()


def user_tz() -> ZoneInfo:
    return ZoneInfo(_read_timezone_name())


def user_tz_name() -> str:
    return _read_timezone_name()


def today_local() -> date:
    return datetime.now(tz=user_tz()).date()


def now_utc() -> datetime:
    return datetime.now(tz=UTC)


def validate_tz(name: str) -> bool:
    return name in available_timezones()
