"""E2E: /api/charts list + detail + refresh + 404 + bad registry = 422."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _seed_expenses(client: TestClient) -> None:
    client.post("/api/balances", json={"cash_balance": 1000.0, "online_balance": 50000.0})
    for payload in [
        {"date": "2026-04-10", "expense_name": "Zomato", "type_category": "Want, Food & Drinks", "payment_method": "paid", "amount": 450.0},
        {"date": "2026-04-11", "expense_name": "HPCL",   "type_category": "Need, Travel",        "payment_method": "paid", "amount": 800.0},
        {"date": "2026-04-12", "expense_name": "SIP",    "type_category": "Investment, Miscellaneous", "payment_method": "paid", "amount": 5000.0},
    ]:
        r = client.post("/api/expenses", json=payload)
        assert r.status_code == 201, r.text


def test_list_contains_six_charts(tmp_workspace: Path) -> None:
    client = TestClient(create_app())
    r = client.get("/api/charts")
    assert r.status_code == 200
    body = r.json()
    ids = {c["id"] for c in body["charts"]}
    assert {"cumulative_types_pie", "monthly_stack", "category_donut", "goal_progress", "daily_spend_line", "top_vendors"} <= ids


def test_cumulative_types_pie_payload(tmp_workspace: Path) -> None:
    client = TestClient(create_app())
    _seed_expenses(client)
    r = client.get("/api/charts/cumulative_types_pie")
    assert r.status_code == 200
    body = r.json()
    assert body["type"] == "pie"
    assert set(body["labels"]) == {"Need", "Want", "Investment"}
    d = dict(zip(body["labels"], body["datasets"][0]["data"], strict=False))
    assert d["Want"] == 450.0
    assert d["Need"] == 800.0
    assert d["Investment"] == 5000.0


def test_unknown_chart_returns_404(tmp_workspace: Path) -> None:
    client = TestClient(create_app())
    r = client.get("/api/charts/does_not_exist")
    assert r.status_code == 404


def test_refresh_reloads_registry(tmp_workspace: Path) -> None:
    client = TestClient(create_app())
    # Append a new chart to the YAML on disk
    charts_path = tmp_workspace / "data" / "meta" / "charts.yaml"
    text = charts_path.read_text(encoding="utf-8")
    text += """
  - id: brand_new_chart
    title: "Brand New"
    source: expenses
    type: pie
    group_by: type
"""
    charts_path.write_text(text, encoding="utf-8")

    r = client.post("/api/charts/refresh")
    assert r.status_code == 200

    r = client.get("/api/charts")
    ids = {c["id"] for c in r.json()["charts"]}
    assert "brand_new_chart" in ids


def test_invalid_registry_returns_422(tmp_workspace: Path) -> None:
    client = TestClient(create_app())
    charts_path = tmp_workspace / "data" / "meta" / "charts.yaml"
    charts_path.write_text(
        """
version: 1
charts:
  - id: bad
    title: Bad
    source: expenses
    type: pie
    group_by: type
    filter: "__import__('os')"
""",
        encoding="utf-8",
    )
    r = client.post("/api/charts/refresh")
    assert r.status_code == 422
    r = client.get("/api/charts")
    assert r.status_code == 422


def test_empty_chart_returns_meta_empty(tmp_workspace: Path) -> None:
    client = TestClient(create_app())
    # No expenses seeded -> pie should be empty
    r = client.get("/api/charts/cumulative_types_pie")
    assert r.status_code == 200
    body = r.json()
    assert body["meta"].get("empty") is True
    assert body["labels"] == []
    assert body["datasets"] == []
