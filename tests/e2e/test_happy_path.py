"""End-to-end happy path via FastAPI TestClient."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_health_and_full_flow(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # health
    r = client.get("/health")
    assert r.status_code == 200
    assert r.json()["status"] == "ok"

    # seed balance
    r = client.post(
        "/api/balances", json={"cash_balance": 1000.0, "online_balance": 50000.0}
    )
    assert r.status_code == 201

    # current balance
    r = client.get("/api/balances/current")
    assert r.status_code == 200
    assert r.json() == {"cash_balance": 1000.0, "online_balance": 50000.0}

    # create expense
    payload = {
        "date": "2026-04-23",
        "expense_name": "Zomato",
        "type_category": "Want, Food & Drinks",
        "payment_method": "paid",
        "amount": 450.0,
    }
    r = client.post("/api/expenses", json=payload)
    assert r.status_code == 201
    body = r.json()
    assert body["online_balance_after"] == 50000.0 - 450.0

    # list expenses
    r = client.get("/api/expenses")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1

    # totals
    r = client.get("/api/reports/totals", params={"scope": "daily", "day": "2026-04-23"})
    assert r.status_code == 200
    assert r.json()["total"] == 450.0

    # settings
    r = client.get("/api/settings")
    assert r.status_code == 200
    assert r.json()["timezone"] == "Asia/Kolkata"

    # patch timezone
    r = client.patch("/api/settings", json={"timezone": "America/New_York"})
    assert r.status_code == 200
    assert r.json()["tz_changed"] is True

    # reject invalid timezone
    r = client.patch("/api/settings", json={"timezone": "Atlantis/Lost"})
    assert r.status_code in (400, 422)
