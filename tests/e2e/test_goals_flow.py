"""E2E goals flow — create, contribute, status progression, achieve."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_goal_lifecycle(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        # Create a goal in Table A
        a = c.post("/api/goals/overview", json={
            "goal_name": "Trip",
            "target_amount": 10000,
            "current_amount": 0,
            "monthly_contribution": 1000,
        }).json()
        assert a["status"] == "just_started"
        assert a["months_left"] == 10
        assert a["pct_complete"] == 0

        # PATCH to move current — status → in_progress
        p = c.patch(f"/api/goals/overview/{a['goal_id']}", json={"current_amount": 2000}).json()
        assert p["pct_complete"] == 20.0
        assert p["status"] == "in_progress"

        # PATCH → nearing_goal
        p = c.patch(f"/api/goals/overview/{a['goal_id']}", json={"current_amount": 8500}).json()
        assert p["status"] == "nearing_goal"

        # PATCH → achieved
        p = c.patch(f"/api/goals/overview/{a['goal_id']}", json={"current_amount": 10000}).json()
        assert p["status"] == "achieved"
        assert p["months_left"] == 0

        # List returns freshly derived fields
        rows = c.get("/api/goals/overview").json()
        assert any(r["status"] == "achieved" for r in rows)

        # Delete
        d = c.delete(f"/api/goals/overview/{a['goal_id']}")
        assert d.status_code == 204


def test_sources_contribute_flow(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        b = c.post("/api/goals/sources", json={
            "goal_name": "Car",
            "target_amount": 50000,
            "manual_saved": 0,
            "auto_added": 0,
            "monthly_contribution": 2500,
        }).json()
        gid = b["goal_id"]
        # Two contributions
        c.post(f"/api/goals/sources/{gid}/contribute", json={"amount": 5000, "kind": "manual"})
        r = c.post(f"/api/goals/sources/{gid}/contribute", json={"amount": 2500, "kind": "auto"}).json()
        assert r["manual_saved"] == 5000.0
        assert r["auto_added"] == 2500.0
        assert r["total_saved"] == 7500.0
        assert r["status"] == "in_progress"
