# FinEye — Cloud BYOK Production Plan

> Plan file: `docs/PLAN-fineye-cloud-byok.md`
> Date: 2026-04-25
> Mode: PLANNING ONLY — no code written

---

## Executive Summary

FinEye transitions from a single-user local CSV tool to a production-ready, multi-user SaaS by layering three capabilities in strict order: (1) a lightweight auth layer using Supabase Auth so multiple users can self-host or share a hosted instance, each with complete data isolation enforced via Postgres Row-Level Security; (2) BYOK (Bring Your Own Key) Supabase integration where users supply their own Supabase project credentials through a Settings UI, keeping operator and user data fully segregated with zero shared database risk; and (3) Google Sheets sync that reuses the existing gspread-based SheetsClient and async sync worker, extended to accept OAuth2 credentials uploaded by the user at runtime. The existing WAL + atomic CSV write path is preserved unchanged as an offline fallback and local truth store, so the app remains fully functional with no network.

---

## Project Type

**BACKEND + WEB** — FastAPI backend, Vanilla JS / Jinja2 frontend, no mobile layer.

**Primary agents:** `backend-specialist`, `database-architect`, `security-auditor`, `frontend-specialist`

---

## Success Criteria

| Criteria | Measurable |
|----------|-----------|
| Multi-user isolation | Two users in hosted mode see zero rows from each other |
| BYOK Supabase | User pastes URL + anon key in Settings; app writes to their Postgres within 60 s |
| Offline fallback | All CRUD works with `FINEYE_SUPABASE_ENABLED=false`; no errors thrown |
| Google Sheets sync | Existing CSV data appears in user's Sheet within 5 min of migration trigger |
| Zero data loss | No row lost during dual-write failure (WAL replay covers the gap) |
| Migration | CLI script pushes all existing CSV rows to Supabase with idempotent upserts |
| Auth friction | New user registers and reaches dashboard in under 90 seconds |

---

## Architecture Decision Records

### ADR-A: BYOK Supabase Credential Storage

**Decision:** Settings page UI + encrypted storage in `data/meta.json`

**Options considered:**

| Option | Pros | Cons | Verdict |
|--------|------|------|---------|
| Env vars only | Simple, no UI needed | Requires restart on change; hard for hosted SaaS | Rejected for hosted mode |
| `.env` file the user edits | Familiar for devs | Not usable for non-technical users; file access needed | Acceptable for pure self-hosted only |
| Settings page + `meta.json` (encrypted) | Works for both self-hosted and SaaS; live reload | Must encrypt key at rest; slightly more code | **Chosen** |

**Implementation detail:**
- The Settings page gains a "Cloud Storage" tab with fields: Supabase URL, Anon Key, Service Role Key (optional, only for migrations).
- Keys are encrypted with `cryptography.fernet.Fernet` before writing to `data/meta.json` under the key `supabase_credentials`.
- The Fernet secret is derived from an `FINEYE_SECRET_KEY` environment variable (mandatory for cloud deployments; auto-generated for local installs on first run).
- On read, the app decrypts at startup and keeps the Supabase client in-process; no plain-text key is ever served over the API.
- The Service Role Key is only ever needed for the one-time migration CLI (`scripts/migrate_csv_to_supabase.py`) and is NOT stored by default.

**Fallback:** If no Supabase credentials are configured, the app operates in local-CSV-only mode. No error is raised; all Supabase code paths are behind a `is_supabase_configured()` guard in `app/deps.py`.

---

### ADR-B: Supabase Schema Design

**Decision:** Mirror existing CSV tables as Postgres tables with `user_id UUID` column + Row-Level Security on every table.

