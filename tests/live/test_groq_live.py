"""Live Groq smoke test — gated behind `--live`.

Run with: pytest --live tests/live/test_groq_live.py
Requires GROQ_API_KEY in the env (loaded from .env via pydantic-settings).
"""
from __future__ import annotations

import os
from datetime import date

import pytest

from app.services.llm import GroqLLMClient, ParseContext


@pytest.mark.live
@pytest.mark.asyncio
async def test_real_groq_parses_samosa_transcript() -> None:
    api_key = os.environ.get("GROQ_API_KEY", "")
    if not api_key:
        pytest.skip("GROQ_API_KEY not set")

    client = GroqLLMClient(
        api_key=api_key,
        model=os.environ.get("GROQ_MODEL", "llama-3.3-70b-versatile"),
        base_url=os.environ.get("GROQ_BASE_URL", "https://api.groq.com/openai/v1"),
    )
    ctx = ParseContext(
        today=date.today(),
        currency="INR",
        uniques={},
        last_known_balances={"cash": 1000.0, "online": 50000.0},
    )
    parsed = await client.parse_expense("paid 150 for samosas", ctx)
    assert parsed.action in ("expense", "clarify")
    if parsed.action == "expense":
        assert parsed.amount == 150.0
