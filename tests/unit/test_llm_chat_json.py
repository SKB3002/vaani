"""Tests for GroqLLMClient.chat_json — generic analysis-model surface."""
from __future__ import annotations

import json
from collections.abc import Iterator

import httpx
import pytest
import respx

from app import config as _cfg
from app.services.llm import GroqLLMClient, LLMTransportError

BASE = "https://api.groq.com/openai/v1"
ENDPOINT = f"{BASE}/chat/completions"


def _groq_response(content: str) -> dict:
    return {
        "id": "x",
        "choices": [{"index": 0, "message": {"role": "assistant", "content": content}}],
    }


@pytest.fixture
def _fresh_settings(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _cfg.get_settings.cache_clear()
    yield
    _cfg.get_settings.cache_clear()


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_uses_analysis_model_by_default(
    monkeypatch: pytest.MonkeyPatch, _fresh_settings: None
) -> None:
    monkeypatch.setenv("GROQ_ANALYSIS_MODEL", "openai/gpt-oss-120b")
    _cfg.get_settings.cache_clear()

    payload = json.dumps({"summary": "ok"})
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_groq_response(payload))
    )
    client = GroqLLMClient(api_key="k", model="llama-3.3-70b-versatile", base_url=BASE)

    result = await client.chat_json(system="be concise", user="summarize this")

    assert result == payload
    assert route.called
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "openai/gpt-oss-120b"
    assert body["messages"][0] == {"role": "system", "content": "be concise"}
    assert body["messages"][1] == {"role": "user", "content": "summarize this"}
    assert "max_tokens" not in body


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_honors_caller_model_override() -> None:
    payload = json.dumps({"x": 1})
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_groq_response(payload))
    )
    client = GroqLLMClient(api_key="k", model="primary-model", base_url=BASE)

    result = await client.chat_json(
        system="s", user="u", model="custom/override-model"
    )

    assert result == payload
    body = json.loads(route.calls[0].request.content)
    assert body["model"] == "custom/override-model"


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_raises_transport_error_on_500() -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(500, text="boom"))
    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)

    with pytest.raises(LLMTransportError) as excinfo:
        await client.chat_json(system="s", user="u", model="any")

    assert excinfo.value.status == 500


@pytest.mark.asyncio
@respx.mock
async def test_chat_json_includes_max_tokens_when_provided() -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_groq_response("{}"))
    )
    client = GroqLLMClient(api_key="k", model="m", base_url=BASE)

    await client.chat_json(system="s", user="u", model="any", max_tokens=512)
    body_with = json.loads(route.calls[0].request.content)
    assert body_with["max_tokens"] == 512

    await client.chat_json(system="s", user="u", model="any")
    body_without = json.loads(route.calls[1].request.content)
    assert "max_tokens" not in body_without
