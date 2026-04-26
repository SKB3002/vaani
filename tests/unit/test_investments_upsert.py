"""Unit: POST /api/investments with the same month twice = update, not insert."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_upsert_by_month(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    r = client.post(
        "/api/investments",
        json={"month": "2026-04", "long_term": 1000.0, "emergency_fund": 500.0},
    )
    assert r.status_code == 201, r.text
    assert r.json()["total"] == 1500.0

    # Second POST same month — should update, not duplicate
    r = client.post(
        "/api/investments",
        json={"month": "2026-04", "long_term": 2000.0, "fixed_deposits": 3000.0},
    )
    assert r.status_code == 201, r.text
    # New values replace old; total = 2000 + 3000 (emergency_fund not in body → None)
    assert r.json()["total"] == 5000.0

    r = client.get("/api/investments")
    assert r.status_code == 200
    rows = r.json()
    months = [row["month"] for row in rows]
    assert months.count("2026-04") == 1


def test_patch_recomputes_total(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    client.post(
        "/api/investments",
        json={"month": "2026-05", "long_term": 100.0, "mid_long_term": 200.0},
    )

    r = client.patch("/api/investments/2026-05", json={"long_term": 1000.0})
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 1200.0  # 1000 + 200


def test_delete_removes_row(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    client.post("/api/investments", json={"month": "2026-06", "long_term": 500.0})
    r = client.delete("/api/investments/2026-06")
    assert r.status_code == 204

    r = client.get("/api/investments/2026-06")
    assert r.status_code == 404
