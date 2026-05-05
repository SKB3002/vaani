"""Auto-add tags when budget rules change — surfaces them to the LLM and the
expense form via uniques.json."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import uniques as uniques_store


def test_creating_rule_adds_tag(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Utilities", "monthly_budget": 3000, "carry_cap": 0, "priority": 1,
        })
        tags = uniques_store.list_tags()
        assert "Utilities" in tags


def test_tags_endpoint_returns_known_tags(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Rent", "monthly_budget": 12000, "carry_cap": 0, "priority": 1,
        })
        c.post("/api/budgets/rules", json={
            "category": "Medical", "monthly_budget": 0, "carry_cap": 0, "priority": 91,
        })
        r = c.get("/api/budgets/tags").json()
        assert "Rent" in r["tags"]
        assert "Medical" in r["tags"]


def test_deleting_rule_removes_tag(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Utilities", "monthly_budget": 3000, "carry_cap": 0, "priority": 1,
        })
        assert "Utilities" in uniques_store.list_tags()

        r = c.delete("/api/budgets/rules/Utilities")
        assert r.status_code == 204
        assert "Utilities" not in uniques_store.list_tags()


def test_duplicate_create_does_not_double_add(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        for _ in range(3):
            c.post("/api/budgets/rules", json={
                "category": "Utilities", "monthly_budget": 3000, "carry_cap": 0, "priority": 1,
            })
        tags = uniques_store.list_tags()
        assert tags.count("Utilities") == 1


def test_uniques_endpoint_includes_tags_field(tmp_workspace: Path) -> None:
    """The /api/uniques endpoint (used by voice flow) surfaces tags so the LLM sees them."""
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Utilities", "monthly_budget": 3000, "carry_cap": 0, "priority": 1,
        })
        r = c.get("/api/uniques").json()
        assert "tags" in r
        assert "Utilities" in r["tags"]
