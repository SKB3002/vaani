"""Insights cache — narration cache for monthly briefings and chat answers.

Dual-mode: CSV (via LedgerWriter) locally; Supabase upsert in cloud mode.
The cache is mode-agnostic at this layer because every read/write goes
through ``LedgerWriter``, which already short-circuits to the right backend.

Cache key namespacing
---------------------
The persisted ``key_hash`` is a composite hash of (kind, bundle_hash,
month, prompt_version, model). Without this composition, two empty months
would collide on ``bundle_hash`` alone and the wrong narration could be
served. ``compute_cache_key`` is the single function callers use — never
hash by hand.

Invalidation
------------
``make_invalidator`` produces a ``ChangeCallback`` for
``LedgerWriter.on_change``. The callback never raises (the ledger swallows
observer errors anyway, but we are belt-and-braces here). It walks an
allowlist of bundle-affecting tables and is otherwise a no-op:

- ``insights_cache`` events       → no-op (avoid infinite loop)
- ``expenses`` events             → invalidate that month's briefing if we
                                     can determine the month from the row;
                                     otherwise invalidate all briefings.
                                     Always invalidate all chat answers.
- Other allowlist tables          → invalidate all briefings + all chats.
- Anything else                   → no-op.

TTL
---
Each row stores ``expires_at = created_at + ttl_days``. ``get`` checks the
timestamp and returns ``None`` for expired rows; ``prune_expired`` is a
manual maintenance hook that hard-deletes them. We do NOT prune
automatically — that decision belongs to a later admin/cron task.
"""
from __future__ import annotations

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import pandas as pd
import ulid

from app.services.ledger import ChangeCallback, ChangeEvent, LedgerWriter

log = logging.getLogger("vaani.insights.cache")

CacheKind = Literal["monthly_briefing", "chat_answer"]

# Prompt-version constants. Bump these when the corresponding system prompt
# template changes — the cache key picks them up and old narrations become
# unreachable without an explicit flush.
PROMPT_VERSION_MONTHLY_BRIEFING: str = "v1"
PROMPT_VERSION_CHAT_ANSWER: str = "v1"

# Tables whose mutations should invalidate the cache. Anything outside this
# set is a no-op for the invalidator — derived state (e.g. ``budget_state``
# or ``budget_adjustments``) is recomputed from these primary tables, and
# ``balances`` / ``drafts`` do not enter the bundle at all.
BUNDLE_AFFECTING_TABLES: frozenset[str] = frozenset(
    {
        "expenses",
        "budget_rules",
        "budget_table_c",
        "wishlist",
        "investments",
    }
)

_CACHE_TABLE: str = "insights_cache"


# ---------------------------------------------------------------------------
# Cache key
# ---------------------------------------------------------------------------


def compute_cache_key(
    *,
    kind: CacheKind,
    bundle_hash_value: str,
    month: str,
    prompt_version: str,
    model: str,
) -> str:
    """Compose the storage ``key_hash`` for a cache row.

    Why composite?
    - ``bundle_hash`` alone collides for two empty months.
    - Different ``kind`` values must never share a row.
    - ``prompt_version`` lets us flush all narrations by bumping the prompt.
    - ``model`` segments rows so a model swap regenerates without flushes.
    """
    payload = {
        "kind": kind,
        "bundle_hash": bundle_hash_value,
        "month": month,
        "prompt_version": prompt_version,
        "model": model,
    }
    canonical = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------------------
# InsightsCache
# ---------------------------------------------------------------------------


