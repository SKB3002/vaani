-- Vaani — users table for multi-user mode.
-- Run once after 001_schema.sql and 002_insights_cache.sql.
--
-- Every data table already carries a `user_id UUID NOT NULL`. This table is
-- the source of those ids when FINEYE_MULTI_USER=true. Single-user mode keeps
-- using FINEYE_OWNER_ID and ignores this table.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email           TEXT NOT NULL UNIQUE,
    password_hash   TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    consent_at      TIMESTAMPTZ
);

-- Case-insensitive uniqueness — we lowercase in the app layer already, but
-- a partial index belt-and-braces against a future caller skipping that.
CREATE UNIQUE INDEX IF NOT EXISTS idx_users_email_lower
    ON users (LOWER(email));
