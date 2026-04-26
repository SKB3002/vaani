"""E2E: POST /api/expenses with payment_method='adjusted' bypasses expense row."""
from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app import config as _cfg
from app import deps as _deps
from app.main import create_app


@pytest.fixture
def client(tmp_workspace: Path) -> Iterator[TestClient]:
    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()

    app = create_app()
    with TestClient(app) as tc:
        tc.post("/api/balances", json={"cash_balance": 1000.0, "online_balance": 50000.0})
        yield tc

    _cfg.get_settings.cache_clear()
    _deps.get_ledger.cache_clear()
    _deps.get_balance_service.cache_clear()


def test_adjusted_post_does_not_write_expense_row(client: TestClient) -> None:
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-04-23",
            "expense_name": "Balance transfer",
            "type_category": "Need, Miscellaneous",
            "payment_method": "adjusted",
            "adjustment_type": "cash_to_online",
            "amount": 500.0,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["type"] == "adjustment"
    assert body["adjustment_type"] == "cash_to_online"
    assert body["balances"]["cash_balance"] == 500.0
    assert body["balances"]["online_balance"] == 50500.0

    # No expense row written
    rows = client.get("/api/expenses").json()
    assert rows == []

    # Balance snapshot reflects the adjustment
    cur = client.get("/api/balances/current").json()
    assert cur["cash_balance"] == 500.0
    assert cur["online_balance"] == 50500.0


def test_adjusted_missing_adjustment_type_defaults_to_cash_to_online(
    client: TestClient,
) -> None:
    # Grid doesn't surface adjustment_type — backend defaults to cash_to_online.
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-04-23",
            "expense_name": "X",
            "type_category": "Need, Miscellaneous",
            "payment_method": "adjusted",
            "amount": 500.0,
        },
    )
    assert r.status_code == 201
    body = r.json()
    assert body["type"] == "adjustment"
    assert body["adjustment_type"] == "cash_to_online"


def test_paid_for_roundtrip(client: TestClient) -> None:
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-04-23",
            "expense_name": "Lunch for Arjun",
            "type_category": "Want, Food & Drinks",
            "payment_method": "paid_for",
            "paid_for_method": "online",
            "paid_for_someone": True,
            "person_name": "Arjun",
            "amount": 400.0,
        },
    )
    assert r.status_code == 201, r.text
    body = r.json()
    assert body["payment_method"] == "paid_for"
    assert body["paid_for_method"] == "online"
    assert body["online_balance_after"] == 50000.0 - 400.0
