"""Unit: contribute with source=expense dual-writes to expenses.csv.

Per §4.1 single-ledger principle, a wishlist contribution that the user
flags as a real spend must appear in expenses.csv with
type_category='Investment, Miscellaneous' and notes='wishlist:{id}'.
"""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.deps import get_ledger
from app.main import create_app


def test_contribute_from_expense_dual_writes(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # seed balances so the balance snapshot works
    client.post(
        "/api/balances", json={"cash_balance": 1000.0, "online_balance": 20000.0}
    )

    r = client.post(
        "/api/wishlist",
        json={"item": "Camera", "target_amount": 50000.0},
    )
    wish_id = r.json()["id"]

    r = client.post(
        f"/api/wishlist/{wish_id}/contribute",
        json={"amount": 2500.0, "source": "expense"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wishlist"]["saved_so_far"] == 2500.0
    assert body["expense"] is not None
    assert body["expense"]["type_category"] == "Investment, Miscellaneous"
    assert body["expense"]["amount"] == 2500.0
    assert body["expense"]["notes"] == f"wishlist:{wish_id}"

    # expenses.csv has the contribution row
    get_ledger.cache_clear()
    ledger = get_ledger()
    df = ledger.read("expenses")
    mask = df["notes"].astype("string") == f"wishlist:{wish_id}"
    assert mask.sum() == 1
    row = df.loc[mask].iloc[0]
    assert row["type_category"] == "Investment, Miscellaneous"
    assert float(row["amount"]) == 2500.0


def test_contribute_expense_idempotent_via_ulid(tmp_workspace: Path) -> None:
    """Two contributions of the same amount create two distinct expense rows.

    Wishlist contributions are intentionally NOT dedup-keyed — users may
    contribute the same amount twice legitimately. Each call gets a fresh ULID.
    """
    app = create_app()
    client = TestClient(app)

    client.post("/api/balances", json={"cash_balance": 0.0, "online_balance": 10000.0})
    r = client.post("/api/wishlist", json={"item": "Subs", "target_amount": 1000.0})
    wish_id = r.json()["id"]

    for _ in range(3):
        r = client.post(
            f"/api/wishlist/{wish_id}/contribute",
            json={"amount": 100.0, "source": "expense"},
        )
        assert r.status_code == 200

    get_ledger.cache_clear()
    ledger = get_ledger()
    df = ledger.read("expenses")
    mask = df["notes"].astype("string") == f"wishlist:{wish_id}"
    assert mask.sum() == 3

    ids = df.loc[mask, "id"].tolist()
    assert len(set(ids)) == 3  # three distinct ULIDs


def test_contribute_manual_does_not_touch_expenses(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    r = client.post("/api/wishlist", json={"item": "Thing", "target_amount": 500.0})
    wish_id = r.json()["id"]

    r = client.post(
        f"/api/wishlist/{wish_id}/contribute",
        json={"amount": 100.0, "source": "manual"},
    )
    assert r.status_code == 200
    assert r.json()["expense"] is None

    get_ledger.cache_clear()
    ledger = get_ledger()
    df = ledger.read("expenses")
    assert df.empty or (df["notes"].astype("string") == f"wishlist:{wish_id}").sum() == 0
