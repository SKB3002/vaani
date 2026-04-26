"""Settings models (reflects meta.json)."""
from __future__ import annotations

from pydantic import BaseModel, Field, field_validator


class Caps(BaseModel):
    medical_upper_cap: float = Field(ge=0, default=10000)
    emergency_monthly_cap: float = Field(ge=0, default=5000)


class SettingsRead(BaseModel):
    currency: str
    timezone: str
    caps: Caps


class SettingsPatch(BaseModel):
    currency: str | None = None
    timezone: str | None = None
    caps: Caps | None = None

    @field_validator("timezone")
    @classmethod
    def _valid_tz(cls, v: str | None) -> str | None:
        if v is None:
            return v
        from zoneinfo import available_timezones

        if v not in available_timezones():
            raise ValueError(f"invalid timezone: {v}")
        return v
