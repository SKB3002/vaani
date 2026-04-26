"""Sheets enabled path with mocked SheetsClient — observer enqueues on write."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.routers import sheets as sheets_router
from app.services.sheets.sync_worker import SyncQueue, register_sheets_observer


class MockClient:
    def __init__(self) -> None:
        self.upserts: list[tuple[str, dict[str, Any], str]] = []
        self.deletes: list[tuple[str, str, str]] = []

    def spreadsheet_title(self) -> str:
        return "MockSheet"

    def list_tabs(self) -> list[str]:
        return ["expenses"]

    def ensure_tab(self, name: str, headers: list[str]) -> Any:
        return None

    def upsert_row(self, tab: str, row: dict[str, Any], key_column: str = "id") -> None:
        self.upserts.append((tab, row, key_column))

    def delete_row(self, tab: str, key_value: str, key_column: str = "id") -> bool:
        self.deletes.append((tab, key_value, key_column))
        return True

    def read_all(self, tab: str) -> list[dict[str, Any]]:
        return []

    def batch_upsert(
        self, tab: str, rows: list[dict[str, Any]], key_column: str = "id"
    ) -> int:
        for r in rows:
            self.upsert_row(tab, r, key_column)
        return len(rows)


@pytest.fixture
def mocked_sheets_app(tmp_workspace: Path):  # type: ignore[no-untyped-def]
    """Spin a TestClient with the mock Sheets client attached manually."""
    app = create_app()
    mock = MockClient()

    # We drive the app through its normal lifespan (disabled), then attach manually.
    with TestClient(app) as client:
        from app.deps import get_ledger

        ledger = get_ledger()
        queue = SyncQueue(
            client=mock,  # type: ignore[arg-type]
            wal_dir=tmp_workspace / ".wal",
            data_dir=tmp_workspace / "data",
            backoff_base=0.01,
        )
        # NOTE: worker loop is not started here — observer still writes to
        # the pending WAL (durable), and `/drain-queue` executes synchronously
        # via the queue's own asyncio primitives inside the endpoint's loop.
        register_sheets_observer(ledger, queue)
        sheets_router.set_client(mock)  # type: ignore[arg-type]
        sheets_router.set_sync_queue(queue)
        yield client, mock, queue
        ledger.clear_observers()
        sheets_router.set_client(None)
        sheets_router.set_sync_queue(None)


def test_expense_write_enqueues_sync_job(mocked_sheets_app) -> None:  # type: ignore[no-untyped-def]
    client, mock, queue = mocked_sheets_app

    # Seed balance first.
    r = client.post(
        "/api/balances", json={"cash_balance": 1000.0, "online_balance": 50000.0}
    )
    assert r.status_code == 201

    # Add an expense — observer should enqueue one upsert for expenses.
    r = client.post(
        "/api/expenses",
        json={
            "date": "2026-04-23",
            "expense_name": "Test",
            "type_category": "Need, Food & Drinks",
            "payment_method": "paid",
            "amount": 42.0,
        },
    )
    assert r.status_code == 201

    # Queue depth should be >= 1 (balances + expenses both enqueue).
    r = client.get("/api/sheets/status")
    body = r.json()
    # enabled=False because env var not set, but queue still populated via manual wiring.
    assert body["queue_depth"] >= 1


def test_drain_empties_queue(mocked_sheets_app) -> None:  # type: ignore[no-untyped-def]
    client, mock, queue = mocked_sheets_app
    client.post("/api/balances", json={"cash_balance": 100.0, "online_balance": 100.0})
    client.post(
        "/api/expenses",
        json={
            "date": "2026-04-23",
            "expense_name": "X",
            "type_category": "Need, Miscellaneous",
            "payment_method": "paid_cash",
            "amount": 10.0,
        },
    )
    r = client.post("/api/sheets/drain-queue")
    assert r.status_code == 200
    body = r.json()
    assert body["remaining"] == 0
    # Mock client should have received the upserts.
    assert len(mock.upserts) >= 1