class InsightsCache:
    """Narration cache — opaque payload storage keyed by (owner, kind, hash).

    The cache does NOT enforce a schema on ``payload`` — it is JSON-encoded
    and round-tripped as-is. The narrator (briefing) and chat orchestrator
    each own their own payload shape.
    """

    def __init__(
        self,
        ledger: LedgerWriter,
        *,
        ttl_days: int,
        owner_id: str,
    ) -> None:
        self._ledger = ledger
        self._ttl = timedelta(days=int(ttl_days))
        # Retained as a fallback only — actual scoping is per-request via the
        # contextvar (see ``_owner``). Tests that need a fixed owner can rely
        # on this default by never setting the contextvar.
        self._default_owner_id = owner_id

    @property
    def _owner_id(self) -> str:
        """Resolve the active owner id, preferring the request-scoped value."""
        from app.context import current_user_id

        uid = current_user_id()
        return uid or self._default_owner_id

    # ---- read --------------------------------------------------------------

    def get(self, *, kind: CacheKind, key_hash: str) -> dict[str, Any] | None:
        """Return the deserialised payload for a cache row, or ``None``.

        Returns ``None`` for misses AND for rows whose ``expires_at`` is in
        the past — stale rows are not surfaced to callers. Pruning the row
        from disk is the job of ``prune_expired``.
        """
        row = self._find_row(kind=kind, key_hash=key_hash)
        if row is None:
            return None
        if self._is_expired(row.get("expires_at")):
            return None
        payload_json = row.get("payload_json")
        if not payload_json:
            return None
        try:
            parsed: dict[str, Any] = json.loads(str(payload_json))
        except (TypeError, ValueError):
            log.warning(
                "insights_cache: malformed payload_json for kind=%s key_hash=%s",
                kind,
                key_hash[:12],
            )
            return None
        return parsed

    # ---- write -------------------------------------------------------------

    def put(
        self,
        *,
        kind: CacheKind,
        key_hash: str,
        payload: dict[str, Any],
    ) -> None:
        """Upsert a cache row.

        We do not have a true upsert in ``LedgerWriter`` (the API is append /
        update / delete by PK). The unique constraint here is composite
        (``owner_id``, ``kind``, ``key_hash``) — none of those are the PK.
        So we emulate upsert as: read → filter for collisions → delete each
        by PK → append the new row. Both csv and supabase modes flow through
        the ledger so observers fire correctly in both.
        """
        # Drop any existing row(s) with the same logical key. Multiple
        # collisions shouldn't happen (the ledger is single-writer per
        # table) but we tolerate them to be safe.
        for existing in self._matching_pks(kind=kind, key_hash=key_hash):
            self._ledger.delete(_CACHE_TABLE, existing)

        now = datetime.now(UTC)
        row: dict[str, Any] = {
            "id": str(ulid.new()),
            "owner_id": self._owner_id,
            "kind": kind,
            "key_hash": key_hash,
            "payload_json": json.dumps(payload, separators=(",", ":")),
            "created_at": now.isoformat(),
            "expires_at": (now + self._ttl).isoformat(),
        }
        self._ledger.append(_CACHE_TABLE, row)

    # ---- invalidation ------------------------------------------------------

    def invalidate_month(self, *, month: str) -> int:
        """Delete all ``monthly_briefing`` rows referencing ``month``.

        We can't reverse-engineer the month from ``key_hash`` (it is opaque
        by design), so we substring-match the month token against
        ``payload_json``. The narrator payload always embeds ``"month":
        "YYYY-MM"`` so the match is reliable in practice; if a future
        payload omits it, this becomes a no-op for that row and the next
        bundle-wide invalidation will catch it.
        """
        df = self._owner_rows(kind="monthly_briefing")
        if df.empty:
            return 0
        token = f'"{month}"'
        matches = df[
            df["payload_json"].fillna("").astype("string").str.contains(token, regex=False)
        ]
        return self._delete_pks(matches)

    def invalidate_all_briefings(self) -> int:
        """Delete every ``monthly_briefing`` row for this owner."""
        df = self._owner_rows(kind="monthly_briefing")
        return self._delete_pks(df)

    def invalidate_all_chats(self) -> int:
        """Delete every ``chat_answer`` row for this owner."""
        df = self._owner_rows(kind="chat_answer")
        return self._delete_pks(df)

    def invalidate_all(self) -> int:
        """Delete every cache row for this owner (briefings + chats)."""
        df = self._owner_rows()
        return self._delete_pks(df)

    # ---- maintenance -------------------------------------------------------

    def prune_expired(self) -> int:
        """Hard-delete rows whose ``expires_at`` is in the past.

        Maintenance hook only — the engine never calls this automatically.
        """
        df = self._owner_rows()
        if df.empty:
            return 0
        now_iso = datetime.now(UTC).isoformat()
        expired = df[
            df["expires_at"].fillna("").astype("string") < now_iso
        ]
        return self._delete_pks(expired)

    # ---- internals ---------------------------------------------------------

    def _is_expired(self, expires_at: Any) -> bool:
        """Return True if ``expires_at`` is in the past or unreadable."""
        if expires_at is None:
            return True
        try:
            text = str(expires_at)
        except (TypeError, ValueError):
            return True
        if not text or text.lower() == "nan":
            return True
        try:
            exp_dt = datetime.fromisoformat(text)
        except ValueError:
            return True
        if exp_dt.tzinfo is None:
            exp_dt = exp_dt.replace(tzinfo=UTC)
        return exp_dt <= datetime.now(UTC)

    def _owner_rows(self, *, kind: CacheKind | None = None) -> pd.DataFrame:
        """Read cache rows scoped to this owner, optionally filtered by kind."""
        df = self._ledger.read(_CACHE_TABLE)
        if df.empty:
            return df
        owner_mask = df["owner_id"].fillna("").astype("string") == str(self._owner_id)
        df = df[owner_mask]
        if kind is not None:
            kind_mask = df["kind"].fillna("").astype("string") == str(kind)
            df = df[kind_mask]
        return df

    def _find_row(
        self, *, kind: CacheKind, key_hash: str
    ) -> dict[str, Any] | None:
        df = self._owner_rows(kind=kind)
        if df.empty:
            return None
        hash_mask = df["key_hash"].fillna("").astype("string") == str(key_hash)
        matches = df[hash_mask]
        if matches.empty:
            return None
        # If multiple rows somehow share the key (shouldn't happen post-put),
        # prefer the most recently created one.
        if "created_at" in matches.columns:
            matches = matches.sort_values("created_at", ascending=False)
        result: dict[str, Any] = matches.iloc[0].to_dict()
        return result

    def _matching_pks(self, *, kind: CacheKind, key_hash: str) -> list[str]:
        df = self._owner_rows(kind=kind)
        if df.empty:
            return []
        hash_mask = df["key_hash"].fillna("").astype("string") == str(key_hash)
        ids = df.loc[hash_mask, "id"].dropna().astype("string").tolist()
        return [str(i) for i in ids]

    def _delete_pks(self, df: pd.DataFrame) -> int:
        if df.empty or "id" not in df.columns:
            return 0
        ids = df["id"].dropna().astype("string").tolist()
        deleted = 0
        for pk in ids:
            if not pk:
                continue
            if self._ledger.delete(_CACHE_TABLE, str(pk)):
                deleted += 1
        return deleted


