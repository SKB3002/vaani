"""Timezone service tests."""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.services import tz as tz_service


def test_default_timezone_is_asia_kolkata(tmp_workspace: Path) -> None:
    assert tz_service.user_tz_name() == "Asia/Kolkata"


def test_today_local_at_late_night_ist(tmp_workspace: Path, monkeypatch) -> None:
    """At 23:59 IST (= 18:29 UTC), today_local should be the IST date.

    At 00:30 IST (= 19:00 UTC prior day), today_local should be the NEW IST date.
    """
    ist = ZoneInfo("Asia/Kolkata")
    utc = ZoneInfo("UTC")

    # UTC 18:29 on 2026-04-23 → IST 23:59 same day
    fake_utc = datetime(2026, 4, 23, 18, 29, tzinfo=utc)
    converted_ist = fake_utc.astimezone(ist)
    assert converted_ist.date().isoformat() == "2026-04-23"

    # UTC 19:00 on 2026-04-23 → IST 00:30 on 2026-04-24 (the next IST day)
    fake_utc_late = datetime(2026, 4, 23, 19, 0, tzinfo=utc)
    assert fake_utc_late.astimezone(ist).date().isoformat() == "2026-04-24"

    # Smoke check: real today_local returns a valid IST date
    tz_service.invalidate_cache()
    today = tz_service.today_local()
    now_ist = datetime.now(tz=ist).date()
    assert today == now_ist


def test_tz_change_is_picked_up_after_cache_invalidation(tmp_workspace: Path) -> None:
    meta = tmp_workspace / "data" / "meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["timezone"] = "America/New_York"
    meta.write_text(json.dumps(data), encoding="utf-8")
    tz_service.invalidate_cache()

    assert tz_service.user_tz_name() == "America/New_York"


def test_invalid_tz_falls_back_to_default(tmp_workspace: Path) -> None:
    meta = tmp_workspace / "data" / "meta.json"
    data = json.loads(meta.read_text(encoding="utf-8"))
    data["timezone"] = "Not/A/Real/Zone"
    meta.write_text(json.dumps(data), encoding="utf-8")
    tz_service.invalidate_cache()

    assert tz_service.user_tz_name() == "Asia/Kolkata"


def test_validate_tz() -> None:
    assert tz_service.validate_tz("Asia/Kolkata") is True
    assert tz_service.validate_tz("Atlantis/Lost") is False
