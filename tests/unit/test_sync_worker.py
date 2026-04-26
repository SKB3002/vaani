"""SyncQueue — WAL persistence, retries, deadletter, restart recovery."""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from app.services.sheets.sync_worker import SyncJob, SyncQueue


class FakeClient:
    """Minimal fake SheetsClient. Configurable failure count per call."""

    def __init__(self, *, fail_times: int = 0) -> None:
        self.upserts: list[tuple[str, dict[str, Any], str]] = []
        self.deletes: list[tuple[str, str, str]] = []
        self.remaining_failures = fail_times

    def upsert_row(self, tab: str, row: dict[str, Any], key_column: str = "id") -> None:
        if self.remaining_failures > 0:
            self.remaining_failures -= 1
            raise RuntimeError("simulated transient failure")
        self.upserts.append((tab, row, key_column))

    def delete_row(self, tab: str, key_value: str, key_column: str = "id") -> bool:
        self.deletes.append((tab, key_value, key_column))
        return True


@pytest.fixture
def wal_and_data(tmp_path: Path) -> tuple[Path, Path]:
    wal = tmp_path / ".wal"
    data = tmp_path / "data"
    wal.mkdir()
    data.mkdir()
    return wal, data


async def test_enqueue_success_clears_wal(wal_and_data: tuple[Path, Path]) -> None:
    wal, data = wal_and_data
    client = FakeClient()
    queue = SyncQueue(client=client, wal_dir=wal, data_dir=data, backoff_base=0.01)
    await queue.start()
    try:
        queue.enqueue_upsert("expenses", "ULID1", {"id": "ULID1", "amount": 1.0})
        # Pending WAL should have one line immediately.
        assert (wal / "sheets_pending.jsonl").read_text().count("\n") == 1
        # Give the worker a few ticks to drain.
        for _ in range(20):
            await asyncio.sleep(0.02)
            if client.upserts:
                break
        assert len(client.upserts) == 1
        assert queue.queue_depth() == 0
        remaining = (wal / "sheets_pending.jsonl").read_text().strip()
        assert remaining == ""
    finally:
        await queue.stop()


async def test_retry_succeeds_after_failures(wal_and_data: tuple[Path, Path]) -> None:
    wal, data = wal_and_data
    client = FakeClient(fail_times=3)
    queue = SyncQueue(client=client, wal_dir=wal, data_dir=data, backoff_base=0.01)
    await queue.start()
    try:
        queue.enqueue_upsert("expenses", "A", {"id": "A", "amount": 1.0})
        for _ in range(80):
            await asyncio.sleep(0.02)
            if client.upserts:
                break
        assert len(client.upserts) == 1
        assert queue.deadletter_count() == 0
    finally:
        await queue.stop()


async def test_deadletter_after_max_retries(wal_and_data: tuple[Path, Path]) -> None:
    wal, data = wal_and_data
    client = FakeClient(fail_times=999)
    queue = SyncQueue(
        client=client, wal_dir=wal, data_dir=data, max_retries=3, backoff_base=0.01
    )
    await queue.start()
    try:
        queue.enqueue_upsert("expenses", "B", {"id": "B", "amount": 1.0})
        for _ in range(100):
            await asyncio.sleep(0.02)
            if queue.deadletter_count() >= 1:
                break
        assert queue.deadletter_count() == 1
        assert client.upserts == []
    finally:
        await queue.stop()


async def test_restart_loads_pending_wal(wal_and_data: tuple[Path, Path]) -> None:
    wal, data = wal_and_data
    # Pre-seed the pending WAL as if a previous run died.
    pending = wal / "sheets_pending.jsonl"
    job = SyncJob(
        tab="expenses",
        op="upsert",
        key_column="id",
        key_value="RESTART",
        row={"id": "RESTART", "amount": 42.0},
    )
    pending.write_text(job.to_json() + "\n", encoding="utf-8")

    client = FakeClient()
    queue = SyncQueue(client=client, wal_dir=wal, data_dir=data, backoff_base=0.01)
    await queue.start()
    try:
        for _ in range(30):
            await asyncio.sleep(0.02)
            if client.upserts:
                break
        assert len(client.upserts) == 1
        assert client.upserts[0][1]["id"] == "RESTART"
    finally:
        await queue.stop()


async def test_queue_disabled_non_syncable_table(wal_and_data: tuple[Path, Path]) -> None:
    wal, data = wal_and_data
    queue = SyncQueue(client=FakeClient(), wal_dir=wal, data_dir=data)
    await queue.start()
    try:
        queue.enqueue_upsert("drafts", "D1", {"id": "D1"})
        assert not (wal / "sheets_pending.jsonl").exists() or (
            wal / "sheets_pending.jsonl"
        ).read_text().strip() == ""
    finally:
        await queue.stop()
