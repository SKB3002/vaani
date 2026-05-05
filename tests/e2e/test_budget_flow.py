"""E2E budget flow — rule + expense → table-c reflects current-month state."""
from __future__ import annotations

from datetime import date
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from app.main import create_app


def _today() -> str:
    return date.today().strftime("%Y-%m-%d")


def _today_month() -> str:
    from datetime import datetime
    return datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y-%m")


def test_rule_expense_tablec(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        # Add rule
        r = c.post("/api/budgets/rules", json={
            "category": "electricity",
            "monthly_budget": 3000,
            "carry_cap": 4000,
            "priority": 1,
        })
        assert r.status_code == 201

        # Add expense in current month so it counts
        today = _today()
        exp = c.post("/api/expenses", json={
            "date": today,
            "expense_name": "electricity bill",
            "type_category": "Need, Miscellaneous",
            "payment_method": "paid",
            "paid_for_someone": False,
            "paid_by_someone": False,
            "amount": 2500,
            "source": "manual",
        })
        assert exp.status_code == 201
        eid = exp.json()["id"]
        c.patch(f"/api/expenses/{eid}", json={"custom_tag": "electricity"})

        # Force recompute
        c.post("/api/budgets/recompute")

        # Read Table C — month param accepted but engine returns current-month state
        tc = c.get(f"/api/budgets/table-c?month={_today_month()}").json()
        rows = tc["rows"]
        rule_names = {r["category"] for r in rows}
        assert "electricity" in rule_names
        assert "Emergency" in rule_names
        assert "Medical" in rule_names
        elec = next(r for r in rows if r["category"] == "electricity")
        # Pool was seeded to 3000; spent 2500 → remaining 500
        assert elec["budget"] == 3000.0
        assert elec["actual"] == 2500.0
        assert elec["remaining"] == 500.0


def test_cap_patch_persists(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        r0 = c.get("/api/budgets/caps").json()
        assert r0["medical_upper_cap"] == 10000

        r = c.patch("/api/budgets/caps", json={
            "medical_upper_cap": 500,
            "emergency_monthly_cap": 0,
        })
        assert r.status_code == 200
        assert r.json()["medical_upper_cap"] == 500

        r2 = c.get("/api/budgets/caps").json()
        assert r2["medical_upper_cap"] == 500
        assert r2["emergency_monthly_cap"] == 0
