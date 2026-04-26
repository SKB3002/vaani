"""LedgerWriter on_change observer — fires exactly once per mutation."""
from __future__ import annotations

from typing import Any

from app.services.ledger import LedgerWriter


def test_append_fires_observer_once(ledger: LedgerWriter) -> None:
    events: list[dict[str, Any]] = []
    ledger.on_change(events.append)
    row = {
        "id": "W1",
        "item": "Camera",
        "target_amount": 1.0,
        "saved_so_far": 0.0,
        "priority": "med",
        "source": "manual",
        "created_at": "2026-04-23T00:00:00Z",
        "status": "active",
    }
    ledger.append("wishlist", row)

    assert len(events) == 1
    evt = events[0]
    assert evt["table"] == "wishlist"
    assert evt["op"] == "append"
    assert evt["pk_column"] == "id"
    assert evt["pk_value"] == "W1"
    assert evt["row"]["item"] == "Camera"


def test_update_fires_observer(ledger: LedgerWriter) -> None:
    ledger.append(
        "wishlist",
        {
            "id": "W1",
            "item": "X",
            "target_amount": 1.0,
            "saved_so_far": 0.0,
            "priority": "med",
            "source": "manual",
            "created_at": "2026-04-23T00:00:00Z",
            "status": "active",
        },
    )
    events: list[dict[str, Any]] = []
    ledger.on_change(events.append)
    ledger.update("wishlist", "W1", {"priority": "high"})
    assert len(events) == 1
    assert events[0]["op"] == "update"
    assert events[0]["pk_value"] == "W1"


def test_delete_fires_observer(ledger: LedgerWriter) -> None:
    ledger.append(
        "wishlist",
        {
            "id": "W2",
            "item": "Y",
            "target_amount": 1.0,
            "saved_so_far": 0.0,
            "priority": "med",
            "source": "manual",
            "created_at": "2026-04-23T00:00:00Z",
            "status": "active",
        },
    )
    events: list[dict[str, Any]] = []
    ledger.on_change(events.append)
    assert ledger.delete("wishlist", "W2") is True
    assert len(events) == 1
    assert events[0]["op"] == "delete"


def test_observer_exception_never_breaks_write(ledger: LedgerWriter) -> None:
    def boom(_: dict[str, Any]) -> None:
        raise RuntimeError("observer should not break writes")

    ledger.on_change(boom)
    # Must still succeed despite the observer raising.
    row = {
        "id": "W3",
        "item": "Z",
        "target_amount": 1.0,
        "saved_so_far": 0.0,
        "priority": "med",
        "source": "manual",
        "created_at": "2026-04-23T00:00:00Z",
        "status": "active",
    }
    written = ledger.append("wishlist", row)
    assert written["id"] == "W3"


def test_failed_update_does_not_fire_observer(ledger: LedgerWriter) -> None:
    events: list[dict[str, Any]] = []
    ledger.on_change(events.append)
    assert ledger.update("wishlist", "NON_EXISTENT", {"priority": "high"}) is None
    assert events == []
