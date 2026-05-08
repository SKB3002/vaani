"""Insights API — AI-narrated monthly briefing.

Read-only. Computes a deterministic stats bundle in pandas, optionally
narrates it via Groq gpt-oss-120b, caches the narration. The route is the
ONLY layer that knows about caching — narrator and aggregator stay pure.
"""
from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config import Settings, get_settings
from app.deps import get_insights_cache, get_ledger
from app.services.insights.aggregator import build_monthly_bundle, bundle_hash
from app.services.insights.cache import (
    PROMPT_VERSION_MONTHLY_BRIEFING,
    InsightsCache,
    compute_cache_key,
)
from app.services.insights.narrator import (
    NarrationContractError,
    narrate_briefing,
)
from app.services.ledger import LedgerWriter
from app.services.llm import GroqLLMClient, LLMParseError, LLMTransportError

log = logging.getLogger("vaani.insights.route")

router = APIRouter(prefix="/api/insights", tags=["insights"])

_MONTH_RE = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


def _make_analysis_llm(cfg: Settings) -> GroqLLMClient:
    """Construct a GroqLLMClient configured for the analysis model.

    Mirrors `app/services/insights/llm_client.py::get_analysis_llm_client`
    but returns the underlying `GroqLLMClient` directly because
    `narrate_briefing` consumes that surface (via `chat_json`). Uses the
    fallback model so transient 429/5xx on the analysis model degrade
    onto the secondary model rather than failing the whole route.
    """
    return GroqLLMClient(
        api_key=cfg.GROQ_API_KEY,
        model=cfg.GROQ_ANALYSIS_MODEL,
        base_url=cfg.GROQ_BASE_URL,
        fallback_model=cfg.GROQ_FALLBACK_MODEL or None,
    )


@router.get("/monthly")
async def get_monthly_briefing(
    response: Response,
    month: str = Query(..., description="YYYY-MM, e.g. 2026-04"),
    refresh: bool = Query(False, description="Bypass cache and regenerate"),
    ledger: LedgerWriter = Depends(get_ledger),
    cache: InsightsCache = Depends(get_insights_cache),
) -> dict[str, Any]:
    """Returns {stats_bundle, narration, cache_hit, generated_at}."""
    response.headers["Cache-Control"] = "no-cache"

    if not _MONTH_RE.match(month):
        raise HTTPException(status_code=422, detail="month must be YYYY-MM")

    bundle = build_monthly_bundle(month, ledger)

    # Empty month — short-circuit before any cache or LLM work.
    if bundle.current_month.txn_count == 0:
        return {
            "stats_bundle": bundle.model_dump(mode="json"),
            "narration": None,
            "cache_hit": False,
            "generated_at": None,
            "reason": "empty_month",
        }

    cfg = get_settings()
    key = compute_cache_key(
        kind="monthly_briefing",
        bundle_hash_value=bundle_hash(bundle),
        month=bundle.month,
        prompt_version=PROMPT_VERSION_MONTHLY_BRIEFING,
        model=cfg.GROQ_ANALYSIS_MODEL,
    )

    if not refresh:
        cached = cache.get(kind="monthly_briefing", key_hash=key)
        if cached is not None:
            return {
                "stats_bundle": bundle.model_dump(mode="json"),
                "narration": cached.get("narration"),
                "cache_hit": True,
                "generated_at": cached.get("generated_at"),
            }

    # Cache miss path. Skip LLM entirely if no API key configured.
    if not cfg.GROQ_API_KEY:
        return {
            "stats_bundle": bundle.model_dump(mode="json"),
            "narration": None,
            "cache_hit": False,
            "generated_at": None,
            "reason": "groq_not_configured",
        }

    llm = _make_analysis_llm(cfg)
    narration = None
    reason: str | None = None
    try:
        narration = await narrate_briefing(
            bundle,
            llm=llm,
            max_retries=cfg.INSIGHTS_NARRATION_MAX_RETRIES,
        )
        if narration is None:
            # Narrator returned None — Groq unreachable.
            reason = "groq_unreachable"
    except LLMTransportError as exc:
        log.warning("narrate_briefing transport error: %s", exc)
        reason = "groq_unreachable"
    except NarrationContractError:
        log.exception("narrate_briefing contract violation (bug)")
        reason = "contract_violation"
    except LLMParseError:
        log.exception("narrate_briefing returned unparseable JSON")
        reason = "bad_json"

    if narration is None:
        return {
            "stats_bundle": bundle.model_dump(mode="json"),
            "narration": None,
            "cache_hit": False,
            "generated_at": None,
            "reason": reason or "narration_unavailable",
        }

    payload = {
        "month": bundle.month,
        "model": cfg.GROQ_ANALYSIS_MODEL,
        "prompt_version": PROMPT_VERSION_MONTHLY_BRIEFING,
        "generated_at": datetime.now(UTC).isoformat(),
        "narration": narration.model_dump(mode="json"),
    }
    cache.put(kind="monthly_briefing", key_hash=key, payload=payload)

    return {
        "stats_bundle": bundle.model_dump(mode="json"),
        "narration": narration.model_dump(mode="json"),
        "cache_hit": False,
        "generated_at": payload["generated_at"],
    }
