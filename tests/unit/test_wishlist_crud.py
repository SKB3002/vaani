"""Unit: wishlist full lifecycle: create -> contribute -> achieved -> abandon."""
from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from app.main import create_app


def test_wishlist_full_lifecycle(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)

    # create
    r = client.post(
        "/api/wishlist",
        json={
            "item": "New bike",
            "target_amount": 10000.0,
            "priority": "high",
            "notes": "Trek FX",
            "link": "https://example.com/bike",
        },
    )
    assert r.status_code == 201, r.text
    created = r.json()
    wish_id = created["id"]
    assert created["saved_so_far"] == 0.0
    assert created["status"] == "active"
    assert created["source"] == "manual"
    assert created["priority"] == "high"
    assert created["notes"] == "Trek FX"
    assert created["link"] == "https://example.com/bike"

    # list default = active
    r = client.get("/api/wishlist")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # contribute partial (manual — no expense row written)
    r = client.post(
        f"/api/wishlist/{wish_id}/contribute", json={"amount": 4000.0, "source": "manual"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["wishlist"]["saved_so_far"] == 4000.0
    assert body["wishlist"]["status"] == "active"
    assert body["expense"] is None

    # contribute enough to cross threshold
    r = client.post(
        f"/api/wishlist/{wish_id}/contribute", json={"amount": 6000.0, "source": "manual"}
    )
    assert r.status_code == 200
    body = r.json()
    assert body["wishlist"]["saved_so_far"] == 10000.0
    assert body["wishlist"]["status"] == "achieved"

    # achieved is now in the achieved filter, not active
    r = client.get("/api/wishlist?status=active")
    assert r.json() == []
    r = client.get("/api/wishlist?status=achieved")
    assert len(r.json()) == 1

    # soft-delete → abandoned
    r = client.delete(f"/api/wishlist/{wish_id}")
    assert r.status_code == 200
    assert r.json()["status"] == "abandoned"

    r = client.get(f"/api/wishlist/{wish_id}")
    assert r.json()["status"] == "abandoned"


def test_hard_delete(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/wishlist", json={"item": "Headphones", "target_amount": 5000.0})
    wish_id = r.json()["id"]

    r = client.delete(f"/api/wishlist/{wish_id}?hard=true")
    assert r.status_code == 200
    assert r.json()["deleted"] == "hard"

    r = client.get(f"/api/wishlist/{wish_id}")
    assert r.status_code == 404


def test_patch_auto_achieves(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    r = client.post("/api/wishlist", json={"item": "Book", "target_amount": 500.0})
    wish_id = r.json()["id"]

    r = client.patch(f"/api/wishlist/{wish_id}", json={"saved_so_far": 500.0})
    assert r.status_code == 200
    assert r.json()["status"] == "achieved"


def test_validation_errors(tmp_workspace: Path) -> None:
    app = create_app()
    client = TestClient(app)
    # target_amount must be > 0
    r = client.post("/api/wishlist", json={"item": "X", "target_amount": 0})
    assert r.status_code == 422
    # empty item
    r = client.post("/api/wishlist", json={"item": "", "target_amount": 100})
    assert r.status_code == 422
