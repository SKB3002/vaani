"""Narrator digit-free / stat-ref contract tests (Plan goal G5)."""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.insights.aggregator import (
    MonthlyStatsBundle,
    PeriodStats,
)
from app.services.insights.narrator import (
    NarrationContractError,
    narrate_briefing,
)
from app.services.llm import GroqLLMClient

BASE = "https://api.groq.com/openai/v1"
ENDPOINT = f"{BASE}/chat/completions"


def _empty_period(label: str, start: str, end: str) -> PeriodStats:
    return PeriodStats(
        label=label,
        start_date=start,
        end_date=end,
        net_spend=0.0,
        txn_count=0,
        by_category=[],
        by_type={},
        by_payment_method={},
        top_merchants=[],
    )


def _minimal_bundle() -> MonthlyStatsBundle:
    return MonthlyStatsBundle(
        month="2026-04",
        generated_at="2026-05-01T00:00:00+05:30",
        owner_id="test-owner",
        currency="INR",
        current_month=_empty_period("2026-04", "2026-04-01", "2026-05-01"),
        previous_month=_empty_period("2026-03", "2026-03-01", "2026-04-01"),
        trailing_3m=_empty_period("trailing_3m", "2026-01-01", "2026-04-01"),
        trailing_12m=_empty_period("trailing_12m", "2025-04-01", "2026-04-01"),
        category_deltas_vs_prev=[],
        category_deltas_vs_3m_avg=[],
        budget_utilisation=[],
        goals=[],
        net_cashflow=0.0,
        top_n_largest_txns=[],
        investment_total_current=0.0,
        investment_total_prev_month=0.0,
        investment_delta_pct=None,
    )


def _groq_response(content: dict | str) -> dict:
    body = content if isinstance(content, str) else json.dumps(content)
    return {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": body}}],
    }


@pytest.mark.asyncio
@respx.mock
async def test_narration_rejects_digits() -> None:
    """A digit in any narrative field MUST raise after the retry budget."""
    bad_with_digit = {
        "headline": "You spent 12345 rupees this month.",
        "tone": "neutral",
        "sections": [],
    }
    # Both the first attempt and the repair retry contain digits.
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response(bad_with_digit)),
            httpx.Response(200, json=_groq_response(bad_with_digit)),
        ]
    )

    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)
    with pytest.raises(NarrationContractError) as excinfo:
        await narrate_briefing(_minimal_bundle(), llm=client, max_retries=1)

    assert any(v.startswith("digit_in_field:") for v in excinfo.value.violations)


@pytest.mark.asyncio
@respx.mock
async def test_narration_rejects_unknown_stat_ref() -> None:
    """An unknown {{...}} key MUST raise after the retry budget."""
    bad_with_unknown_ref = {
        "headline": "Your spend was {{nonexistent_key}} this month.",
        "tone": "neutral",
        "sections": [],
    }
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response(bad_with_unknown_ref)),
            httpx.Response(200, json=_groq_response(bad_with_unknown_ref)),
        ]
    )

    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)
    with pytest.raises(NarrationContractError) as excinfo:
        await narrate_briefing(_minimal_bundle(), llm=client, max_retries=1)

    assert any(
        v == "unknown_stat_ref:nonexistent_key" for v in excinfo.value.violations
    )