# ---------------------------------------------------------------------------
# Observer factory
# ---------------------------------------------------------------------------


def make_invalidator(cache: InsightsCache) -> ChangeCallback:
    """Build a ``ChangeCallback`` that invalidates the narration cache.

    The callback NEVER raises back into the writer — observer errors are
    already swallowed by ``LedgerWriter._notify``, but we add a defensive
    try/except here so the ledger doesn't even have to log.
    """

    def _invalidate(event: ChangeEvent) -> None:
        try:
            _dispatch(cache, event)
        except Exception:  # noqa: BLE001 — observers must never raise
            log.exception("insights_cache invalidator failed: %s", event.get("table"))

    return _invalidate


def _dispatch(cache: InsightsCache, event: ChangeEvent) -> None:
    table = str(event.get("table") or "")
    # Avoid infinite loops — our own writes mustn't re-trigger invalidation.
    if table == _CACHE_TABLE:
        return
    if table not in BUNDLE_AFFECTING_TABLES:
        return

    if table == "expenses":
        month = _extract_month(event)
        if month is not None:
            cache.invalidate_month(month=month)
        else:
            # Fallback when the event doesn't carry a row (e.g. delete_where
            # or a row missing the date field) — be conservative.
            cache.invalidate_all_briefings()
        # Chat answers can span any time range; we cannot be surgical.
        cache.invalidate_all_chats()
        return

    # Bundle-wide tables — every cached briefing and chat answer is suspect.
    cache.invalidate_all_briefings()
    cache.invalidate_all_chats()


def _extract_month(event: ChangeEvent) -> str | None:
    """Best-effort extraction of ``YYYY-MM`` from an expense change event."""
    row = event.get("row")
    if not isinstance(row, dict):
        return None
    raw_date = row.get("date")
    if raw_date is None:
        return None
    try:
        text = str(raw_date)
    except (TypeError, ValueError):
        return None
    # Accept ``YYYY-MM-DD`` or anything that pandas can parse (covers ISO
    # timestamps written by importers).
    if len(text) >= 7 and text[4] == "-":
        candidate = text[:7]
        if candidate[:4].isdigit() and candidate[5:7].isdigit():
            return candidate
    try:
        parsed = pd.to_datetime(text, errors="raise")
    except (ValueError, TypeError):
        return None
    if pd.isna(parsed):
        return None
    return f"{parsed.year:04d}-{parsed.month:02d}"


__all__ = [
    "BUNDLE_AFFECTING_TABLES",
    "CacheKind",
    "InsightsCache",
    "PROMPT_VERSION_CHAT_ANSWER",
    "PROMPT_VERSION_MONTHLY_BRIEFING",
    "compute_cache_key",
    "make_invalidator",
]
