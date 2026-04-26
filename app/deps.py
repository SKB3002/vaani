"""FastAPI dependency providers — process-wide singletons."""
from __future__ import annotations

from functools import lru_cache

from app.config import get_settings
from app.services.balances import BalanceService
from app.services.budget_runner import BudgetRunner
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
