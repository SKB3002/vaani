"""Async sync worker for Google Sheets.

Guarantees:
- Local CSV write never blocks on the queue. `enqueue_*` is sync and fast.
- On startup we load `.wal/sheets_pending.jsonl` and re-queue its contents.
- On failure past `max_retries` we move the job to `.wal/sheets_deadletter.jsonl`
  and keep draining.
- On success we remove the job's line from the pending WAL.
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from app.services.ledger import ChangeEvent, LedgerWriter
from app.services.sheets.client import SheetsClient, SheetsClientError
from app.storage.schemas import SCHEMAS
from app.storage.user_columns import resolve_columns

logger = logging.getLogger("fineye.sheets.worker")

SYNCABLE_TABLES: set[str] = {
    "expenses",
    "balances",
    "wishlist",
    "goals_a",
    "goals_b",
    "budget_rules",
    "budget_table_c",
    "investments",
}

MAX_BACKOFF_S = 30.0


@dataclass
class SyncJob:
    tab: str
    op: str  # "upsert" | "delete"
    key_column: str
    key_value: str
    row: dict[str, Any] | None
    enqueued_at: str = field(default_factory=lambda: datetime.now(UTC).isoformat())
    attempts: int = 0

    def to_json(self) -> str:
        return json.dumps(asdict(self), default=str)

    @classmethod
    def from_json(cls, line: str) -> SyncJob:
        data = json.loads(line)
        return cls(**data)


class SyncQueue:
    """In-process async queue + durable pending WAL.

    Thread-safety:
    - `enqueue_*` is called from sync endpoints (request threads).
    - The worker coroutine runs on the event loop.
    - WAL file writes are serialised with a `threading.Lock`.
    """

    def __init__(
        self,
        client: SheetsClient | None,
        wal_dir: str | Path,
        *,
        data_dir: str | Path,
        max_retries: int = 6,
        backoff_base: float = 1.0,
    ) -> None:
        self.client = client
        self.wal_dir = Path(wal_dir)
        self.data_dir = Path(data_dir)
        self.max_retries = max_retries
        self.backoff_base = backoff_base

        self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.pending_path = self.wal_dir / "sheets_pending.jsonl"
        self.deadletter_path = self.wal_dir / "sheets_deadletter.jsonl"

        self._queue: asyncio.Queue[SyncJob] | None = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._worker_task: asyncio.Task[None] | None = None
        self._wal_lock = threading.Lock()
        self._last_sync_at: str | None = None
        self._last_error: str | None = None

    # ---------- lifecycle ----------
    async def start(self) -> None:
        if self._worker_task is not None:
            return
        self._queue = asyncio.Queue()
        self._loop = asyncio.get_running_loop()
        # Reload anything we didn't finish last time.
        for job in self._load_pending():
            await self._queue.put(job)
        self._worker_task = asyncio.create_task(self._run(), name="sheets-sync-worker")

    async def stop(self) -> None:
        if self._worker_task is None:
            return
        self._worker_task.cancel()
        try:
            await self._worker_task
        except (asyncio.CancelledError, Exception):  # noqa: BLE001
            pass
        self._worker_task = None

    # ---------- enqueue ----------
    def enqueue_upsert(
        self, tab: str, key_value: str, row: dict[str, Any], key_column: str = "id"
    ) -> None:
        if tab not in SYNCABLE_TABLES:
            return
        job = SyncJob(
            tab=tab,
            op="upsert",
            key_column=key_column,
            key_value=str(key_value) if key_value is not None else "",
            row=row,
        )
        self._persist(job)
        self._submit(job)

    def enqueue_delete(self, tab: str, key_value: str, key_column: str = "id") -> None:
        if tab not in SYNCABLE_TABLES:
            return
        job = SyncJob(
            tab=tab,
            op="delete",
            key_column=key_column,
            key_value=str(key_value),
            row=None,
        )
        self._persist(job)
        self._submit(job)

    # ---------- introspection ----------
    def queue_depth(self) -> int:
        return self._queue.qsize() if self._queue is not None else self._count_pending()

    def deadletter_count(self) -> int:
        if not self.deadletter_path.exists():
            return 0
        with self.deadletter_path.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def last_sync_at(self) -> str | None:
        return self._last_sync_at

    def last_error(self) -> str | None:
        return self._last_error

    async def drain(self) -> dict[str, int]:
        """Force drain: process every queued/pending job now. Returns counts."""
        if self.client is None:
            return {"processed": 0, "failed": 0, "remaining": self._count_pending()}
        processed = 0
        failed = 0
        if self._queue is not None:
            while not self._queue.empty():
                job = self._queue.get_nowait()
                ok = await self._execute(job)
                if ok:
                    processed += 1
                else:
                    failed += 1
        # Also sweep anything still pending on disk (e.g. worker wasn't started).
        for job in self._load_pending():
            ok = await self._execute(job)
            if ok:
                processed += 1
            else:
                failed += 1
        return {
            "processed": processed,
            "failed": failed,
            "remaining": self._count_pending(),
        }

    # ---------- internals ----------
    def _submit(self, job: SyncJob) -> None:
        if self._queue is None or self._loop is None:
            # worker not running — keep on disk; it'll be picked up at next start()
            return
        try:
            self._loop.call_soon_threadsafe(self._queue.put_nowait, job)
        except RuntimeError:
            logger.debug("event loop unavailable, job %s deferred to WAL", job.tab)

    async def _run(self) -> None:
        assert self._queue is not None
        while True:
            try:
                job = await self._queue.get()
            except asyncio.CancelledError:
                return
            await self._execute(job)

    async def _execute(self, job: SyncJob) -> bool:
        """Run one job. Returns True on success."""
        if self.client is None:
            self._last_error = "sheets disabled"
            self._to_deadletter(job)
            self._clear_pending(job)
            return False
        try:
            await asyncio.to_thread(self._do_call, job)
        except Exception as exc:  # noqa: BLE001
            job.attempts += 1
            self._last_error = f"{type(exc).__name__}: {exc}"
            logger.warning(
                "sheets sync attempt %d failed for %s/%s: %s",
                job.attempts,
                job.tab,
                job.key_value,
                exc,
            )
            if job.attempts >= self.max_retries:
                self._to_deadletter(job)
                self._clear_pending(job)
                return False
            delay = min(self.backoff_base * (2**job.attempts), MAX_BACKOFF_S)
            asyncio.create_task(self._requeue_after(job, delay))
            return False
        else:
            self._last_sync_at = datetime.now(UTC).isoformat()
            self._last_error = None
            self._clear_pending(job)
            return True

    def _do_call(self, job: SyncJob) -> None:
        assert self.client is not None
        if job.op == "upsert":
            if job.row is None:
                raise SheetsClientError("upsert job missing row")
            self.client.upsert_row(job.tab, job.row, job.key_column)
        elif job.op == "delete":
            self.client.delete_row(job.tab, job.key_value, job.key_column)
        else:
            raise SheetsClientError(f"unknown op: {job.op}")

    async def _requeue_after(self, job: SyncJob, delay: float) -> None:
        await asyncio.sleep(delay)
        if self._queue is not None:
            await self._queue.put(job)

    # ---------- WAL persistence ----------
    def _persist(self, job: SyncJob) -> None:
        with self._wal_lock:
            with self.pending_path.open("a", encoding="utf-8") as fh:
                fh.write(job.to_json() + "\n")

    def _clear_pending(self, job: SyncJob) -> None:
        """Rewrite pending WAL without the matching job line.

        Idempotent: if nothing matches, no-op.
        """
        if not self.pending_path.exists():
            return
        with self._wal_lock:
            surviving: list[str] = []
            target_key = (job.tab, job.op, job.key_value, job.enqueued_at)
            with self.pending_path.open("r", encoding="utf-8") as fh:
                for line in fh:
                    stripped = line.strip()
                    if not stripped:
                        continue
                    try:
                        parsed = SyncJob.from_json(stripped)
                    except (json.JSONDecodeError, TypeError):
                        continue
                    if (
                        parsed.tab,
                        parsed.op,
                        parsed.key_value,
                        parsed.enqueued_at,
                    ) == target_key:
                        continue
                    surviving.append(stripped)
            tmp = self.pending_path.with_suffix(".jsonl.tmp")
            tmp.write_text("\n".join(surviving) + ("\n" if surviving else ""), encoding="utf-8")
            tmp.replace(self.pending_path)

    def _load_pending(self) -> list[SyncJob]:
        if not self.pending_path.exists():
            return []
        out: list[SyncJob] = []
        with self.pending_path.open("r", encoding="utf-8") as fh:
            for line in fh:
                stripped = line.strip()
                if not stripped:
                    continue
                try:
                    out.append(SyncJob.from_json(stripped))
                except (json.JSONDecodeError, TypeError):
                    logger.warning("skipping malformed sheets_pending line")
        return out

    def _count_pending(self) -> int:
        if not self.pending_path.exists():
            return 0
        with self.pending_path.open("r", encoding="utf-8") as fh:
            return sum(1 for line in fh if line.strip())

    def _to_deadletter(self, job: SyncJob) -> None:
        with self._wal_lock:
            with self.deadletter_path.open("a", encoding="utf-8") as fh:
                fh.write(job.to_json() + "\n")


# ---------- LedgerWriter observer wiring ----------
def register_sheets_observer(
    ledger: LedgerWriter, sync: SyncQueue
) -> Callable[[ChangeEvent], None]:
    """Register a post-commit callback that enqueues a Sheets sync job.

    - Non-syncable tables are dropped by the queue.
    - Observer NEVER raises (LedgerWriter._notify swallows, but we also defend).

    Returns the registered callback so callers can deregister it via
    `ledger.off_change(cb)` during hot-reload.
    """

    def observer(event: ChangeEvent) -> None:
        table = event.get("table")
        if not table or table not in SYNCABLE_TABLES:
            return
        op = event.get("op")
        pk_col = event.get("pk_column") or "id"
        pk_value = event.get("pk_value")

        if op == "append" or op == "update":
            row = event.get("row")
            if not row or pk_value is None:
                return
            sync.enqueue_upsert(table, str(pk_value), dict(row), pk_col)
        elif op == "delete":
            if pk_value is None:
                return
            sync.enqueue_delete(table, str(pk_value), pk_col)
        elif op == "delete_where":
            # Bulk delete — upstream caller should run /sync-all to reconcile.
            logger.info("delete_where on %s — run /api/sheets/sync-all to reconcile", table)

    ledger.on_change(observer)
    return observer


def build_tab_headers(data_dir: Path, table: str) -> list[str]:
    """Headers = built-in schema columns + user columns from the registry."""
    schema = SCHEMAS[table]
    builtin = list(schema["columns"])
    try:
        user_cols = [
            c["key"] for c in resolve_columns(data_dir, table) if not c.get("builtin", False)
        ]
    except Exception:  # noqa: BLE001 - tolerate missing registry in tests
        user_cols = []
    return [*builtin, *user_cols]
