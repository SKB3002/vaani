"""Stub LLM client conforms to the protocol and raises NotImplementedError."""
from __future__ import annotations

from collections.abc import Iterator
from datetime import date

import pytest

from app import config as _cfg
from app.services.llm import LLMClient, ParseContext, StubLLMClient, get_llm_client


@pytest.fixture(autouse=True)
def _clear_groq_key(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setenv("GROQ_API_KEY", "")
    _cfg.get_settings.cache_clear()
    yield
    _cfg.get_settings.cache_clear()


def test_stub_conforms_to_protocol() -> None:
    client = get_llm_client()
    assert isinstance(client, LLMClient)  # runtime_checkable Protocol


def test_stub_is_stub() -> None:
    assert isinstance(get_llm_client(), StubLLMClient)


@pytest.mark.asyncio
async def test_stub_raises_not_implemented() -> None:
    client = get_llm_client()
    ctx = ParseContext(today=date(2026, 4, 23), currency="INR", uniques={}, last_known_balances={})
    with pytest.raises(NotImplementedError):
        await client.parse_expense("spent 100 at zomato", ctx)
