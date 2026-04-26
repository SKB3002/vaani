-- FinEye — Supabase schema
-- Run this once in Supabase SQL Editor (or via psql).
-- Every table mirrors the CSV schema exactly, plus user_id for multi-user RLS.
-- Phase 1: user_id is always the owner UUID from FINEYE_OWNER_ID.
-- Phase 2: user_id will be auth.uid() from Supabase Auth.

-- ── enable pgcrypto for gen_random_uuid() ───────────────────────────────────
CREATE EXTENSION IF NOT EXISTS pgcrypto;

-- ── expenses ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS expenses (
    id                  TEXT PRIMARY KEY,
    user_id             UUID NOT NULL,
    date                TEXT NOT NULL,
    created_at          TEXT,
    expense_name        TEXT NOT NULL,
    type_category       TEXT,
    payment_method      TEXT,
    paid_for_someone    BOOLEAN DEFAULT FALSE,
    paid_by_someone     BOOLEAN DEFAULT FALSE,
    person_name         TEXT,
    amount              NUMERIC(12,2) NOT NULL,
    cash_balance_after  NUMERIC(12,2) DEFAULT 0,
    online_balance_after NUMERIC(12,2) DEFAULT 0,
    source              TEXT,
    raw_transcript      TEXT,
    notes               TEXT,
    import_batch_id     TEXT,
    custom_tag          TEXT,
    paid_for_method     TEXT,
    adjustment_type     TEXT
);

CREATE INDEX IF NOT EXISTS idx_expenses_user_date ON expenses (user_id, date DESC);

-- ── balances ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS balances (
    asof            TEXT NOT NULL,
    user_id         UUID NOT NULL,
    cash_balance    NUMERIC(12,2) DEFAULT 0,
    online_balance  NUMERIC(12,2) DEFAULT 0,
    reason          TEXT,
    PRIMARY KEY (user_id, asof)
);

-- ── investments ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS investments (
    month               TEXT NOT NULL,
    user_id             UUID NOT NULL,
    long_term           NUMERIC(12,2),
    mid_long_term       NUMERIC(12,2),
    emergency_fund      NUMERIC(12,2),
    bike_savings_wants  NUMERIC(12,2),
    misc_spend_save     NUMERIC(12,2),
    fixed_deposits      NUMERIC(12,2),
    total               NUMERIC(12,2),
    import_batch_id     TEXT,
    PRIMARY KEY (user_id, month)
);

-- ── wishlist ────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS wishlist (
    id              TEXT PRIMARY KEY,
    user_id         UUID NOT NULL,
    item            TEXT NOT NULL,
    target_amount   NUMERIC(12,2),
    saved_so_far    NUMERIC(12,2) DEFAULT 0,
    priority        TEXT,
    notes           TEXT,
    link            TEXT,
    source          TEXT,
    created_at      TEXT,
    status          TEXT DEFAULT 'active',
    import_batch_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_wishlist_user ON wishlist (user_id);

-- ── goals_a ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS goals_a (
    goal_id             TEXT PRIMARY KEY,
    user_id             UUID NOT NULL,
    goal_name           TEXT NOT NULL,
    target_amount       NUMERIC(12,2),
    current_amount      NUMERIC(12,2) DEFAULT 0,
    monthly_contribution NUMERIC(12,2) DEFAULT 0,
    pct_complete        NUMERIC(6,2) DEFAULT 0,
    months_left         INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'active',
    import_batch_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_goals_a_user ON goals_a (user_id);

-- ── goals_b ─────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS goals_b (
    goal_id             TEXT PRIMARY KEY,
    user_id             UUID NOT NULL,
    goal_name           TEXT NOT NULL,
    target_amount       NUMERIC(12,2),
    manual_saved        NUMERIC(12,2) DEFAULT 0,
    auto_added          NUMERIC(12,2) DEFAULT 0,
    total_saved         NUMERIC(12,2) DEFAULT 0,
    monthly_contribution NUMERIC(12,2) DEFAULT 0,
    pct_complete        NUMERIC(6,2) DEFAULT 0,
    months_left         INTEGER DEFAULT 0,
    status              TEXT DEFAULT 'active',
    import_batch_id     TEXT
);

CREATE INDEX IF NOT EXISTS idx_goals_b_user ON goals_b (user_id);

-- ── budget_rules ─────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budget_rules (
    category        TEXT NOT NULL,
    user_id         UUID NOT NULL,
    monthly_budget  NUMERIC(12,2),
    carry_cap       NUMERIC(12,2),
    priority        INTEGER,
    PRIMARY KEY (user_id, category)
);

-- ── budget_table_c ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS budget_table_c (
    month           TEXT NOT NULL,
    user_id         UUID NOT NULL,
    category        TEXT NOT NULL,
    budget          NUMERIC(12,2),
    actual          NUMERIC(12,2),
    remaining       NUMERIC(12,2),
    carry_buffer    NUMERIC(12,2),
    overflow        NUMERIC(12,2),
    to_medical      NUMERIC(12,2),
    to_emergency    NUMERIC(12,2),
    med_balance     NUMERIC(12,2),
    emerg_balance   NUMERIC(12,2),
    notes           TEXT,
    PRIMARY KEY (user_id, month, category)
);

-- ── drafts ───────────────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS drafts (
    id              TEXT PRIMARY KEY,
    user_id         UUID NOT NULL,
    target_table    TEXT,
    row_json        TEXT,
    errors          TEXT,
    source_filename TEXT,
    created_at      TEXT,
    import_batch_id TEXT
);

CREATE INDEX IF NOT EXISTS idx_drafts_user ON drafts (user_id);

-- ── Row Level Security (Phase 2 — enable when Supabase Auth is wired up) ────
-- Uncomment these when you add Supabase Auth in Phase 2.
-- ALTER TABLE expenses        ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE balances        ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE investments     ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE wishlist        ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE goals_a         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE goals_b         ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE budget_rules    ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE budget_table_c  ENABLE ROW LEVEL SECURITY;
-- ALTER TABLE drafts          ENABLE ROW LEVEL SECURITY;
--
-- CREATE POLICY "own rows" ON expenses        USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON balances        USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON investments     USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON wishlist        USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON goals_a         USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON goals_b         USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON budget_rules    USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON budget_table_c  USING (user_id = auth.uid());
-- CREATE POLICY "own rows" ON drafts          USING (user_id = auth.uid());
