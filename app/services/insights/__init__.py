"""AI Insights subsystem - aggregator, cache, narrator.

Re-exports the public surface so callers can import from the package root
without reaching into submodules.
"""
from __future__ import annotations

from app.services.insights.aggregator import (
    MonthlyStatsBundle,
    build_monthly_bundle,
    bundle_hash,
)
from app.services.insights.cache import (
    PROMPT_VERSION_CHAT_ANSWER,
    PROMPT_VERSION_MONTHLY_BRIEFING,
    InsightsCache,
    compute_cache_key,
    make_invalidator,
)
from app.services.insights.narrator import (
    Narration,
    NarrationContractError,
    NarrationSection,
    extract_allowed_stat_refs,
    find_violations,
    narrate_briefing,
)

__all__ = [
    "InsightsCache",
    "MonthlyStatsBundle",
    "Narration",
    "NarrationContractError",
    "NarrationSection",
    "PROMPT_VERSION_CHAT_ANSWER",
    "PROMPT_VERSION_MONTHLY_BRIEFING",
    "build_monthly_bundle",
    "bundle_hash",
    "compute_cache_key",
    "extract_allowed_stat_refs",
    "find_violations",
    "make_invalidator",
    "narrate_briefing",
]
