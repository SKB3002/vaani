"""LLM client interface + Stub + real Groq implementation.

Groq exposes an OpenAI-compatible Chat Completions API. We keep the Protocol
+ StubLLMClient so tests that want to bypass the network still work, and
swap in the real GroqLLMClient at runtime when GROQ_API_KEY is configured.
"""
from __future__ import annotations

import json
from datetime import date
from typing import Any, Protocol, runtime_checkable

import httpx
from pydantic import BaseModel, Field, ValidationError

from app.config import get_settings
from app.models.expense import ParsedExpense
from app.services.prompts.expense_parser import EXPENSE_PARSER_SYSTEM


class LLMError(Exception):
    """Base class for LLM-related errors."""


class LLMTransportError(LLMError):
    """Network/HTTP failure talking to the provider."""

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.status = status


class LLMParseError(LLMError):
    """Provider returned content that failed JSON/schema validation twice."""

    def __init__(self, message: str, *, raw: str, transcript: str) -> None:
        super().__init__(message)
        self.raw = raw
        self.transcript = transcript


class ParseContext(BaseModel):
    today: date
    currency: str = "INR"
    uniques: dict[str, Any] = Field(default_factory=dict)
    last_known_balances: dict[str, float] = Field(default_factory=dict)


@runtime_checkable
class LLMClient(Protocol):
    async def parse_expense(
        self, transcript: str, ctx: ParseContext
    ) -> ParsedExpense: ...


class StubLLMClient:
    """Placeholder used when no GROQ_API_KEY is configured."""

    async def parse_expense(
        self, transcript: str, ctx: ParseContext
    ) -> ParsedExpense:
        raise NotImplementedError("LLM client is wired in M2")


def _build_user_message(transcript: str, ctx: ParseContext) -> str:
    payload = {
        "transcript": transcript,
        "today": ctx.today.isoformat(),
        "currency": ctx.currency,
        "uniques": ctx.uniques,
        "last_known_balances": ctx.last_known_balances,
    }
    return json.dumps(payload, ensure_ascii=False)


class GroqLLMClient:
    """Real Groq client using httpx + OpenAI-compatible Chat Completions.

    Supports a fallback model that takes over on transport / 429 / 5xx errors
    from the primary. Bad-JSON retries stay on the same model (handled inside
    parse_expense) — those are prompt issues, not availability issues.
    """

    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str,
        *,
        fallback_model: str | None = None,
        timeout: float = 20.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self._api_key = api_key
        self._model = model
        self._fallback_model = fallback_model
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client = client  # test injection

    def _http(self) -> httpx.AsyncClient:
        if self._client is not None:
            return self._client
        return httpx.AsyncClient(
            base_url=self._base_url,
            headers={
                "Authorization": f"Bearer {self._api_key}",
                "Content-Type": "application/json",
            },
            timeout=self._timeout,
        )

    async def _post(
        self,
        messages: list[dict[str, str]],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        response_format: dict[str, Any] | None = None,
    ) -> str:
        body: dict[str, Any] = {
            "model": model or self._model,
            "response_format": response_format
            if response_format is not None
            else {"type": "json_object"},
            "temperature": 0.1,
            "messages": messages,
        }
        if max_tokens is not None:
            body["max_tokens"] = max_tokens
        client = self._http()
        own = self._client is None
        try:
            try:
                resp = await client.post("/chat/completions", json=body)
            except httpx.HTTPError as exc:
                raise LLMTransportError(f"groq transport error: {exc}") from exc
            if resp.status_code >= 400:
                raise LLMTransportError(
                    f"groq returned {resp.status_code}: {resp.text[:400]}",
                    status=resp.status_code,
                )
            data = resp.json()
        finally:
            if own:
                await client.aclose()
        try:
            return str(data["choices"][0]["message"]["content"])
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMTransportError(f"malformed groq response: {data!r}") from exc

    async def parse_expense(
        self, transcript: str, ctx: ParseContext
    ) -> ParsedExpense:
        try:
            return await self._parse_with_model(transcript, ctx, self._model)
        except LLMTransportError as exc:
            if not self._fallback_model or not _is_availability_error(exc):
                raise
            # Primary unavailable / rate-limited / 5xx — retry on fallback model.
            return await self._parse_with_model(transcript, ctx, self._fallback_model)

    async def chat_json(
        self,
        *,
        system: str,
        user: str,
        model: str | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Generic JSON chat completion against the analysis model.

        Returns the raw assistant content; the caller is responsible for JSON
        parsing and schema validation. When ``model`` is None, defaults to
        ``settings.GROQ_ANALYSIS_MODEL`` (NOT the primary expense-parser model).
        Errors propagate as ``LLMTransportError``; no fallback is attempted.
        """
        chosen_model = model or get_settings().GROQ_ANALYSIS_MODEL
        messages: list[dict[str, str]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        return await self._post(messages, model=chosen_model, max_tokens=max_tokens)

    async def _parse_with_model(
        self, transcript: str, ctx: ParseContext, model: str
    ) -> ParsedExpense:
        messages: list[dict[str, str]] = [
            {"role": "system", "content": EXPENSE_PARSER_SYSTEM},
            {"role": "user", "content": _build_user_message(transcript, ctx)},
        ]

        raw = await self._post(messages, model=model)
        parsed, err = _try_parse(raw)
        if parsed is not None:
            return parsed

        # One repair retry on the same model — bad JSON is a prompt issue.
        messages.append({"role": "assistant", "content": raw})
        messages.append(
            {
                "role": "user",
                "content": (
                    f"Your previous output failed validation: {err}. "
                    "Return valid JSON only matching schema."
                ),
            }
        )
        raw2 = await self._post(messages, model=model)
        parsed2, err2 = _try_parse(raw2)
        if parsed2 is not None:
            return parsed2

        raise LLMParseError(
            f"failed to parse LLM output after retry: {err2}",
            raw=raw2,
            transcript=transcript,
        )


def _is_availability_error(exc: LLMTransportError) -> bool:
    """True if the error indicates the model is unavailable (vs a real client bug).

    429 (rate limit), 5xx, and unknown-status transport failures (network) all
    qualify. 4xx other than 429 are real client errors — no fallback would help.
    """
    if exc.status is None:
        return True  # network / DNS / connect error
    if exc.status == 429:
        return True
    return exc.status >= 500


def _try_parse(raw: str) -> tuple[ParsedExpense | None, str | None]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"JSONDecodeError: {exc}"
    try:
        return ParsedExpense.model_validate(obj), None
    except ValidationError as exc:
        return None, f"ValidationError: {exc}"


def get_llm_client() -> LLMClient:
    """Pick a real Groq client when an API key is configured, else the stub."""
    settings = get_settings()
    if settings.GROQ_API_KEY:
        return GroqLLMClient(
            api_key=settings.GROQ_API_KEY,
            model=settings.GROQ_MODEL,
            base_url=settings.GROQ_BASE_URL,
            fallback_model=settings.GROQ_FALLBACK_MODEL or None,
        )
    return StubLLMClient()
