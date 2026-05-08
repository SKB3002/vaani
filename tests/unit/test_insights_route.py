"""End-to-end tests for the AI Insights monthly briefing route.

The route under test is ``GET /api/insights/monthly``. All Groq calls are
mocked at the HTTP layer with respx — no real network traffic, no real LLM.
Each test seeds expense fixtures via ``LedgerWriter.append`` so the
deterministic stats bundle is non-empty (or skipped when we want the
empty-month short-circuit).
"""
from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import ulid
from fastapi.testclient import TestClient

from app import config as _cfg
from app import deps as _deps
from app.main import create_app
from app.services.ledger import LedgerWriter

BASE = "https://api.groq.com/openai/v1"
ENDPOINT = f"{BASE}/chat/completions"


# ---------------------------------------------------------------------------
# Fixture helpers
# ---------------------------------------------------------------------------


def _seed_expense(
    ledger: LedgerWriter,
    *,
    date: str = "2026-04-15",
    amount: float = 250.0,
    name: str = "dining",
    category: str = "Need, Food & Drinks",
    payment_method: str = "paid_cash",
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "id": str(ulid.new()),
        "date": date,
        "created_at": datetime.now(UTC).isoformat(),
        "expense_name": name,
        "type_category": category,
        "payment_method": payment_method,
        "paid_for_someone": False,
        "paid_by_someone": False,
        "person_name": "",
        "amount": amount,
        "cash_balance_after": 0.0,
        "online_balance_after": 0.0,
        "source": "test",
        "raw_transcript": "",
        "notes": "",
        "import_batch_id": "",
        "custom_tag": "",
        "paid_for_method": "",
        "adjustment_type": "",
    }
    ledger.append("expenses", row)
    return row


def _groq_response(content: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "x",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": json.dumps(content)}}
        ],
    }


def _valid_narration(headline: str = "A balanced month for spending") -> dict[str, Any]:
    return {
        "headline": headline,
        "tone": "neutral",
        "sections": [],
    }


@pytest.fixture
def client(tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch) -> TestClient:
    # Default to a configured key — individual tests override when needed.
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_ANALYSIS_MODEL", "openai/gpt-oss-120b")
    _cfg.get_settings.cache_clear()
    _deps.get_insights_cache.cache_clear()
    return TestClient(create_app())


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_invalid_month_returns_422(client: TestClient) -> None:
    resp = client.get("/api/insights/monthly", params={"month": "2026-13"})
    assert resp.status_code == 422


@respx.mock
def test_empty_month_short_circuits_no_llm_call(client: TestClient) -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_groq_response(_valid_narration()))
    )
    resp = client.get("/api/insights/monthly", params={"month": "2026-04"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["cache_hit"] is False
    assert body["narration"] is None
    assert body["reason"] == "empty_month"
    assert route.call_count == 0


def test_groq_not_configured_returns_null_narration(
    tmp_workspace: Path,
    ledger: LedgerWriter,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "")
    _cfg.get_settings.cache_clear()
    _deps.get_insights_cache.cache_clear()

    _seed_expense(ledger)
    with TestClient(create_app()) as c:
        resp = c.get("/api/insights/monthly", params={"month": "2026-04"})

    assert resp.status_code == 200
    body = resp.json()
    assert body["narration"] is None
    assert body["reason"] == "groq_not_configured"
    assert body["stats_bundle"]["current_month"]["txn_count"] == 1


@respx.mock
def test_cache_hit_skips_llm_call(client: TestClient, ledger: LedgerWriter) -> None:
    route = respx.post(ENDPOINT).mock(
        return_value=httpx.Response(200, json=_groq_response(_valid_narration()))
    )
    _seed_expense(ledger)

    first = client.get("/api/insights/monthly", params={"month": "2026-04"})
    assert first.status_code == 200
    body1 = first.json()
    assert body1["cache_hit"] is False
    assert body1["narration"] is not None
    assert body1["narration"]["headline"] == "A balanced month for spending"
    assert route.call_count == 1

    second = client.get("/api/insights/monthly", params={"month": "2026-04"})
    assert second.status_code == 200
    body2 = second.json()
    assert body2["cache_hit"] is True
    assert body2["narration"] == body1["narration"]
    assert route.call_count == 1  # No new LLM call.


@respx.mock
def test_refresh_param_busts_cache(client: TestClient, ledger: LedgerWriter) -> None:
    route = respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response(_valid_narration("First headline"))),
            httpx.Response(200, json=_groq_response(_valid_narration("Second headline"))),
        ]
    )
    _seed_expense(ledger)

    first = client.get("/api/insights/monthly", params={"month": "2026-04"})
    assert first.json()["narration"]["headline"] == "First headline"
    assert route.call_count == 1

    refreshed = client.get(
        "/api/insights/monthly", params={"month": "2026-04", "refresh": "true"}
    )
    body = refreshed.json()
    assert body["cache_hit"] is False
    assert body["narration"]["headline"] == "Second headline"
    assert route.call_count == 2


@respx.mock
def test_observer_invalidates_cache_on_expense_write(
    tmp_workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "test-key")
    monkeypatch.setenv("GROQ_ANALYSIS_MODEL", "openai/gpt-oss-120b")
    _cfg.get_settings.cache_clear()
    _deps.get_insights_cache.cache_clear()

    route = respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response(_valid_narration("First"))),
            httpx.Response(200, json=_groq_response(_valid_narration("Second"))),
        ]
    )

    # Use TestClient as a context manager so the lifespan runs and the
    # invalidator observer is wired up against the singleton ledger.
    with TestClient(create_app()) as c:
        ledger = _deps.get_ledger()
        _seed_expense(ledger)

        first = c.get("/api/insights/monthly", params={"month": "2026-04"})
        assert first.json()["cache_hit"] is False
        assert route.call_count == 1

        # Second call without writes -> cache hit, no new LLM call.
        warm = c.get("/api/insights/monthly", params={"month": "2026-04"})
        assert warm.json()["cache_hit"] is True
        assert route.call_count == 1

        # Append a new expense in April. The on_change observer should fire
        # the invalidator and wipe the April briefing.
        _seed_expense(ledger, date="2026-04-20", amount=99.0, name="snack")

        third = c.get("/api/insights/monthly", params={"month": "2026-04"})
        body = third.json()
        assert body["cache_hit"] is False
        assert body["narration"]["headline"] == "Second"
        assert route.call_count == 2


@respx.mock
def test_groq_unreachable_returns_null_narration(
    client: TestClient, ledger: LedgerWriter
) -> None:
    respx.post(ENDPOINT).mock(return_value=httpx.Response(503, text="upstream down"))
    _seed_expense(ledger)

    resp = client.get("/api/insights/monthly", params={"month": "2026-04"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["narration"] is None
    assert body["reason"] == "groq_unreachable"
    assert body["stats_bundle"]["current_month"]["txn_count"] == 1


@respx.mock
def test_contract_violation_falls_through_to_null(
    client: TestClient, ledger: LedgerWriter
) -> None:
    bad = {
        "headline": "Spent 250 on dining",  # digit -> contract violation
        "tone": "neutral",
        "sections": [],
    }
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response(bad)),
            httpx.Response(200, json=_groq_response(bad)),
        ]
    )
    _seed_expense(ledger)

    resp = client.get("/api/insights/monthly", params={"month": "2026-04"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["narration"] is None
    assert body["reason"] == "contract_violation"
