"""E2E tests for the /api/expense/parse and /api/uniques/teach endpoints."""
from __future__ import annotations

import json
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from app import config as _cfg
from app import deps as _deps
from app.main import create_app

BASE = "https://api.groq.com/openai/v1"
ENDPOINT = f"{BASE}/chat/completions"


def _groq_response(content: dict | str) -> dict:
    body = content if isinstance(content, str) else json.dumps(content)
    return {"choices": [{"index": 0, "message": {"role": "assistant", "content": body}}]}


@pytest.fixture
def client(
    tmp_workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[TestClient]:
    monkeypatch.setenv("GROQ_API_KEY", "gsk_test")
    monkeypatch.setenv("GROQ_MODEL", "llama-3.3-70b-versatile")
    monkeypatch.setenv("GROQ_BASE_URL", BASE)
    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()

    app = create_app()
    with TestClient(app) as tc:
        # Seed balances so snapshots work.
        tc.post("/api/balances", json={"cash_balance": 1000.0, "online_balance": 50000.0})
        yield tc

    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()


@respx.mock
def test_parse_expense_happy_path(client: TestClient, tmp_workspace: Path) -> None:
    respx.post(ENDPOINT).mock(
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
                    "confidence": 0.95,
                }
            ),
        )
    )
    r = client.post(
        "/api/expense/parse", json={"transcript": "spent 250 at zomato with upi"}
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "inserted"
    assert body["row"]["amount"] == 250.0
    assert body["balances"]["online_balance"] == 50000.0 - 250.0

    rows = client.get("/api/expenses").json()
    assert len(rows) == 1
    assert rows[0]["source"] == "voice"


@respx.mock
def test_parse_atm_transfer(client: TestClient, tmp_workspace: Path) -> None:
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
    r = client.post(
        "/api/expense/parse", json={"transcript": "withdrew 2000 from ATM"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "atm_transfer"
    assert body["balances"]["cash_balance"] == 1000.0 + 2000.0
    assert body["balances"]["online_balance"] == 50000.0 - 2000.0

    # No expense row written.
    assert client.get("/api/expenses").json() == []


@respx.mock
def test_parse_clarify(client: TestClient) -> None:
    respx.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json=_groq_response(
                {
                    "action": "clarify",
                    "date": "2026-04-23",
                    "needs_clarification": True,
                    "question": "How much did you spend?",
                    "confidence": 0.3,
                }
            ),
        )
    )
    r = client.post("/api/expense/parse", json={"transcript": "bought something"})
    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "clarify"
    assert "how much" in body["question"].lower()


@respx.mock
def test_parse_malformed_twice_returns_422(client: TestClient) -> None:
    respx.post(ENDPOINT).mock(
        side_effect=[
            httpx.Response(200, json=_groq_response("not-json")),
            httpx.Response(200, json=_groq_response("still-not-json")),
        ]
    )
    r = client.post("/api/expense/parse", json={"transcript": "mumble"})
    assert r.status_code == 422
    detail = r.json()["detail"]
    assert detail["error"] == "llm_parse_failed"
    assert detail["raw_transcript"] == "mumble"
    assert "still-not-json" in detail["raw"]


def test_teach_uniques_updates_file(client: TestClient, tmp_workspace: Path) -> None:
    r = client.post(
        "/api/uniques/teach",
        json={
            "surface": "bros",
            "vendor": "Brotherhood Cafe",
            "type_category": "Want, Food & Drinks",
        },
    )
    assert r.status_code == 200
    data = r.json()
    assert "brotherhood cafe" in data["vendors"]
    assert data["vendors"]["brotherhood cafe"]["category"] == "Food & Drinks"
    assert data["aliases"]["bros"] == "Brotherhood Cafe"

    # File on disk matches.
    path = tmp_workspace / "data" / "uniques.json"
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk == data

    # GET /api/uniques returns same.
    assert client.get("/api/uniques").json() == data