**Rationale:** Column names in `app/storage/schemas.py` become SQL columns directly. This keeps the data model consistent across storage backends and makes the migration script trivial. A single `user_id` foreign key references `auth.users(id)` (Supabase's built-in auth table); RLS policies enforce that `user_id = auth.uid()` on all operations.

**Key schema rules:**
- Every table gains `user_id UUID NOT NULL REFERENCES auth.users(id)`.
- Primary keys match the existing CSV `pk` field (e.g., `id` for expenses, `month` for investments). The composite unique key is `(user_id, pk_column)`.
- Tables that use non-UUID PKs (e.g., `month` in `investments`, `asof` in `balances`) get a surrogate `_row_id UUID DEFAULT gen_random_uuid()` as the physical primary key; the original column remains the business key with a `UNIQUE(user_id, month)` constraint.
- All columns are `TEXT` or `NUMERIC(18,4)` or `BOOLEAN`, matching CSV dtypes exactly.
- `TIMESTAMPTZ created_at DEFAULT now()` is added to all tables for Supabase's built-in audit.

**RLS policy model:**
```sql
-- Pattern applied to every table:
ALTER TABLE expenses ENABLE ROW LEVEL SECURITY;

CREATE POLICY "user owns rows"
  ON expenses
  USING (user_id = auth.uid())
  WITH CHECK (user_id = auth.uid());
```

**What happens if a user's anon key leaks:**
- The anon key alone cannot bypass RLS. It can only read/write rows where `user_id = auth.uid()`.
- An attacker with just the anon key and no valid JWT cannot authenticate; they get zero rows.
- The service role key bypasses RLS and must never be stored in the app or sent to the browser. It is only used in the one-time migration CLI, run locally.
- Recommendation: document that users should rotate their Supabase anon key if compromised; the Settings page makes re-entry trivial.

---

### ADR-C: Write Strategy

**Decision:** Dual-write — CSV-primary (WAL + atomic write) then async Supabase push via existing observer pattern.

**Options considered:**

| Strategy | Data Safety | Complexity | Offline Support | Verdict |
|----------|------------|-----------|-----------------|---------|
| CSV-primary + async background sync | High (WAL covers gap) | Low (reuses existing observer) | Full | **Chosen** |
| Supabase-primary + CSV cache | Medium (depends on network) | High | Poor | Rejected |
| Dual-write synchronous | High | Medium | None (blocks on network) | Rejected |

**How it works:**
1. Every write goes through `LedgerWriter` exactly as today (WAL entry + atomic CSV write).
2. The existing `ChangeCallback` observer mechanism in `LedgerWriter.on_change()` fires a post-commit notification.
3. A new `SupabaseSyncWorker` (modeled after the existing `sheets/sync_worker.py`) picks up the change event and pushes the row to Supabase asynchronously.
4. The Supabase push uses a `sheets_pending.jsonl`-style queue file (`wal/supabase_pending.jsonl`) for durability. If the push fails, the WAL entry is replayed on next startup.
5. The CSV file is always the source of truth for reads in offline mode. When Supabase is configured, reads can optionally go to Supabase (Phase 3 decision); in Phase 1, reads remain CSV-only.

**Zero-data-loss guarantee:** The WAL entry is written and fsynced before the CSV write. If the Supabase push never succeeds, the pending WAL file retains the event. On reconnect, the worker drains the queue.

---

### ADR-D: Google Sheets Integration

**Decision:** Keep existing gspread service-account JSON flow; add OAuth2 path as Phase 2 enhancement. Sync direction: push-only (CSV → Sheets) with optional bidirectional pull for import flow.

**What already exists:**
- `app/services/sheets/client.py` — SheetsClient with `upsert_row`, `batch_upsert`, `read_all`.
- `app/services/sheets/sync_worker.py` — async queue with dead-letter and WAL-backed durability.
- `app/services/sheets/integrations_store.py` — stores credentials path + spreadsheet ID.
- Tables synced: `expenses`, `balances`, `wishlist`, `goals_a`, `goals_b`, `budget_rules`, `budget_table_c`, `investments`.

**What changes for multi-user:**
- Credentials are scoped per `user_id`. The `integrations_store` must save per-user credential paths (or the credentials blob itself encrypted in meta.json).
- In hosted SaaS mode, each user uploads their `credentials.json` via the existing `/api/sheets/credentials` endpoint; the file is stored under `data/{user_id}/credentials.json` and never shared.
- OAuth2 path (Phase 2): add a `/api/sheets/oauth/start` → `/api/sheets/oauth/callback` flow using `google-auth-oauthlib`. This removes the need for users to create a service account.

**Bidirectional sync scope:** Pull (Sheets → CSV) only applies to the import flow (existing CSV import logic). A dedicated "Pull from Sheets" button triggers `SheetsClient.read_all()` → runs through the existing import mapper + dedup. This is Phase 2.

---

### ADR-E: Auth Layer

**Decision:** Supabase Auth (magic link + optional Google OAuth) for hosted SaaS; bypass-able for pure self-hosted single-user installs.

**Options considered:**

| Option | Friction | Multi-user | Self-hosted | Verdict |
|--------|---------|------------|-------------|---------|
| No auth — single-user local | None | No | Yes | Acceptable as default local mode |
| PIN/password in meta.json | Low | No (shared) | Yes | Insufficient for hosted SaaS |
| Supabase Auth (magic link) | Low | Yes | Yes (user can bring own Supabase Auth project) | **Chosen** |
| Separate auth microservice | High | Yes | Hard to operate | Rejected |

**How it works:**
- Supabase Auth is part of every Supabase project, so users bringing their own Supabase project get auth for free.
- The FastAPI backend validates the Supabase JWT on every request via a `get_current_user` FastAPI dependency (validates against the Supabase JWKS endpoint or the `SUPABASE_JWT_SECRET`).
- The frontend receives the access token on login (magic link click) and stores it in `localStorage`. Every API call includes `Authorization: Bearer <token>`.
- Self-hosted single-user bypass: if `FINEYE_AUTH_DISABLED=true` (default for local installs), the `get_current_user` dependency returns a synthetic `user_id = "local"` without checking any token. All CSV paths remain scoped to the `data/` folder as today.
- Session refresh is handled by the Supabase JS SDK (`@supabase/supabase-js`) on the frontend.

**Auth flow for hosted SaaS:**
1. User visits app → redirected to `/login` if no valid token.
2. User enters email → Supabase sends magic link.
3. User clicks link → Supabase JS SDK exchanges the token → stores in localStorage.
4. Frontend sends Bearer token on all subsequent API calls.
5. FastAPI dependency decodes JWT, extracts `sub` as `user_id`, scopes all data paths to that user.

---

### ADR-F: Migration (CSV to Supabase)

**Decision:** A standalone CLI script `scripts/migrate_csv_to_supabase.py` that reads all local CSVs and upserts to Supabase using the Service Role Key (bypasses RLS for the initial load). Idempotent — safe to re-run.

**Design:**
- Takes `--user-id` (the Supabase `auth.users.id` of the owner) and `--supabase-url` + `--service-role-key` as arguments or from env vars.
- Reads each CSV through `read_csv_typed()` (existing, schema-validated).
- Upserts via `supabase-py` client using `on_conflict="id"` (or the table's pk column).
- Adds `user_id` to every row before upsert.
- Prints a summary: rows attempted, rows succeeded, rows skipped (duplicate), rows errored.
- On error, writes failed rows to `migration_errors.jsonl` for manual review.

**When to run:** Once, after the user has configured their Supabase credentials in Settings and clicked "Migrate existing data." The Settings page triggers this via a POST to `/api/admin/migrate` (protected by the service role key, only callable from a logged-in admin session).

---

### ADR-G: Deployment Targets

**Decision:** Docker + docker-compose for self-hosted; Railway/Fly.io for managed hosting. Vercel is excluded.

**Rationale:**

| Target | Fit | Notes |
|--------|-----|-------|
| Docker + docker-compose | Excellent | FastAPI + file I/O; volume mounts for `data/`, `.wal/` |
| Railway | Good | Native Docker support; persistent volumes; reasonable cost |
| Fly.io | Good | Persistent volumes; global edge; more ops overhead |
| Vercel | Poor | Serverless functions; no persistent file I/O; pandas too heavy |

**docker-compose services:**
- `app` — FastAPI container (Uvicorn workers, `uvicorn app.main:app --host 0.0.0.0 --port 8000`)
- `app` mounts `./data:/app/data` and `./.wal:/app/.wal` as named volumes.
- No database container needed (Postgres is Supabase's, not ours to host).
- Optional `nginx` reverse proxy service for TLS termination in self-hosted deployments.

**Files to create:**
- `Dockerfile`
- `docker-compose.yml`
- `docker-compose.prod.yml` (adds Nginx + TLS config)
- `.env.example` (updated with all new variables)

---

## Phased Task Breakdown

---

### Phase 1: Auth + Supabase BYOK

**Goal:** Users can register/login with magic link; their data is isolated in their own Supabase project via BYOK credentials entered in Settings; existing CSV path remains fully functional.

**Dependencies:** Supabase project (user-supplied), `supabase-py`, `cryptography`, `python-jose`

**Risk:** JWT validation library choice — `python-jose` has known CVEs; prefer `PyJWT >= 2.8` with `cryptography` backend.

#### Tasks

**Task 1.1 — Auth dependency + JWT validation middleware**
- Agent: `security-auditor` + `backend-specialist`
- INPUT: `app/deps.py`, `app/config.py`
- OUTPUT: New `app/auth.py` with `get_current_user(token: str) -> UserContext` FastAPI dependency. `UserContext` is a dataclass with `user_id: str`. When `FINEYE_AUTH_DISABLED=true`, returns `UserContext(user_id="local")` without touching the token.
- VERIFY: `pytest tests/unit/test_auth.py` — test disabled mode returns "local", test valid JWT returns correct sub, test invalid JWT raises 401.

**Task 1.2 — Supabase credential storage in meta.json (encrypted)**
- Agent: `backend-specialist`
- INPUT: `app/routers/settings.py`, `app/models/settings.py`, `data/meta.json`
- OUTPUT: `SettingsPatch` and `SettingsRead` gain a `supabase` field (`SupabaseCredentials` model with `url`, `anon_key`, `enabled` fields). Fernet encryption/decryption in `app/services/crypto.py`. `meta.json` stores `{"supabase": {"url": "...", "anon_key": "<fernet token>", "enabled": true}}`. The plain-text anon key is never serialized.
- VERIFY: PATCH `/api/settings` with credentials → GET returns `enabled: true` and masked key (last 4 chars only). Raw `meta.json` contains only ciphertext.

**Task 1.3 — SupabaseSyncWorker (async push after CSV write)**
- Agent: `backend-specialist`
- INPUT: `app/services/sheets/sync_worker.py` (reference implementation), `app/services/ledger.py`
- OUTPUT: `app/services/supabase/sync_worker.py` with `SupabaseSyncWorker` class. Registers itself as a `LedgerWriter.on_change()` observer. Enqueues change events to `wal/supabase_pending.jsonl`. Background asyncio task drains the queue with exponential backoff. Dead-letter file at `wal/supabase_deadletter.jsonl`.
- VERIFY: `pytest tests/unit/test_supabase_sync_worker.py` — enqueue event → queue file grows by 1 line. Simulate Supabase failure → entry moves to dead-letter after max_retries. `tests/unit/test_supabase_disabled.py` — no queue file created when Supabase is not configured.

**Task 1.4 — Supabase table DDL + RLS migrations**
- Agent: `database-architect`
- INPUT: `app/storage/schemas.py` (all 9 tables)
- OUTPUT: `supabase/migrations/001_initial_schema.sql` containing `CREATE TABLE` statements and RLS policies for all 9 tables. See DDL sketches below.
- VERIFY: Run migration against a test Supabase project. Attempt to query another user's rows via the anon key — expect 0 rows returned.

**Task 1.5 — Settings UI: Cloud Storage tab**
- Agent: `frontend-specialist`
- INPUT: `templates/settings.html` (or equivalent Jinja2 template)
- OUTPUT: New "Cloud Storage" section in the Settings page with input fields for Supabase URL, Anon Key, and an Enable toggle. PATCH `/api/settings` on save. Visual indicator (green dot / red dot) showing connection status via GET `/api/supabase/health`.
- VERIFY: Manual test — enter valid credentials → green indicator. Enter invalid URL → error toast shown.

**Task 1.6 — Login page + Supabase JS SDK integration**
- Agent: `frontend-specialist`
- INPUT: `templates/base.html`, `static/js/`
- OUTPUT: `templates/login.html` with magic link email form. `static/js/auth.js` initializes `@supabase/supabase-js` with the user's Supabase URL + anon key (read from a `/api/config/public` endpoint that returns non-secret settings). On successful auth, stores access token in `localStorage`; attaches `Authorization: Bearer` header to all `fetch()` calls via a global interceptor.
- VERIFY: Manual test in hosted mode — enter email → receive link → click → reach dashboard. Refresh page → session persists.

**Task 1.7 — Migration CLI script**
- Agent: `backend-specialist`
- INPUT: `app/storage/csv_store.py`, `app/storage/schemas.py`
- OUTPUT: `scripts/migrate_csv_to_supabase.py` — reads all CSVs via `read_csv_typed()`, upserts to Supabase using service role key, tags every row with `--user-id`. Writes `migration_errors.jsonl` on failures. Prints summary table.
- VERIFY: Run against a fresh Supabase project with 100 test rows → all rows present in Postgres. Re-run → no duplicate rows (idempotent). Run with intentionally wrong service role key → clear error message, no partial writes.

**Task 1.8 — `/api/admin/migrate` endpoint (triggers migration in-app)**
- Agent: `backend-specialist`
- INPUT: `scripts/migrate_csv_to_supabase.py`, `app/routers/`
- OUTPUT: `app/routers/admin.py` with `POST /api/admin/migrate` that runs the migration logic as a background task. Protected by requiring a valid session + `FINEYE_ADMIN_KEY` header. Returns a task ID; status polled via `GET /api/admin/migrate/{task_id}`.
- VERIFY: POST triggers migration → background task runs → status transitions to "complete". Attempt without admin key → 403.

**Phase 1 Verification Checklist:**
- [ ] `pytest tests/` passes with 0 failures (existing + new tests)
- [ ] Auth disabled mode: all existing E2E tests pass unchanged
- [ ] Auth enabled mode: unauthenticated request to any `/api/` route returns 401
- [ ] Two users in hosted mode: user A's expenses not visible to user B
- [ ] BYOK credentials encrypted in `meta.json` (manual inspection)
- [ ] Supabase sync worker enqueues and drains correctly
- [ ] Migration script is idempotent

---

### Phase 2: Google Sheets — Multi-User + OAuth2

**Goal:** Each user can connect their own Google Sheet (service-account JSON or OAuth2); push sync works per-user; optional bidirectional pull.

**Dependencies:** Existing `gspread`, `google-auth`, `google-auth-oauthlib` (new for OAuth2 path)

**Risk:** OAuth2 callback URL must be registered in Google Cloud Console; document this step clearly for self-hosters.

#### Tasks

**Task 2.1 — Per-user credential isolation in integrations_store**
- Agent: `backend-specialist`
- INPUT: `app/services/sheets/integrations_store.py`
- OUTPUT: `IntegrationsStore` gains a `user_id` constructor parameter. Credential file paths become `data/{user_id}/sheets_credentials.json`. All read/write methods are scoped to the user's subdirectory. `LedgerWriter` observer registration is done per-user in the startup lifecycle.
- VERIFY: `pytest tests/unit/test_integrations_store.py` — two `IntegrationsStore` instances with different `user_id` values read/write to separate paths. No cross-contamination.

**Task 2.2 — OAuth2 auth flow endpoints**
- Agent: `backend-specialist`
- INPUT: `app/routers/sheets.py`
- OUTPUT: `GET /api/sheets/oauth/start` → returns authorization URL. `GET /api/sheets/oauth/callback` → exchanges code for tokens, stores encrypted refresh token in `data/{user_id}/sheets_token.json`. `SheetsClient` extended with `from_oauth_token()` class method that uses the stored token.
- VERIFY: Manual walkthrough of OAuth2 flow in dev environment. Stored token file contains no plain-text secret (Fernet-encrypted). Existing service-account flow unchanged.

**Task 2.3 — Push sync scoped per user**
- Agent: `backend-specialist`
- INPUT: `app/services/sheets/sync_worker.py`, `app/services/sheets/lifecycle.py`
- OUTPUT: `SheetsSyncWorker` takes `user_id` in constructor. The `LedgerWriter` observer for Sheets is registered in a per-user lifecycle init. In hosted SaaS mode, each logged-in user's worker is initialized on their first authenticated request and cached.
- VERIFY: User A syncs to their sheet → User B's sheet unchanged. `pytest tests/unit/test_sync_worker.py` passes.

**Task 2.4 — Bidirectional pull ("Import from Sheets" button)**
- Agent: `frontend-specialist` + `backend-specialist`
- INPUT: `app/services/sheets/client.py` (`read_all`), existing import mapper
- OUTPUT: `POST /api/sheets/pull` endpoint that reads all rows from the user's configured Sheet tabs → passes through the existing import mapper + dedup → returns a draft import preview. User confirms → rows committed to CSV + Supabase. Button added to Sheets settings page.
- VERIFY: Seed Sheet with 5 rows → pull → 5 rows appear in draft preview → confirm → rows in CSV and Supabase. Re-pull → 0 new rows (dedup works).

**Task 2.5 — Sheets Settings UI update (per-user)**
- Agent: `frontend-specialist`
- INPUT: `templates/` (Sheets settings section)
- OUTPUT: Settings page gains a "Google Sheets" card showing: connection method (service account / OAuth2), connected Sheet name, sync status, "Pull from Sheets" button, "Disconnect" button.
- VERIFY: Manual test — connect via OAuth2 → card shows Sheet title. Disconnect → card resets. Service account path still functional.

**Phase 2 Verification Checklist:**
- [ ] `pytest tests/` passes including `test_sheets_*.py`
- [ ] OAuth2 flow completes end-to-end in dev
- [ ] Service account flow unchanged
- [ ] Per-user credential isolation confirmed (two accounts, two separate Sheet connections)
- [ ] Bidirectional pull with dedup works

---

### Phase 3: Hosted SaaS Polish

**Goal:** Operator can deploy FinEye as a hosted product; users self-onboard with magic link, bring their own Supabase project, and have complete data isolation from day one.

**Dependencies:** Docker, docker-compose, Nginx (optional), Fly.io or Railway CLI

#### Tasks

**Task 3.1 — Dockerfile + docker-compose**
- Agent: `devops-engineer`
- INPUT: project root, `requirements.txt` (or `pyproject.toml`)
- OUTPUT: `Dockerfile` (multi-stage: builder + runtime, non-root user, copies only `app/`, `static/`, `templates/`). `docker-compose.yml` with `app` service, named volumes for `data` and `.wal`, health check on `GET /api/health`. `.dockerignore` excludes `data/`, `.env`, `.wal/`, `tests/`.
- VERIFY: `docker compose up --build` → app starts on port 8000. `curl http://localhost:8000/api/health` → `{"status": "ok"}`. `docker compose down && docker compose up` → data persists.

**Task 3.2 — Environment variable documentation + `.env.example` update**
- Agent: `backend-specialist`
- INPUT: `app/config.py`, `.env.example`
- OUTPUT: `.env.example` updated with all new variables:
  ```
  FINEYE_SECRET_KEY=<generate with: python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())">
  FINEYE_AUTH_DISABLED=false        # set true for local single-user
  FINEYE_ADMIN_KEY=<random string>  # protects /api/admin/* routes
  FINEYE_SUPABASE_ENABLED=false     # set true to activate Supabase sync
  ```
- VERIFY: Fresh install from `.env.example` → app starts with no errors.

**Task 3.3 — Onboarding wizard (first-run experience)**
- Agent: `frontend-specialist`
- INPUT: `templates/`, `app/routers/home.py`
- OUTPUT: On first login (no data in `meta.json`), user is routed to `/onboarding`. 3-step wizard: (1) Choose mode (local-only / cloud BYOK), (2) If cloud: paste Supabase URL + anon key → test connection, (3) Optional: connect Google Sheet. On complete, redirects to dashboard.
- VERIFY: New account → onboarding wizard shown. Complete wizard → reach dashboard. Return visit → wizard skipped.

**Task 3.4 — Rate limiting + abuse protection**
- Agent: `security-auditor`
- INPUT: `app/main.py` (FastAPI app factory)
- OUTPUT: `slowapi` rate limiter applied to auth-adjacent endpoints (`/api/admin/migrate`: 1/hour per user; `/api/sheets/oauth/start`: 5/hour per user). Standard endpoints get a generous limit (300/min per user) to avoid breaking normal use.
- VERIFY: Exceed rate limit → 429 response with `Retry-After` header.

**Task 3.5 — Health + readiness endpoints**
- Agent: `backend-specialist`
- INPUT: `app/routers/health.py`
- OUTPUT: `GET /api/health` → liveness check (always 200 if process alive). `GET /api/health/ready` → readiness check (verifies `data/` dir writable, Supabase reachable if configured, WAL dir writable). Used by docker-compose and Railway health checks.
- VERIFY: `curl /api/health/ready` → 200 when all systems go. Simulate Supabase unreachable → 503 with JSON body listing which check failed.

**Task 3.6 — Fly.io / Railway deployment guide**
- Agent: `devops-engineer`
- INPUT: `Dockerfile`, `docker-compose.yml`
- OUTPUT: `docs/DEPLOY.md` with step-by-step for Railway (push to GitHub → Railway auto-detect Dockerfile → set env vars → add persistent volume for `/app/data`) and Fly.io (`fly launch` → `fly volumes create fineye_data` → `fly deploy`).
- VERIFY: Deploy to staging Railway project → app accessible at public URL → health check green.

**Task 3.7 — Security scan + dependency audit**
- Agent: `security-auditor`
- INPUT: all Python files, `requirements.txt`
- OUTPUT: Run `pip-audit` and `bandit -r app/`. Fix all high/critical findings. Document any accepted medium risks in `docs/SECURITY.md`.
- VERIFY: `pip-audit` returns 0 known vulnerabilities. `bandit` severity high count = 0.

**Phase 3 Verification Checklist:**
- [ ] `docker compose up --build` succeeds cold
- [ ] Data survives `docker compose down && docker compose up`
- [ ] Hosted deployment accessible at public URL (Railway staging)
- [ ] New user onboarding wizard completes without errors
- [ ] Rate limiting blocks abuse scenarios
- [ ] `/api/health/ready` correctly reports Supabase reachability
- [ ] `pip-audit` clean
- [ ] `bandit` high severity count = 0

---

## Data Model: Supabase Table DDL Sketches

All tables share this pattern: original columns preserved verbatim, `user_id` added, surrogate PK where needed, RLS enabled.

```sql
-- =============================================
-- EXPENSES
-- =============================================
CREATE TABLE expenses (
    _row_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    id              TEXT NOT NULL,
    date            TEXT,
    created_at      TEXT,
    expense_name    TEXT,
    type_category   TEXT,
    payment_method  TEXT,
    paid_for_someone BOOLEAN,
    paid_by_someone  BOOLEAN,
    person_name     TEXT,
    amount          NUMERIC(18,4),
    cash_balance_after   NUMERIC(18,4),
    online_balance_after NUMERIC(18,4),
    source          TEXT,
    raw_transcript  TEXT,
    notes           TEXT,
    import_batch_id TEXT,
    custom_tag      TEXT,
    paid_for_method TEXT,
    adjustment_type TEXT,
    _synced_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, id)
);
ALTER TABLE expenses ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON expenses
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- BALANCES
-- =============================================
CREATE TABLE balances (
    _row_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    asof          TEXT NOT NULL,
    cash_balance  NUMERIC(18,4),
    online_balance NUMERIC(18,4),
    reason        TEXT,
    _synced_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, asof)
);
ALTER TABLE balances ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON balances
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- INVESTMENTS
-- =============================================
CREATE TABLE investments (
    _row_id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id           UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    month             TEXT NOT NULL,
    long_term         NUMERIC(18,4),
    mid_long_term     NUMERIC(18,4),
    emergency_fund    NUMERIC(18,4),
    bike_savings_wants NUMERIC(18,4),
    misc_spend_save   NUMERIC(18,4),
    fixed_deposits    NUMERIC(18,4),
    total             NUMERIC(18,4),
    import_batch_id   TEXT,
    _synced_at        TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, month)
);
ALTER TABLE investments ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON investments
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- WISHLIST
-- =============================================
CREATE TABLE wishlist (
    _row_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    id              TEXT NOT NULL,
    item            TEXT,
    target_amount   NUMERIC(18,4),
    saved_so_far    NUMERIC(18,4),
    priority        TEXT,
    notes           TEXT,
    link            TEXT,
    source          TEXT,
    created_at      TEXT,
    status          TEXT,
    import_batch_id TEXT,
    _synced_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, id)
);
ALTER TABLE wishlist ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON wishlist
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- GOALS_A
-- =============================================
CREATE TABLE goals_a (
    _row_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    goal_id              TEXT NOT NULL,
    goal_name            TEXT,
    target_amount        NUMERIC(18,4),
    current_amount       NUMERIC(18,4),
    monthly_contribution NUMERIC(18,4),
    pct_complete         NUMERIC(18,4),
    months_left          INTEGER,
    status               TEXT,
    import_batch_id      TEXT,
    _synced_at           TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, goal_id)
);
ALTER TABLE goals_a ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON goals_a
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- GOALS_B
-- =============================================
CREATE TABLE goals_b (
    _row_id              UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id              UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    goal_id              TEXT NOT NULL,
    goal_name            TEXT,
    target_amount        NUMERIC(18,4),
    manual_saved         NUMERIC(18,4),
    auto_added           NUMERIC(18,4),
    total_saved          NUMERIC(18,4),
    monthly_contribution NUMERIC(18,4),
    pct_complete         NUMERIC(18,4),
    months_left          INTEGER,
    status               TEXT,
    import_batch_id      TEXT,
    _synced_at           TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, goal_id)
);
ALTER TABLE goals_b ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON goals_b
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- BUDGET_RULES
-- =============================================
CREATE TABLE budget_rules (
    _row_id         UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id         UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    category        TEXT NOT NULL,
    monthly_budget  NUMERIC(18,4),
    carry_cap       NUMERIC(18,4),
    priority        INTEGER,
    _synced_at      TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, category)
);
ALTER TABLE budget_rules ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON budget_rules
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- BUDGET_TABLE_C
-- =============================================
CREATE TABLE budget_table_c (
    _row_id       UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id       UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    month         TEXT NOT NULL,
    category      TEXT NOT NULL,
    budget        NUMERIC(18,4),
    actual        NUMERIC(18,4),
    remaining     NUMERIC(18,4),
    carry_buffer  NUMERIC(18,4),
    overflow      NUMERIC(18,4),
    to_medical    NUMERIC(18,4),
    to_emergency  NUMERIC(18,4),
    med_balance   NUMERIC(18,4),
    emerg_balance NUMERIC(18,4),
    notes         TEXT,
    _synced_at    TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, month, category)
);
ALTER TABLE budget_table_c ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON budget_table_c
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());

-- =============================================
-- DRAFTS
-- =============================================
CREATE TABLE drafts (
    _row_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id          UUID NOT NULL REFERENCES auth.users(id) ON DELETE CASCADE,
    id               TEXT NOT NULL,
    target_table     TEXT,
    row_json         TEXT,
    errors           TEXT,
    source_filename  TEXT,
    created_at       TEXT,
    import_batch_id  TEXT,
    _synced_at       TIMESTAMPTZ DEFAULT now(),
    UNIQUE(user_id, id)
);
ALTER TABLE drafts ENABLE ROW LEVEL SECURITY;
CREATE POLICY "user owns rows" ON drafts
  USING (user_id = auth.uid()) WITH CHECK (user_id = auth.uid());
```

---

## Security Model

### Trust Boundaries

| Layer | What it protects | Mechanism |
|-------|-----------------|-----------|
| Supabase JWT | API endpoints | FastAPI dependency validates `Authorization: Bearer` on every request |
| RLS | Database rows | `user_id = auth.uid()` policy on all tables; enforced by Postgres, not app code |
| Fernet encryption | Credentials at rest | Anon key encrypted before writing to `meta.json`; decrypted in-process only |
| `FINEYE_SECRET_KEY` | Fernet master key | Stored only in environment; never in `meta.json` or any file committed to git |
| Service Role Key | Migration only | Never stored in app; passed as CLI arg or env var; not available to frontend |
| `FINEYE_ADMIN_KEY` | Admin endpoints | Header-based; only the operator knows this key |

### Threat Model

| Threat | Mitigated by |
|--------|-------------|
| User A reads User B's data | RLS + `user_id` scoping; impossible via anon key |
| Stolen anon key | No data access without valid JWT; anon key alone gets 0 rows |
| `meta.json` file leak | Credentials encrypted with Fernet; attacker needs `FINEYE_SECRET_KEY` too |
| `FINEYE_SECRET_KEY` leak | Rotation: re-generate key, re-encrypt all credentials in Settings |
| SQL injection via Supabase | `supabase-py` uses parameterized queries; no raw SQL from user input |
| SSRF via Supabase URL | Validate URL format on save; restrict to `https://` scheme with `*.supabase.co` or user-defined allow-list |
| Brute-force magic link | Supabase's built-in rate limiting on auth endpoints |
| Large file upload (migration) | Service role key required; only operator/owner can trigger |

### Key Rotation Procedure (documented for operators)

1. Generate new `FINEYE_SECRET_KEY`.
2. Run `python scripts/rotate_secret_key.py --old-key <old> --new-key <new>` — re-encrypts all credentials in all `meta.json` files.
3. Update environment variable on server.
4. Restart app.

---

## File Structure (New Files Only)

```
fineeye/
├── app/
│   ├── auth.py                             # get_current_user dependency, UserContext
│   ├── services/
│   │   ├── crypto.py                       # Fernet encrypt/decrypt helpers
│   │   └── supabase/
│   │       ├── __init__.py
│   │       ├── client.py                   # supabase-py wrapper, lazy init
│   │       └── sync_worker.py              # async push worker (mirrors sheets/sync_worker)
│   └── routers/
│       └── admin.py                        # /api/admin/migrate, /api/admin/migrate/{task_id}
├── supabase/
│   └── migrations/
│       └── 001_initial_schema.sql          # all 9 tables + RLS policies
├── scripts/
│   ├── migrate_csv_to_supabase.py          # one-time migration CLI
│   └── rotate_secret_key.py               # key rotation helper
├── Dockerfile
├── docker-compose.yml
├── docker-compose.prod.yml
├── .env.example                            # updated with all new vars
└── docs/
    ├── PLAN-fineye-cloud-byok.md           # this file
    ├── DEPLOY.md                           # Railway + Fly.io guide
    └── SECURITY.md                         # threat model + key rotation docs
```

**Modified existing files:**

| File | Change |
|------|--------|
| `app/config.py` | Add `FINEYE_SECRET_KEY`, `FINEYE_AUTH_DISABLED`, `FINEYE_ADMIN_KEY`, `FINEYE_SUPABASE_ENABLED` |
| `app/deps.py` | Add `get_current_user`, `get_supabase_client`, `get_supabase_sync_worker` |
| `app/models/settings.py` | Add `SupabaseCredentials`, `SupabaseCredentialsPatch` models |
| `app/routers/settings.py` | Handle Supabase credential read/write with encryption |
| `app/services/sheets/integrations_store.py` | Scope to `user_id` |
| `app/services/sheets/sync_worker.py` | Accept `user_id` constructor parameter |
| `app/routers/health.py` | Add `/api/health/ready` readiness check |
| `app/main.py` | Register admin router, init Supabase sync worker on startup |

---

## Phase X: Final Verification Checklist

> To be completed after all three phases are implemented.

### Automated

- [ ] `pytest tests/ -v` — 0 failures, coverage >= 80%
- [ ] `python C:/Users/santa/.claude/plugins/cache/agenthub/hub/0.4.5/skills/vulnerability-scanner/scripts/security_scan.py .` — 0 critical issues
- [ ] `python C:/Users/santa/.claude/plugins/cache/agenthub/hub/0.4.5/skills/api-patterns/scripts/api_validator.py .` — all endpoints documented
- [ ] `pip-audit` — 0 known CVEs
- [ ] `bandit -r app/ --severity-level high` — 0 findings
- [ ] `docker compose up --build` — starts cleanly, health check green
- [ ] Migration script: 100 test rows → Supabase → re-run → no duplicates

### Manual

- [ ] Auth disabled mode: all existing E2E tests pass (no regression)
- [ ] Auth enabled mode: unauthenticated → 401; authenticated → correct data
- [ ] User isolation: two accounts, zero cross-contamination of rows
- [ ] BYOK flow: paste credentials in Settings → green status → write expense → appears in Supabase table
- [ ] Google Sheets per-user: two accounts connected to different sheets → sync writes to correct sheets
- [ ] OAuth2 Sheets flow: complete end-to-end without service account
- [ ] Offline fallback: disable network → CRUD still works → re-enable → WAL drains
- [ ] Docker data persistence: `down && up` → data intact
- [ ] Onboarding wizard: new user → wizard → dashboard in under 90 seconds
- [ ] Rate limiting: exceed threshold → 429 with Retry-After

### Rule Compliance

- [ ] No purple/violet hex codes in any new UI
- [ ] No boilerplate template layouts — UI matches existing FinEye design system
- [ ] All ADRs answered with rationale (A through G above)
- [ ] No code written during planning phase

---

## Open Risks and Mitigations

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|-----------|
| `PyJWT` version incompatibility with Supabase JWT format | Medium | High | Pin to `PyJWT>=2.8,<3`; add test with real Supabase-issued token |
| `supabase-py` client not thread-safe | Medium | High | One client instance per request, or use asyncio-native `supabase-py` v2 with `AsyncClient` |
| Fernet key lost → credentials unrecoverable | Low | High | Document backup procedure; allow re-entry of credentials in Settings without the old key |
| Google OAuth2 callback URL mismatch in self-hosted | Medium | Medium | Clearly document the exact redirect URI to register; provide a config option for custom domain |
| WAL supabase_pending.jsonl grows unbounded if Supabase is permanently down | Medium | Low | Add compaction job that runs daily; alert in `/api/health/ready` if pending count > 1000 |
| Migration of large datasets (>50k rows) times out in-app | Low | Medium | CLI script preferred; in-app trigger has a 10-minute timeout and streams progress via SSE |
