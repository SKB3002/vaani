"""Goals B: contribute (manual + auto) + sync_to_overview path."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def _client(_tmp_workspace: Path) -> TestClient:
    app = create_app()
    return TestClient(app)


def test_manual_and_auto_contributions(tmp_workspace: Path) -> None:
    c = _client(tmp_workspace)
    with c:
        created = c.post("/api/goals/sources", json={
            "goal_name": "Bike",
            "target_amount": 100000,
            "manual_saved": 1000,
            "auto_added": 0,
            "monthly_contribution": 5000,
        }).json()
        gid = created["goal_id"]
        assert created["total_saved"] == 1000.0

        r1 = c.post(f"/api/goals/sources/{gid}/contribute", json={"amount": 500, "kind": "manual"}).json()
        assert r1["manual_saved"] == 1500.0
        assert r1["total_saved"] == 1500.0

        r2 = c.post(f"/api/goals/sources/{gid}/contribute", json={"amount": 800, "kind": "auto"}).json()
        assert r2["auto_added"] == 800.0
        assert r2["total_saved"] == 2300.0


def test_sync_to_overview(tmp_workspace: Path) -> None:
    c = _client(tmp_workspace)
    with c:
        # Create matching rows in A and B with same goal_name
        a = c.post("/api/goals/overview", json={
            "goal_name": "Laptop",
            "target_amount": 50000,
            "current_amount": 0,
            "monthly_contribution": 5000,
        }).json()
        b = c.post("/api/goals/sources", json={
            "goal_name": "Laptop",
            "target_amount": 50000,
            "manual_saved": 0,
            "auto_added": 0,
            "monthly_contribution": 5000,
        }).json()

        # Contribute with sync
        c.post(
            f"/api/goals/sources/{b['goal_id']}/contribute?sync_to_overview=true",
            json={"amount": 2500, "kind": "manual"},
        )

        # Verify A row was updated
        overview = c.get("/api/goals/overview").json()
        a_now = next(g for g in overview if g["goal_id"] == a["goal_id"])
        assert a_now["current_amount"] == 2500.0
        assert a_now["pct_complete"] == 5.0


def test_no_sync_by_default(tmp_workspace: Path) -> None:
    c = _client(tmp_workspace)
    with c:
        a = c.post("/api/goals/overview", json={
            "goal_name": "Phone",
            "target_amount": 80000,
            "current_amount": 0,
            "monthly_contribution": 8000,
        }).json()
        b = c.post("/api/goals/sources", json={
            "goal_name": "Phone",
            "target_amount": 80000,
            "manual_saved": 0,
            "auto_added": 0,
            "monthly_contribution": 8000,
        }).json()
        c.post(
            f"/api/goals/sources/{b['goal_id']}/contribute",
            json={"amount": 1000, "kind": "manual"},
        )
        overview = c.get("/api/goals/overview").json()
        a_now = next(g for g in overview if g["goal_id"] == a["goal_id"])
        assert a_now["current_amount"] == 0.0  # unchanged
