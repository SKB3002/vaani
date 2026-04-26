"""E2E: add column -> post row with extra field -> GET -> delete column."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_add_post_get_delete_user_column(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # 1. add 'project' column
    r = client.post(
        "/api/tables/expenses/columns",
        json={"key": "project", "label": "Project", "dtype": "string"},
    )
    assert r.status_code == 201, r.text
    merged = r.json()["columns"]
    keys = [c["key"] for c in merged]
    assert "project" in keys

    # 2. GET schema
    r = client.get("/api/tables/expenses/columns")
    assert r.status_code == 200
    keys = [c["key"] for c in r.json()["columns"]]
    assert "project" in keys

    # 3. POST an expense row with the extra field via raw ledger append
    #    (the ExpenseIn pydantic model doesn't know about user columns — this is
    #     the same pattern import / bulk loaders use)
    from app.deps import get_ledger

    get_ledger.cache_clear()
    ledger = get_ledger()
    ledger.append(
        "expenses",
        {
            "id": "01USER",
            "date": "2026-04-22",
            "created_at": "2026-04-22T09:00:00Z",
            "expense_name": "Bike helmet",
            "type_category": "Want, Miscellaneous",
            "payment_method": "paid",
            "paid_for_someone": False,
            "paid_by_someone": False,
            "person_name": None,
            "amount": 3500.0,
            "cash_balance_after": 0.0,
            "online_balance_after": 0.0,
            "source": "manual",
            "raw_transcript": None,
            "notes": None,
            "import_batch_id": None,
            "project": "Bike Upgrades",
        },
    )

    # 4. GET it back — project field should be in the CSV
    df = ledger.read("expenses")
    assert "project" in df.columns
    row = df.loc[df["id"] == "01USER"].iloc[0]
    assert row["project"] == "Bike Upgrades"

    # 5. DELETE the column — warns because data exists
    r = client.delete("/api/tables/expenses/columns/project")
    assert r.status_code == 200
    body = r.json()
    assert body["warning"] is not None and "preserved" in body["warning"]

    # 6. Column is gone from schema but still in CSV (audit safety)
    r = client.get("/api/tables/expenses/columns")
    keys = [c["key"] for c in r.json()["columns"]]
    assert "project" not in keys
    df = ledger.read("expenses")
    assert "project" in df.columns  # preserved


def test_add_column_clash_returns_400(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post(
        "/api/tables/expenses/columns",
        json={"key": "amount", "label": "Amount", "dtype": "number"},
    )
    assert r.status_code == 400


def test_unknown_table_returns_404(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    r = client.get("/api/tables/not_a_real_table/columns")
    assert r.status_code == 404


def test_rename_column_label(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    client.post(
        "/api/tables/wishlist/columns",
        json={"key": "urgency", "label": "Urgency", "dtype": "string"},
    )
    r = client.patch(
        "/api/tables/wishlist/columns/urgency",
        json={"label": "How Urgent"},
    )
    assert r.status_code == 200
    assert r.json()["column"]["label"] == "How Urgent"
