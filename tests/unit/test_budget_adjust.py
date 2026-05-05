"""POST /api/budgets/adjust — Add/Set buttons → budget_state + audit log."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_adjust_add_increments_pool(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Medical", "monthly_budget": 0, "carry_cap": 0, "priority": 91,
        })

        r = c.post("/api/budgets/adjust", json={
            "category": "Medical", "amount": 500, "kind": "add", "note": "set aside",
        })
        assert r.status_code == 200
        assert r.json()["current_budget"] == 500.0

        r = c.post("/api/budgets/adjust", json={
            "category": "Medical", "amount": 250, "kind": "add",
        })
        assert r.json()["current_budget"] == 750.0


def test_adjust_set_overwrites_pool(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Utilities", "monthly_budget": 7000, "carry_cap": 0, "priority": 1,
        })
        # Initial recompute seeds pool to 7000
        c.post("/api/budgets/recompute")

        r = c.post("/api/budgets/adjust", json={
            "category": "Utilities", "amount": 3000, "kind": "set", "note": "reset",
        })
        assert r.status_code == 200
        assert r.json()["current_budget"] == 3000.0


def test_adjust_unknown_category_404(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/budgets/adjust", json={
            "category": "Nonexistent", "amount": 100, "kind": "add",
        })
        assert r.status_code == 404


def test_adjust_validates_kind(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "X", "monthly_budget": 0, "carry_cap": 0, "priority": 1,
        })
        r = c.post("/api/budgets/adjust", json={
            "category": "X", "amount": 100, "kind": "subtract",
        })
        assert r.status_code == 422


def test_adjust_rejects_negative_amount(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "X", "monthly_budget": 0, "carry_cap": 0, "priority": 1,
        })
        r = c.post("/api/budgets/adjust", json={
            "category": "X", "amount": -50, "kind": "add",
        })
        assert r.status_code == 422
