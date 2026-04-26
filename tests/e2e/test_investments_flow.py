"""E2E: add user column -> POST month -> PATCH -> GET summary with user column."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_investments_flow_with_user_column(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # 1. Register a user column for investments
    r = client.post(
        "/api/tables/investments/columns",
        json={"key": "crypto", "label": "Crypto", "dtype": "number"},
    )
    assert r.status_code == 201, r.text

    # 2. POST a month including the user column value
    r = client.post(
        "/api/investments",
        json={
            "month": "2026-04",
            "long_term": 5000.0,
            "emergency_fund": 2000.0,
            "crypto": 1500.0,
        },
    )
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 8500.0  # 5000 + 2000 + 1500

    # 3. POST another month with just built-ins
    r = client.post(
        "/api/investments",
        json={"month": "2026-03", "long_term": 4000.0, "crypto": 1000.0},
    )
    assert r.status_code == 201
    assert r.json()["total"] == 5000.0

    # 4. PATCH: add fixed_deposits to 2026-04, total recomputes
    r = client.patch(
        "/api/investments/2026-04", json={"fixed_deposits": 1000.0}
    )
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 9500.0

    # 5. GET list — month desc
    r = client.get("/api/investments")
    assert r.status_code == 200
    rows = r.json()
    assert [row["month"] for row in rows] == ["2026-04", "2026-03"]

    # 6. GET summary — per-category annual totals include user column
    r = client.get("/api/investments/summary?year=2026")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["months"] == 2
    assert body["totals"]["long_term"] == 9000.0  # 5000 + 4000
    assert body["totals"]["crypto"] == 2500.0     # 1500 + 1000
    assert body["totals"]["fixed_deposits"] == 1000.0
    # grand total = sum of totals (excludes 'total' column itself)
    assert body["grand_total"] == 9000.0 + 2000.0 + 2500.0 + 1000.0


def test_investments_bad_month_format(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/investments", json={"month": "2026/04"})
    assert r.status_code == 422
    r = client.get("/api/investments/2026")
    assert r.status_code == 400
