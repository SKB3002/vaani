"""Analysis-model LLM client for the AI Insights subsystem.

The voice path (`parse_expense`) runs against ``settings.GROQ_MODEL`` (default
``llama-3.3-70b-versatile``). The insights path (briefing narrator + chat
planner/narrator) runs against ``settings.GROQ_ANALYSIS_MODEL``
(default ``openai/gpt-oss-120b``) and is independent.

`AnalysisLLMClient` composes (does NOT inherit from) `GroqLLMClient`. It
reuses `GroqLLMClient._post` for HTTP transport so there is exactly one
implementation of the chat-completion call shape across the codebase.

Availability fallback:
- 429 / 5xx / transport error from the analysis model → retry once on the
  configured fallback model (default: ``settings.GROQ_FALLBACK_MODEL``).
- JSON-validation failure → retry once on the SAME model with the
  validation error appended to the conversation. This mirrors the
  `parse_expense` repair-retry contract: bad output is a prompt issue,
  not an availability issue, so swapping models would not help.
"""
from __future__ import annotations

import json
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from app.config import get_settings
from app.services.llm import (
    GroqLLMClient,
    LLMParseError,
    LLMTransportError,
    _is_availability_error,
)

_T = TypeVar("_T", bound=BaseModel)


class AnalysisLLMClient:
    """Generic chat-completion surface for the insights analysis model.

    Public API:
        - ``complete(messages, *, response_format=None) -> str``
        - ``complete_json(messages, *, schema) -> BaseModel``

    Both use ``settings.GROQ_ANALYSIS_MODEL`` as the primary model and fall
    back to ``fallback_model`` (typically ``settings.GROQ_FALLBACK_MODEL``)
    on transport / 429 / 5xx errors.
    """

    def __init__(
        self,
        *,
        groq: GroqLLMClient,
        model: str,
        fallback_model: str | None = None,
    ) -> None:
        self._groq = groq
        self._model = model
        self._fallback_model = fallback_model

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        response_format: dict[str, Any] | None = None,
        max_tokens: int | None = None,
    ) -> str:
        """Run a chat completion against the analysis model.

        Returns the raw assistant content. When ``response_format`` is
        ``{"type": "json_object"}``, the returned string is guaranteed to be
        valid JSON by the provider; the caller is responsible for parsing.
        On 429 / 5xx / transport failures, retries once on the fallback
        model if one is configured.
        """
        try:
            return await self._post(
                messages,
                model=self._model,
                response_format=response_format,
                max_tokens=max_tokens,
            )
        except LLMTransportError as exc:
            if not self._fallback_model or not _is_availability_error(exc):
                raise
            return await self._post(
                messages,
                model=self._fallback_model,
                response_format=response_format,
                max_tokens=max_tokens,
            )

    async def complete_json(
        self,
        messages: list[dict[str, str]],
        *,
        schema: type[_T],
        max_tokens: int | None = None,
    ) -> _T:
        """Run a JSON chat completion and validate against a Pydantic schema.

        Forces ``response_format={"type": "json_object"}``. On validation
        failure, retries once on the SAME model with the validation error
        appended to the conversation (mirrors `parse_expense` repair-retry).
        Availability errors trigger fallback-model retry as in `complete`.
        Raises `LLMParseError` if validation still fails after the retry.
        """
        json_format: dict[str, Any] = {"type": "json_object"}
        # First attempt — uses primary, with availability fallback inside complete().
        raw = await self.complete(
            messages, response_format=json_format, max_tokens=max_tokens
        )
        parsed, err = _try_validate(raw, schema)
        if parsed is not None:
            return parsed

        # Repair retry on the SAME pathway (primary→fallback) — bad JSON is
        # a prompt issue, not availability, so we keep the model selection
        # as-is and just append the validation error to the conversation.
        repair_messages: list[dict[str, str]] = [
            *messages,
            {"role": "assistant", "content": raw},
            {
                "role": "user",
                "content": (
                    f"Your previous output failed validation: {err}. "
                    "Return valid JSON only matching the requested schema."
                ),
            },
        ]
        raw2 = await self.complete(
            repair_messages, response_format=json_format, max_tokens=max_tokens
        )
        parsed2, err2 = _try_validate(raw2, schema)
        if parsed2 is not None:
            return parsed2

        raise LLMParseError(
            f"failed to validate analysis-model output after retry: {err2}",
            raw=raw2,
            transcript="",
        )

    async def _post(
        self,
        messages: list[dict[str, str]],
        *,
        model: str,
        response_format: dict[str, Any] | None,
        max_tokens: int | None,
    ) -> str:
        # Composition: reuse the GroqLLMClient HTTP machinery — no duplicated
        # transport code. `_post` accepts `response_format=None` (no JSON
        # enforcement) and any model override.
        return await self._groq._post(
            messages,
            model=model,
            max_tokens=max_tokens,
            response_format=response_format,
        )


def _try_validate(
    raw: str, schema: type[_T]
) -> tuple[_T | None, str | None]:
    try:
        obj = json.loads(raw)
    except json.JSONDecodeError as exc:
        return None, f"JSONDecodeError: {exc}"
    try:
        return schema.model_validate(obj), None
    except ValidationError as exc:
        return None, f"ValidationError: {exc}"


def get_analysis_llm_client() -> AnalysisLLMClient:
    """Construct an `AnalysisLLMClient` from current settings.

    Wires the underlying `GroqLLMClient` with the analysis model as primary
    and `GROQ_FALLBACK_MODEL` as the availability fallback. Returns an
    instance even when `GROQ_API_KEY` is empty — calls will fail with
    `LLMTransportError` at request time, which the caller (e.g. narrator)
    can degrade gracefully.
    """
    settings = get_settings()
    groq = GroqLLMClient(
        api_key=settings.GROQ_API_KEY,
        model=settings.GROQ_ANALYSIS_MODEL,
        base_url=settings.GROQ_BASE_URL,
    )
    return AnalysisLLMClient(
        groq=groq,
        model=settings.GROQ_ANALYSIS_MODEL,
        fallback_model=settings.GROQ_FALLBACK_MODEL or None,
    )
