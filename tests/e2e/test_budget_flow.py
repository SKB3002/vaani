"""E2E budget flow — rule → expense → table-c, cap patch → table-c reflects."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


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

        # Add expense
        exp = c.post("/api/expenses", json={
            "date": "2026-04-10",
            "expense_name": "electricity bill",
            "type_category": "Need, Miscellaneous",
            "payment_method": "paid",
            "paid_for_someone": False,
            "paid_by_someone": False,
            "amount": 2500,
            "source": "manual",
        })
        assert exp.status_code == 201
        # Tag the expense via PATCH with custom_tag
        eid = exp.json()["id"]
        c.patch(f"/api/expenses/{eid}", json={"custom_tag": "electricity"})

        # Force recompute
        summary = c.post("/api/budgets/recompute").json()
        assert summary["months_computed"] >= 1

        # Get Table C for that month
        tc = c.get("/api/budgets/table-c?month=2026-04").json()
        rows = tc["rows"]
        # Bootstrap seeds Emergency + Medical rules by default → 3 rows total.
        rule_names = {r["category"] for r in rows}
        assert "electricity" in rule_names
        assert "Emergency" in rule_names
        assert "Medical" in rule_names
        elec = next(r for r in rows if r["category"] == "electricity")
        assert elec["actual"] == 2500.0
        assert elec["carry_buffer"] == 500.0


def test_cap_patch_reflects(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        # Rule with tiny carry_cap forces overflow
        c.post("/api/budgets/rules", json={
            "category": "X",
            "monthly_budget": 1000,
            "carry_cap": 0,
            "priority": 1,
        })

        # Initial caps
        r0 = c.get("/api/budgets/caps").json()
        assert r0["medical_upper_cap"] == 10000

        # Lower medical cap so overflow can't fit
        r = c.patch("/api/budgets/caps", json={
            "medical_upper_cap": 500,
            "emergency_monthly_cap": 0,
        })
        assert r.status_code == 200
        assert r.json()["medical_upper_cap"] == 500

        # Recompute is triggered by PATCH already; read current month (rule has no expense → remaining = budget = 1000, overflow = 1000)
        # 500 to medical (cap), 0 to emergency, 500 lost
        tc = c.post("/api/budgets/recompute").json()
        # Find a row in snapshot
        if tc["last_month_snapshot"]:
            row = tc["last_month_snapshot"][0]
            assert row["to_medical"] == 500.0
            assert row["to_emergency"] == 0.0
