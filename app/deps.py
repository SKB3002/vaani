"""FastAPI dependency providers — process-wide singletons."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.services.balances import BalanceService
from app.services.budget_runner import BudgetRunner
from app.services.insights.cache import InsightsCache
from app.services.insights.llm_client import (
    AnalysisLLMClient,
)
from app.services.insights.llm_client import (
    get_analysis_llm_client as _real_get_analysis_llm,
)
from app.services.ledger import LedgerWriter
from app.services.llm import LLMClient
from app.services.llm import get_llm_client as _real_get_llm


@lru_cache(maxsize=1)
def get_ledger() -> LedgerWriter:
    settings = get_settings()
    return LedgerWriter(settings.resolved_data_dir(), settings.resolved_wal_dir())


@lru_cache(maxsize=1)
def get_balance_service() -> BalanceService:
    return BalanceService(get_ledger())


@lru_cache(maxsize=1)
def get_budget_runner() -> BudgetRunner:
    settings = get_settings()
    return BudgetRunner(
        get_ledger(),
        settings.resolved_data_dir(),
        timezone=settings.default_timezone,
    )


def get_llm_client() -> LLMClient:
    return _real_get_llm()


@lru_cache(maxsize=1)
def get_insights_cache() -> InsightsCache:
    """Process-wide singleton for the narration cache.

    Wires the configured TTL and owner id from settings. Tests must call
    ``get_insights_cache.cache_clear()`` (the ``tmp_workspace`` fixture
    already clears the other singletons; extend it for cache-touching
    tests).
    """
    settings = get_settings()
    return InsightsCache(
        get_ledger(),
        ttl_days=settings.INSIGHTS_CACHE_TTL_DAYS,
        owner_id=settings.OWNER_ID,
    )


@lru_cache(maxsize=1)
def get_analysis_llm_client() -> AnalysisLLMClient:
    """Process-wide singleton for the insights analysis-model client.

    Independent of `get_llm_client()` — voice and analysis paths use
    different models and must not share configuration.
    """
    return _real_get_analysis_llm()
