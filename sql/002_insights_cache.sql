-- FinEye — Insights cache (AI Monthly Briefing + Ask-Your-Ledger chat)
-- Run after 001_schema.sql. Stores narration JSON keyed by sha256(canonicalized bundle).
-- See docs/PLAN-ai-briefing-and-rag.md §9.2 for the contract.
--
-- NOTE: column name is `owner_id` (not `user_id` like other tables) and types are
-- `TEXT` / `TIMESTAMPTZ` to match the Python schema in app/storage/schemas.py
-- (INSIGHTS_CACHE) verbatim. Phase 2 RLS will be wired alongside the rest of the
-- schema; intentionally omitted here for parity with 001_schema.sql.

-- ── insights_cache ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS insights_cache (
    id            TEXT PRIMARY KEY,
    owner_id      TEXT NOT NULL,
    kind          TEXT NOT NULL CHECK (kind IN ('monthly_briefing','chat_answer')),
    key_hash      TEXT NOT NULL,
    payload_json  TEXT NOT NULL,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT now(),
    expires_at    TIMESTAMPTZ NOT NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_insights_cache_owner_kind_hash
    ON insights_cache (owner_id, kind, key_hash);

CREATE INDEX IF NOT EXISTS idx_insights_cache_expires
    ON insights_cache (expires_at);

-- ── Row Level Security (Phase 2 — enable when Supabase Auth is wired up) ────
-- ALTER TABLE insights_cache ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "own rows" ON insights_cache USING (owner_id = auth.uid()::text);
