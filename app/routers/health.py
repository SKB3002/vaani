"""Health + liveness probe."""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.services.tz import user_tz_name

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"ok": "true", "status": "ok", "version": __version__, "tz": user_tz_name()}
