# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Common Commands

Dev environment uses Python ≥3.11. The local virtualenv is `.venv312`.

```bash
pip install -e ".[dev]"
python -m scripts.bootstrap_cli           # creates data/, .wal/, .tmp/, seeds CSV headers
python -m uvicorn app.main:app --reload   # dev server on :8000
python -m scripts.seed                    # optional demo data
python -m scripts.migrate_to_supabase     # migrate local CSVs into Supabase
```

Tests / lint / types:

```bash
python -m pytest -x
python -m pytest tests/unit/test_overflow_engine.py::test_name   # single test
python -m pytest --live                    # opt-in: hits real external APIs (Groq, Sheets)
python -m ruff check .
python -m mypy app/
```

`tests/unit` and `tests/e2e` are the two suites (no `tests/integration/` despite what CONTRIBUTING.md says). Live-marked tests are skipped unless `--live` is passed. The `tmp_workspace` fixture in `tests/conftest.py` rebuilds an isolated `data/` + `.wal/` and clears the `lru_cache` on `get_settings` / `get_ledger` / `get_balance_service` / `get_budget_runner` — use it instead of constructing services directly.

## Architecture

### Two storage modes (one codebase)

`FINEYE_STORAGE_BACKEND` switches behaviour at runtime:

- **`csv` (default, local):** writes go through `LedgerWriter` → WAL append → atomic CSV write → WAL clear. If Supabase is configured, the `supabase_observer` mirrors writes via `on_change`. Bootstrap runs `wal.replay()` on startup to recover from crashes.
- **`supabase` (Vercel):** `LedgerWriter.append/update/delete` short-circuit to direct upserts in `app/storage/supabase_store.py`. WAL and CSV are skipped. `app/main.py` auto-flips to this mode if it detects `EROFS` (Vercel's read-only FS) during bootstrap.

Both modes still fire `on_change` observers, so the budget auto-recompute hook works identically.

### Single write path — never bypass `LedgerWriter`

Every mutation must go through `app/services/ledger.py::LedgerWriter`. Routers must not write directly to CSVs or to `supabase_store`. The contract is: WAL-first → atomic write → notify observers. Breaking this loses the crash-recovery and dual-write guarantees. Schemas (column order, dtypes, primary key) live in `app/storage/schemas.py` and are the single source of truth — both CSV columns and Postgres equivalents come from `SCHEMAS`.

### Observer pattern drives derived state

`LedgerWriter.on_change(callback)` is how derived state stays in sync. Two observers are registered in `app/main.py` lifespan:
1. `_recompute_on_expense_change` — re-runs `BudgetRunner.recompute_all()` whenever the `expenses` table changes (this is how Table C stays current).
2. `supabase_observer` — dual-writes to Postgres in csv mode only.

Observers must never raise back into the write path; the ledger swallows exceptions and logs them.

### Dependency injection via `lru_cache`

`app/deps.py` exposes `get_ledger()`, `get_balance_service()`, `get_budget_runner()`, `get_llm_client()` as process-wide singletons. Tests must call `cache_clear()` on these (the `tmp_workspace` fixture does this) — otherwise stale singletons leak across tests.

### Config quirks

`app/config.py` uses `env_prefix="FINEYE_"` but several fields override with `validation_alias` to read unprefixed env vars (`GROQ_API_KEY`, `DB_HOST`, `GOOGLE_SHEETS_*`). When adding settings, follow the existing pattern — don't assume the `FINEYE_` prefix applies to everything.

### Vercel entry point

`api/index.py` is the serverless ASGI handler. It defensively wraps `app.main:app` import — if startup fails, the handler returns a 500 with diagnostic output (Python version, `sys.path`, env keys, traceback) rather than crashing the function. When debugging Vercel issues, hit any URL and read the diagnostic body.

### Layered services

- `app/routers/` — thin FastAPI handlers, no business logic
- `app/services/` — business logic; `budget_runner.py`, `overflow.py`, `balances.py`, `goals.py`, `llm.py` (Groq client), `imports/` (CSV/Excel mapper + dedup), `charts/` (registry + safe query DSL), `sheets/` (Google Sheets backup with retry/backoff), `prompts/` (LLM prompt templates)
- `app/storage/` — `csv_store` (atomic write + file lock), `wal`, `supabase_store`, `schemas`, `user_columns` (custom-column migration support)
- `app/middleware/auth.py` — `PasswordGateMiddleware` only mounts when `FINEYE_APP_PASSWORD` is set (Vercel deployments)

### Strict-typed modules

Per `pyproject.toml`, `app.services.*` and `app.storage.*` are checked under mypy strict mode (`disallow_untyped_defs`, `disallow_incomplete_defs`). New code in those packages must have full type annotations or mypy will fail.

## Architecture Principles (from CONTRIBUTING.md)

These are load-bearing for review decisions:

1. **Data ownership is sacred.** BYOK — never add a path that sends user data to a server we control.
2. **Zero data loss.** Every write goes through WAL + atomic CSV (local) or upsert (Supabase). No exceptions.
3. **Offline-first.** App must work without internet; cloud sync is a mirror, not a dependency.
4. **INR-first** defaults (₹, IST, UPI/cash/online payment methods). i18n is welcome but defaults stay.
