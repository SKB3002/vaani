"""Custom tags carry a Need/Want/Investment type.

Creating a tag via POST /api/budgets/tags must:
  - register it in uniques (so the LLM sees it),
  - record tag -> type (so the grouped Table C view rolls it up + LLM hints),
  - auto-create a budget_rules row (so the tag gets its own Table C line),
and the tagged spend must flow into that line's `actual`.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app
from app.services import uniques as uniques_store


def test_create_tag_registers_tag_and_type(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        r = c.post("/api/budgets/tags", json={"name": "Gym", "type": "Want"})
        assert r.status_code == 201
        body = r.json()
        assert body["name"] == "Gym"
        assert body["type"] == "Want"

        assert "Gym" in uniques_store.list_tags()
        assert uniques_store.get_tag_type("gym") == "Want"  # case-insensitive lookup


def test_create_tag_auto_creates_budget_rule(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/tags", json={
            "name": "Netflix", "type": "Want", "monthly_budget": 500,
        })
        rules = c.get("/api/budgets/rules").json()
        cats = {r["category"]: r for r in rules}
        assert "Netflix" in cats
        assert cats["Netflix"]["monthly_budget"] == 500.0


def test_tags_endpoint_returns_items_with_types(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/tags", json={"name": "Gym", "type": "Want"})
        c.post("/api/budgets/tags", json={"name": "Medical", "type": "Need"})
        r = c.get("/api/budgets/tags").json()
        # Back-compat flat list still present.
        assert "Gym" in r["tags"] and "Medical" in r["tags"]
        by_name = {it["name"]: it["type"] for it in r["items"]}
        assert by_name["Gym"] == "Want"
        assert by_name["Medical"] == "Need"


def test_tag_type_reaches_llm_context(tmp_workspace: Path) -> None:
    """uniques.json (sent verbatim to the LLM) carries tag_types."""
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/tags", json={"name": "Gym", "type": "Want"})
        u = c.get("/api/uniques").json()
        assert "Gym" in u["tags"]
        assert u.get("tag_types", {}).get("Gym") == "Want"


def test_tagged_expense_flows_into_table_c(tmp_workspace: Path) -> None:
    """End-to-end: a custom-tag expense lands in the tag's Table C row `actual`."""
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/tags", json={
            "name": "Gym", "type": "Want", "monthly_budget": 1000,
        })
        # Log an expense tagged with the new tag.
        r = c.post("/api/expenses", json={
            "date": "2026-06-01",
            "expense_name": "test monthly membership",
            "type_category": "Want, Enjoyment",
            "payment_method": "paid",
            "amount": 750,
            "custom_tag": "Gym",
        })
        assert r.status_code == 201

        c.post("/api/budgets/recompute")
        rows = c.get("/api/budgets/table-c").json()["rows"]
        gym = next((row for row in rows if row["category"] == "Gym"), None)
        assert gym is not None
        assert gym["actual"] == 750.0


def test_deleting_rule_clears_tag_type(tmp_workspace: Path) -> None:
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/tags", json={"name": "Gym", "type": "Want"})
        assert uniques_store.get_tag_type("Gym") == "Want"

        assert c.delete("/api/budgets/rules/Gym").status_code == 204
        assert "Gym" not in uniques_store.list_tags()
        assert uniques_store.get_tag_type("Gym") is None


def test_rule_with_type_records_tag_type(tmp_workspace: Path) -> None:
    """Adding a bare-tag rule with `type` records the tag_type mapping."""
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Rent", "monthly_budget": 12000,
            "carry_cap": 0, "priority": 1, "type": "Need",
        })
        assert uniques_store.get_tag_type("Rent") == "Need"


def test_builtin_category_rule_gets_no_tag_type(tmp_workspace: Path) -> None:
    """A 'Type, Category' rule carries its type in the prefix — no tag_type entry."""
    app = create_app()
    with TestClient(app) as c:
        c.post("/api/budgets/rules", json={
            "category": "Need, Travel", "monthly_budget": 2000,
            "carry_cap": 0, "priority": 1, "type": "Need",
        })
        assert uniques_store.get_tag_type("Need, Travel") is None
