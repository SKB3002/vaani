"""Fallback-retry behaviour for ``GroqLLMClient.chat_json``.

Covers the contract:
- 429 / 5xx / transport errors on the primary trigger one retry on the
  fallback model.
- 4xx other than 429 are real client errors and never retry.
- An explicit ``model=fallback`` call must NOT recurse onto itself.
"""
from __future__ import annotations

import json

import httpx
import pytest
import respx

from app.services.llm import GroqLLMClient, LLMTransportError

BASE = "https://api.groq.com/openai/v1"
ENDPOINT = f"{BASE}/chat/completions"


def _groq_response(content: str) -> dict:
    return {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


def _model_router(
    primary_model: str,
    primary_response: httpx.Response,
    fallback_model: str,
    fallback_response: httpx.Response,
) -> respx.Route:
    """Mock the chat-completions endpoint with branching on the request body's model.

    respx's URL-only matching can't distinguish primary from fallback calls
    because both go to the same endpoint, so we inspect the JSON body.
    """

    def _side_effect(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        model = body.get("model")
        if model == primary_model:
            return primary_response
        if model == fallback_model:
            return fallback_response
        return httpx.Response(599, text=f"unexpected model: {model!r}")

    return respx.post(ENDPOINT).mock(side_effect=_side_effect)


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_falls_back_on_429() -> None:
    fallback_payload = json.dumps({"ok": True})
    route = _model_router(
        primary_model="primary-model",
        primary_response=httpx.Response(429, text="rate limited"),
        fallback_model="fallback-model",
        fallback_response=httpx.Response(200, json=_groq_response(fallback_payload)),
    )
    client = GroqLLMClient(
        api_key="k",
        model="primary-model",
        base_url=BASE,
        fallback_model="fallback-model",
    )

    result = await client.chat_json(
        system="s", user="u", model="primary-model"
    )

    assert result == fallback_payload
    assert route.call_count == 2
    bodies = [json.loads(call.request.content) for call in route.calls]
    assert bodies[0]["model"] == "primary-model"
    assert bodies[1]["model"] == "fallback-model"


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_no_fallback_on_4xx_other_than_429() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(401, text="unauthorized")
    )
    client = GroqLLMClient(
        api_key="k",
        model="primary-model",
        base_url=BASE,
        fallback_model="fallback-model",
    )

    with pytest.raises(LLMTransportError) as excinfo:
        await client.chat_json(system="s", user="u", model="primary-model")

    assert excinfo.value.status == 401
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "primary-model"


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_no_recursion_on_explicit_fallback_model() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(429, text="rate limited")
    )
    client = GroqLLMClient(
        api_key="k",
        model="A",
        base_url=BASE,
        fallback_model="B",
    )

    with pytest.raises(LLMTransportError) as excinfo:
        await client.chat_json(system="s", user="u", model="B")

    assert excinfo.value.status == 429
    # No recursion: exactly one call, and it was on the fallback model.
    assert route.call_count == 1
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "B"
