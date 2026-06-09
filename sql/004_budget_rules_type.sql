-- 004_budget_rules_type.sql — add Need/Want/Investment type to budget rules.
-- Run once in Supabase SQL Editor (or via psql) after 003_*.sql.
--
-- A custom-tag rule (category is a bare tag like "Gym") carries its parent type
-- here so the grouped Table C view can roll it up and the LLM can auto-apply it.
-- Built-in "Type, Category" rules leave this NULL (their type is the prefix).
-- This is also where tag types live in supabase mode, since uniques.json is on
-- the read-only Vercel filesystem and can't persist them.

ALTER TABLE budget_rules ADD COLUMN IF NOT EXISTS type TEXT;
