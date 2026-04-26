"""Tests for GroqLLMClient + get_llm_client dispatch."""
from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import date

import httpx
import pytest
import respx

from app import config as _cfg
from app.services.llm import (
    GroqLLMClient,
    LLMParseError,
    ParseContext,
    StubLLMClient,
    get_llm_client,
)

BASE = "https://api.groq.com/openai/v1"
ENDPOINT = f"{BASE}/chat/completions"


@pytest.fixture
def ctx() -> ParseContext:
    return ParseContext(
        today=date(2026, 4, 23),
        currency="INR",
        uniques={"vendors": {"zomato": {"category": "Food & Drinks", "type": "Want"}}},
        last_known_balances={"cash": 1000.0, "online": 50000.0},
    )


def _groq_response(content: dict | str) -> dict:
    body = content if isinstance(content, str) else json.dumps(content)
    return {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": body}}],
    }


@pytest.fixture
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _cfg.get_settings.cache_clear()
    yield
    _cfg.get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_returns_parsed_expense(ctx: ParseContext) -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_groq_response(
                {
                    "action": "expense",
                    "date": "2026-04-23",
                    "expense_name": "Zomato",
                    "type_category": "Want, Food & Drinks",
                    "payment_method": "paid",
                    "paid_for_someone": False,
                    "paid_by_someone": False,
                    "person_name": None,
                    "amount": 250.0,
                    "atm_amount": None,
                    "needs_clarification": False,
                    "question": None,
                    "confidence": 0.94,
                }
            ),
        )
    )
    client = GroqLLMClient(api_key="k", model="llama-3.3-70b-versatile", base_url=BASE)
    parsed = await client.parse_expense("spent 250 at zomato with upi", ctx)
    assert parsed.action == "expense"
    assert parsed.amount == 250.0
    assert parsed.type_category == "Want, Food & Drinks"
    assert route.called

    # Verify request body shape.
    req = route.calls[0].request
    body = json.loads(req.content)
    assert body["model"] == "llama-3.3-70b-versatile"
    assert body["response_format"] == {"type": "json_object"}
    assert body["temperature"] == 0.1
    assert body["messages"][0]["role"] == "system"
    assert body["messages"][1]["role"] == "user"
    user_payload = json.loads(body["messages"][1]["content"])
    assert user_payload["transcript"] == "spent 250 at zomato with upi"
    assert user_payload["today"] == "2026-04-23"
    assert user_payload["currency"] == "INR"
    assert "uniques" in user_payload
    assert "last_known_balances" in user_payload


@pytest.mark.asyncio
@respx.mock
async def test_malformed_then_retry_succeeds(ctx: ParseContext) -> None:
    valid = _groq_response(
        {
            "action": "atm_transfer",
            "date": "2026-04-23",
            "atm_amount": 2000.0,
            "needs_clarification": False,
            "confidence": 0.9,
        }
    )
    responses = [
        httpx.Response(200, json=_groq_response("not-json{{")),
        httpx.Response(200, json=valid),
    ]
    route = respx.post(ENDPOINT).mock(side_effect=responses)

    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)
    parsed = await client.parse_expense("withdrew 2000 from ATM", ctx)
    assert parsed.action == "atm_transfer"
    assert parsed.atm_amount == 2000.0
    assert route.call_count == 2

    # Second call should include the repair message.
    second = json.loads(route.calls[1].request.content)
    last = second["messages"][-1]["content"]
    assert "failed validation" in last


@pytest.mark.asyncio
@respx.mock
async def test_two_failures_raise_parse_error(ctx: ParseContext) -> None:
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response("not-json")),
            httpx.Response(200, json=_groq_response("still-not-json")),
        ]
    )
    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)
    with pytest.raises(LLMParseError) as excinfo:
        await client.parse_expense("mumble", ctx)
    assert excinfo.value.transcript == "mumble"
    assert "still-not-json" in excinfo.value.raw


@pytest.mark.asyncio
@respx.mock
async def test_atm_branch_transcript(ctx: ParseContext) -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_groq_response(
                {
                    "action": "atm_transfer",
                    "date": "2026-04-23",
                    "atm_amount": 2000.0,
                    "needs_clarification": False,
                    "confidence": 0.99,
                }
            ),
        )
    )
    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)
    parsed = await client.parse_expense("withdrew 2000 from ATM", ctx)
    assert parsed.action == "atm_transfer"
    assert parsed.atm_amount == 2000.0


def test_get_llm_client_stub_when_no_key(
    monkeypatch: pytest.MonkeyPatch, _fresh_settings: None
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "")
    _cfg.get_settings.cache_clear()
    assert isinstance(get_llm_client(), StubLLMClient)


def test_get_llm_client_real_when_key_set(
    monkeypatch: pytest.MonkeyPatch, _fresh_settings: None
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    _cfg.get_settings.cache_clear()
    client = get_llm_client()
    assert isinstance(client, GroqLLMClient)
