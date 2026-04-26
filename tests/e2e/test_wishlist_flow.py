"""E2E: POST wish -> contribute from expense -> both ledgers updated."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_wishlist_end_to_end(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # seed balances so the dual-write expense row gets a balance snapshot
    r = client.post(
        "/api/balances", json={"cash_balance": 500.0, "online_balance": 30000.0}
    )
    assert r.status_code == 201

    # 1. POST wish
    r = client.post(
        "/api/wishlist",
        json={
            "item": "Treadmill",
            "target_amount": 20000.0,
            "priority": "med",
            "notes": "For the home gym",
        },
    )
    assert r.status_code == 201, r.text
    wish = r.json()
    wish_id = wish["id"]
    assert wish["saved_so_far"] == 0.0

    # 2. Contribute 5000 via expense source
    r = client.post(
        f"/api/wishlist/{wish_id}/contribute",
        json={"amount": 5000.0, "source": "expense"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["wishlist"]["saved_so_far"] == 5000.0
    assert body["expense"] is not None
    assert body["expense"]["notes"] == f"wishlist:{wish_id}"

    # 3. GET wishlist → progress visible
    r = client.get("/api/wishlist")
    items = r.json()
    mine = next(w for w in items if w["id"] == wish_id)
    assert mine["saved_so_far"] == 5000.0
    assert mine["status"] == "active"

    # 4. GET expenses → contribution row is there with correct tag
    r = client.get("/api/expenses")
    assert r.status_code == 200
    exps = r.json()
    contrib = [e for e in exps if e.get("notes") == f"wishlist:{wish_id}"]
    assert len(contrib) == 1
    assert contrib[0]["type_category"] == "Investment, Miscellaneous"
    assert contrib[0]["amount"] == 5000.0

    # 5. Balances updated: online -= 5000
    r = client.get("/api/balances/current")
    assert r.status_code == 200
    assert r.json()["online_balance"] == 25000.0

    # 6. Filter tabs: achieved empty, active has our row, all has it too
    r = client.get("/api/wishlist?status=achieved")
    assert all(w["id"] != wish_id for w in r.json())
    r = client.get("/api/wishlist?status=all")
    assert any(w["id"] == wish_id for w in r.json())
