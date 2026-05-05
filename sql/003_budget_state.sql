-- 003_budget_state.sql — running-state engine tables (PR1 of budget-redesign).
-- Run once in Supabase SQL Editor (or via psql) after 001_schema.sql.

-- ── budget_state ─────────────────────────────────────────────────────────────
-- Per-category running pool. One row per budget_rules.category. Persists
-- across months; lazy month rollover updates `current_budget` and bumps
-- `last_rolled_month` on the next recompute.
CREATE TABLE IF NOT EXISTS budget_state (
    user_id            UUID NOT NULL,
    category           TEXT NOT NULL,
    current_budget     NUMERIC(12,2) DEFAULT 0,
    last_rolled_month  TEXT,
    updated_at         TEXT,
    PRIMARY KEY (user_id, category)
);

CREATE INDEX IF NOT EXISTS idx_budget_state_user ON budget_state (user_id);

-- ── budget_adjustments ───────────────────────────────────────────────────────
-- Audit log for the Add / Set buttons (PR2). Append-only.
CREATE TABLE IF NOT EXISTS budget_adjustments (
    id          TEXT PRIMARY KEY,
    user_id     UUID NOT NULL,
    timestamp   TEXT,
    category    TEXT,
    amount      NUMERIC(12,2),
    kind        TEXT,
    note        TEXT
);

CREATE INDEX IF NOT EXISTS idx_budget_adjustments_user ON budget_adjustments (user_id);
CREATE INDEX IF NOT EXISTS idx_budget_adjustments_category ON budget_adjustments (user_id, category);

-- Phase 2 — RLS (uncomment when Supabase Auth is wired up)
-- ALTER TABLE budget_state         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE budget_adjustments   ENABLE ROW LEVEL SECURITY;
-- CREATE POLICY "own rows" ON budget_state       USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON budget_adjustments USING (user_id = auth.uid());
