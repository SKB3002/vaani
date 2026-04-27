"""Write-Ahead Log for crash-safe CSV mutations."""
from __future__ import annotations

import json
import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import ulid


@dataclass
class WalEntry:
    entry_id: str
    table: str
    op: str  # "append" | "update" | "delete"
    row: dict[str, Any]
    ts: float

    def to_json(self) -> str:
        return json.dumps(
            {
                "entry_id": self.entry_id,
                "table": self.table,
                "op": self.op,
                "row": self.row,
                "ts": self.ts,
            },
            default=str,
        )

    @classmethod
    def from_json(cls, line: str) -> WalEntry:
        data = json.loads(line)
        return cls(
            entry_id=data["entry_id"],
            table=data["table"],
            op=data["op"],
            row=data["row"],
            ts=data["ts"],
        )


class WriteAheadLog:
    """Append-only JSONL WAL with applied-ids tracking.

    Each mutation:
      1. Call append(table, op, row) → entry persisted & fsynced → returns entry_id.
      2. Caller performs the actual CSV write.
      3. Caller calls clear(entry_id) on success → entry marked applied.

    On startup, replay_unfinished(handler) re-invokes the handler for any entries
    that were written but never cleared, in order. The handler is expected to be
    idempotent because entry_ids persist in .wal_applied.jsonl.
    """

    def __init__(self, wal_dir: str | Path, *, create_dirs: bool = True) -> None:
        self.wal_dir = Path(wal_dir)
        if create_dirs:
            self.wal_dir.mkdir(parents=True, exist_ok=True)
        self.log_path = self.wal_dir / "wal.jsonl"
        self.applied_path = self.wal_dir / "wal_applied.jsonl"
        self._lock = threading.Lock()

    def append(self, table: str, op: str, row: dict[str, Any]) -> WalEntry:
        entry = WalEntry(
            entry_id=str(ulid.new()),
            table=table,
            op=op,
            row=row,
            ts=time.time(),
        )
        with self._lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(entry.to_json() + "\n")
            f.flush()
            os.fsync(f.fileno())
        return entry

    def clear(self, entry_id: str) -> None:
        with self._lock, open(self.applied_path, "a", encoding="utf-8") as f:
            f.write(entry_id + "\n")
            f.flush()
            os.fsync(f.fileno())

    def _applied_ids(self) -> set[str]:
        if not self.applied_path.exists():
            return set()
        with open(self.applied_path, encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}

    def pending(self) -> list[WalEntry]:
        if not self.log_path.exists():
            return []
        applied = self._applied_ids()
        out: list[WalEntry] = []
        with open(self.log_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = WalEntry.from_json(line)
                except json.JSONDecodeError:
                    continue
                if entry.entry_id not in applied:
                    out.append(entry)
        return out

    def replay_unfinished(self, handler: Any) -> int:
        """Call handler(entry) for each pending entry. Returns count replayed."""
        count = 0
        for entry in self.pending():
            handler(entry)
            self.clear(entry.entry_id)
            count += 1
        return count

    def compact(self) -> None:
        """Optional: rewrite wal.jsonl keeping only non-applied entries."""
        with self._lock:
            pending = self.pending()
            tmp = self.log_path.with_suffix(".tmp")
            with open(tmp, "w", encoding="utf-8") as f:
                for entry in pending:
                    f.write(entry.to_json() + "\n")
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.log_path)
            # Reset applied ledger since wal is clean
            if self.applied_path.exists():
                self.applied_path.unlink()
