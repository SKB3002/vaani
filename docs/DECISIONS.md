# FinEye — Architectural Decisions

Ordered most-recent first. Any choice made during implementation that was not fully specified in `PLAN-fineye-finance-ai.md` is logged here.

---

## 2026-04-23 — `payment_method` expanded to 5 exact values

- **Decision:** Replace the two-value enum (`cash`, `paid`) + two boolean flags (`paid_for_someone`, `paid_by_someone`) with a single `payment_method` enum that takes exactly five values: `paid | paid_cash | paid_by | paid_for | adjusted`. `paid_for` carries a `paid_for_method` sub-field (`cash | online`); `adjusted` carries an `adjustment_type` sub-field (`cash_to_online | online_to_cash`). Validation enforces each sub-field iff the enum requires it (model-level `@model_validator(mode="after")`). The old boolean flag columns are preserved in the CSV schema for read-only backward compatibility but are no longer required on write.
- **Why:** One field with semantic values beats two booleans plus an ambiguous "paid/cash" enum. It eliminates illegal states (e.g. `paid_for_someone=True` with `paid_by_someone=True`) and makes the ledger self-describing — a reader can see `paid_by` and know the user paid nothing without cross-referencing a flag column. Also aligns the LLM schema with the grid dropdown and the import preset parser so all three entry surfaces share one vocabulary.
- **Migration:** Idempotent scan at bootstrap (`.migrated_payment_method_v2` marker). Priority order is `paid_by_someone=True` → `paid_by`, else `paid_for_someone=True` → `paid_for` (with `paid_for_method` inferred from the old cash/paid flag), else `cash` → `paid_cash`, else `paid` stays `paid`. Rewrite count + per-bucket counts are logged at INFO and persisted in the marker file.
- **Grid UX:** Two new conditional columns render as "—" unless the relevant payment enum is selected. When the user flips to `paid_for` or `adjusted`, the afterChange handler auto-focuses the sub-dropdown cell if empty. Script tag bumped to `?v=6`.

---

## 2026-04-23 — `adjusted` payments bypass the expense ledger

- **Decision:** A POST to `/api/expenses` with `payment_method="adjusted"` does NOT write a row to `expenses.csv`. Instead the router calls `BalanceService.adjust(amount, direction)` which appends a single row to `balances.csv` with `reason="adjusted"` and returns `{"type": "adjustment", "balances": {...}}`. The voice router behaves identically — `parsed.payment_method == "adjusted"` is routed to `balances.adjust()` and returns `{"status": "adjusted", ...}`. `snapshot_after_expense()` raises `ValueError` if called with `payment_method="adjusted"` — this is a programming error, not user input.
- **Why:** Adjustments are not outflows; treating them as expenses would poison every Need/Want/Investment aggregation, the daily/monthly totals, and the charts. Keeping them in `balances.csv` with a distinct `reason` preserves the single-ledger-of-truth rule in §4.1 (expenses.csv = every outflow) while still letting the user correct cash/online drift in one atomic move. The adjust() method also validates direction + positive amount so that typos surface at the service boundary, not in the CSV.

---

## 2026-04-23 — `type_category` stored format: colon → comma

- **Decision:** Comma-separated storage `"Type, Category"` (e.g. `"Need, Food & Drinks"`). Grid UI shows two dropdowns (Type / Category); the combined write goes to one column.
- **Why:** Reads naturally in plain text and user-facing CSV/Excel exports. Also matches the preset ingest format (`"Travel, Needs"`) more closely, so the normalizer no longer has to emit a format different from what it consumes.
- **Migration:** Idempotent scan at bootstrap rewrites any row whose `type_category` matches the legacy `^(Need|Want|Investment):(Food & Drinks|Travel|Enjoyment|Miscellaneous)$` pattern. A `data/.migrated_type_category_comma` marker prevents re-scans. Safe on empty data. Logged row count at INFO.
- **Overflow engine:** `BudgetRule.category` now treats `", "` (not `":"`) as the "full type_category" signal; suffix and custom_tag matching otherwise unchanged. Legacy colon-form rules are NOT auto-migrated (rules are user-authored; rewrite on next edit).
- **LLM prompt:** Groq schema block and inline example updated to emit `"Type, Category"`.

---

## 2026-04-23 — M6: Sheets setup is 100% UI-driven via `integrations.json` + hot-reload

- **Choice:** A new runtime config file `data/meta/integrations.json` is the authoritative source for Sheets config at runtime. `.env` / `Settings.GOOGLE_SHEETS_*` remain first-boot defaults only. Resolution order on every read: `integrations.json` > env. Three new endpoints drive the UI flow: `POST /api/sheets/credentials` (multipart JSON upload, validated for `type=service_account` + `client_email` + `private_key` + `project_id`, ≤50 KB, saved to `data/.secrets/service_account.json` with 0600/0700 on POSIX — Windows ACLs out of scope), `DELETE /api/sheets/credentials`, and `PATCH /api/sheets/config` (accepts `spreadsheet_url` or bare `spreadsheet_id`, extracts ID via `/d/(…)/` regex, toggles `enabled`). A new `app/services/sheets/lifecycle.py` module owns `install` / `teardown` / `reload(app)`; `PATCH /config` calls `reload` when `enabled` or `spreadsheet_id` changes so the worker + observer hot-reload without a server restart. `LedgerWriter.off_change()` was added so the previous Sheets observer is deregistered before a new one is attached (prevents double-enqueueing after repeated reloads). Credentials upload does NOT auto-enable sync — the user must explicitly toggle.
- **Why:** A non-technical portfolio visitor won't edit `.env` and restart a server. The earlier setup worked but was gated on ops knowledge; making it click-click-toggle inside the Integrations card is the difference between "nice demo" and "usable product". Keeping the `.env` fallback means Docker / CI defaults still work. `integrations.json` lives under the already-gitignored `data/**` tree so secrets posture is unchanged (the key file stays in `data/.secrets/`, the JSON only stores path + extracted email). Hot-reload via `lifecycle.reload(app)` keeps the local-first write path non-blocking — observer swap is idempotent and worker-start is `await`ed inside the request, so by the time `PATCH /config` returns the client already sees the real status.

---

## 2026-04-24 — M5: Rule-driven charts via `charts.yaml`

- **Choice:** New visualisations are declared as YAML entries in `data/meta/charts.yaml` — zero Python or JS changes. The registry is loaded by `app/services/charts/registry.py` into typed `ChartSpec` objects (pydantic); the generic aggregator in `app/services/charts/aggregator.py` turns each spec into a Chart.js-ready payload via pandas `groupby` / `pivot_table` / `Grouper`. The frontend renderer (`static/js/charts.js`) consumes `/api/charts` + `/api/charts/{id}` and builds Chart.js configs without any chart-specific code.
- **Why:** §8 of the plan mandates "new chart = 1 YAML entry, 0 code". Keeping the aggregator pure (a `data_loader` callable is injected, DataFrames flow through) makes tests trivial and keeps the code path identical whether the data comes from a CSV, a fixture, or a future SQLite table. `POST /api/charts/refresh` reloads the cached registry without a restart, so the YAML can be hot-edited.

## 2026-04-24 — M5: `DataFrame.query` filters sanitised via AST walk

- **Choice:** `filter:` strings in `charts.yaml` (e.g. `"date >= '2026-04-01' and category == 'Travel'"`) are parsed with `ast.parse(mode='eval')` and walked to reject anything outside a tiny allow-list: `BoolOp(and/or)`, `Compare(==,!=,<,<=,>,>=,in,not in)`, `UnaryOp(not)`, `Name`, `Constant` (str/int/float/bool), and literal lists/tuples. Function calls, attribute access, subscripts, comprehensions, lambdas, walrus, and arithmetic are all rejected at registry-load time (so a bad filter fails fast, not on first GET).
- **Why:** `DataFrame.query` uses `eval` semantics and is trivially weaponisable — `"__import__('os').system(...)"` would otherwise execute on first GET. A sanitiser at the spec boundary keeps the "charts are config" promise honest: the user can edit YAML without becoming a code-execution vector, and the same path protects the `POST /api/charts/refresh` endpoint.

## 2026-04-24 — M5: Palette uses CSS vars so theme-swap recolours charts automatically

- **Choice:** Spec palettes accept CSS custom-property tokens (`--chart-need`, `--chart-food`, etc.). The backend returns them unchanged; the frontend resolves via `getComputedStyle(document.documentElement).getPropertyValue(...)`. Each chart re-renders on `fineye:themechange` so light/dark switching recolours without a full reload. A minimal 16-line `--chart-*` block was appended to `static/css/tokens.css` (the only CSS change in M5).
- **Why:** The app's luxury palette is theme-aware and already owned by `tokens.css`. Hard-coding hex in `charts.yaml` would fork the palette and make dark-mode charts visually detached from the rest of the UI. CSS vars keep chart colours in one place and make adding new categories a single token addition.

## 2026-04-24 — M5: Virtual columns `type`/`category` synthesised from `type_category`

- **Choice:** `expenses.csv` stores the combined `type_category` column (`"Need:Travel"`). When a chart spec references `type` or `category`, `app/services/charts/derived.py::add_derived_columns` splits the column at aggregation time — no storage change, no migration. The registry treats them as first-class columns (filter + group_by both work).
- **Why:** Splitting on disk would double the column count for no gain; keeping the split virtual means `type_category` remains the single source of truth and the pie/donut specs stay declarative. Extensible to other sources (e.g. a future `budget_table_c.category_prefix` split) via the same adapter.

---

## 2026-04-23 — M4: Budget engine runs on startup + on every mutation

- **Choice:** `BudgetRunner.recompute_all()` is called from (1) FastAPI `lifespan` startup, (2) every POST/PATCH/DELETE on `/api/budgets/rules`, and (3) every PATCH on `/api/budgets/caps`. Also exposed explicitly via `POST /api/budgets/recompute`. The engine iterates every month from the earliest expense's month through the current local month, carrying state forward, and writes `budget_table_c.csv` in a single atomic replace-all pass.
- **Why:** The plan's §7 calls for a "deterministic pure function" — every input is a CSV, there's no hidden state. Full recompute over a few years of months × a handful of rules is microseconds of pandas work; incremental recompute would add correctness risk (forgetting to invalidate a downstream month when a prior month's rule changed) without measurable gain. Replace-all also means Table C is always consistent with the rules + expenses on disk.

## 2026-04-23 — M4: Custom tags live on expenses.csv as a nullable `custom_tag` column

- **Choice:** Added a new nullable `custom_tag` column to `expenses.csv` schema. Rule category matching is suffix-of-type-category first (e.g. rule `Food & Drinks` matches `Need:Food & Drinks` + `Want:Food & Drinks`), then full `type_category` if the rule string contains `:`, then falls through to `custom_tag` equality. A suffix-style rule additionally aggregates any rows with `custom_tag == rule.category` so the user can freely tag electricity/utilities/rent rows without breaking the pre-existing Need/Want/Investment pie.
- **Why:** The plan's §4.7 says the rule's `category` "matches `type_category` suffix or custom" but never specifies the "custom" storage. A dedicated column (vs. overloading `notes`) keeps it queryable and round-trippable through pandas. Additive + nullable = backward compatible with every row already in the CSV. The column is populated manually for now (PATCH `/api/expenses/{id}` accepts `custom_tag`); the AI pipeline can set it during parse in a future milestone.

## 2026-04-23 — M4: Goals A <-> B linking by `goal_name` with explicit `sync_to_overview` flag

- **Choice:** Tables A (`goals_a.csv`) and B (`goals_b.csv`) remain independent writes. Rows with identical `goal_name` across the two tables are considered "linked" by convention only. `PATCH /api/goals/sources/{goal_id}` and `POST /api/goals/sources/{goal_id}/contribute` take an optional `sync_to_overview` query parameter (default **false**). When true, the row's recomputed `total_saved` is pushed into the corresponding `goals_a` row's `current_amount` and A's derived fields are re-derived.
- **Why:** The plan's §4.5/§4.6 defines two sibling tables with overlapping fields but never pins down the link semantics. A hard FK would force the user to create A before B (or vice versa); lookup-by-name keeps both tables independently authorable. Opt-in sync prevents surprise mutations — most users will want Table A to reflect contributions, but the explicit flag makes the side-effect obvious in API calls and curl logs.

---

## 2026-04-23 — M6: LedgerWriter observer pattern (post-commit, synchronous registration)

- **Choice:** `LedgerWriter.on_change(callback)` registers observers that fire AFTER the atomic CSV write succeeds. Callbacks receive a small event dict `{table, op, pk_column, pk_value, row, ...}`. Observer exceptions are caught and logged — they never propagate back into the write path.
- **Why:** Direct sync calls from the ledger into `SheetsClient` would couple the local-first write path to Sheets' availability. The observer pattern keeps the ledger oblivious to Sheets and lets M6 be a bolt-on that runs only when `GOOGLE_SHEETS_ENABLED=true` and creds are present. Non-Sheets features (reports, charts) continue to work with zero overhead.

## 2026-04-23 — M6: Unknown rows in Sheet are reported, never auto-pulled

- **Choice:** Startup reconciler logs a notice + exposes counts via `GET /api/sheets/status`. `POST /api/sheets/reconcile` returns unknown-row details. Import is explicit per-tab via `POST /api/sheets/reconcile/import?tab=X`.
- **Why:** Per plan Q10, local CSVs are authoritative. Silently importing rows that were manually added to the Sheet would violate that contract. The explicit opt-in preserves the "no data silently overwrites local" guarantee while still offering a one-click import when the user wants it.

## 2026-04-23 — M6: Service account auth is the default (OAuth documented as alternative)

- **Choice:** `SheetsClient` uses `google.oauth2.service_account.Credentials`. `google-auth-oauthlib` is in the dep set but not wired — `docs/SHEETS_SETUP.md` walks through the OAuth path for users who prefer it.
- **Why:** v1 is single-user / localhost (plan Q3). A service account needs one JSON key, one share, and never expires. The OAuth installed-app flow adds refresh-token plumbing, token cache files, and a browser round-trip for the first run — all cost with no v1 benefit.

## 2026-04-23 — M6: Sync queue is durable via `.wal/sheets_pending.jsonl`, in-process only

- **Choice:** Jobs are persisted to `sheets_pending.jsonl` BEFORE being handed to the in-memory `asyncio.Queue`. On success the line is rewritten without the job; on max-retries exceeded the job moves to `sheets_deadletter.jsonl` and is removed from pending. At startup, pending is reloaded into the queue. Idempotency is free because `upsert_row` keys on the row's PK — replaying a job that already succeeded is a no-op.
- **Why:** The enqueue path must be non-blocking for local writes. A fully in-memory queue would lose jobs on crash; a distributed queue (Celery/ARQ) is overkill for a single-user localhost app.

---

## 2026-04-23 — M3: Investments upsert-by-month + Wishlist dual-ledger contributions

- **Investments `POST /api/investments` is upsert-by-month**, not append. `month` is the PK (§4.3), so posting the same month twice replaces the row rather than duplicating. `total` is always server-computed as the sum of numeric columns excluding `total`, `month`, and `import_batch_id`. User-defined columns with `dtype="number"` participate automatically via the universal registry; `string` / `boolean` / `date` user columns are carried on the row but excluded from the total math. PATCH re-runs the total calculation on the merged row so edits stay consistent.
- **Wishlist contributions dual-write to `expenses.csv`** per §4.1's single-ledger principle. `POST /api/wishlist/{id}/contribute` with `source="expense"` bumps `saved_so_far` AND appends an expense row with `type_category="Investment:Miscellaneous"`, `notes="wishlist:{id}"`, and a balance snapshot. Contributions are intentionally NOT dedup-keyed — each call gets a fresh ULID so repeating the same amount is allowed (users legitimately contribute the same round number multiple times).
- **Wishlist schema grew**: added `notes` (free text) and `link` (URL) to `wishlist.csv`. `priority` was already in the schema (§4.4) as an optional `high|med|low` enum. Columns are nullable strings on disk.
- **Soft-delete is default** for wishlist (`status="abandoned"`); `?hard=true` removes the row. Matches the "no data ever lost" audit posture already used by the user-columns registry.
- **Why:** These are the three ambiguities the plan didn't pin down for M3. Upsert-by-month matches how users think about the monthly grid (one row per month, always). Dual-writing keeps the Need/Want/Investment pie honest without coupling wishlist to the expense router. `notes` and `link` were missing from the schema but present in the add-wish UI requirements — adding them is cheaper than writing a "notes belong elsewhere" adapter.

---

## 2026-04-23 — User-defined columns generalised to ALL tables

- **Choice:** The "+ Add Column" registry that was investments-only (`data/meta/investment_columns.json`) is now a universal per-table feature, persisted at `data/meta/user_columns/{table}.json`. New endpoints: `GET/POST/PATCH/DELETE /api/tables/{table}/columns`. Supported dtypes: `string`, `number`, `boolean`, `date`. Forward-only (NaN / default for prior rows). Deletion removes from the **registry only** — the CSV column is preserved as-is so no data is ever silently lost.
- **Why:** User request; the audit-safe delete matches the "no data ever lost" principle already guiding the ledger's WAL + atomic-CSV layer. The legacy `investment_columns.json` is migrated on first read and preserved on disk (never deleted). A process-wide `threading.RLock` serialises registry mutations — chosen over `Lock` because `add_column` calls `list_user_columns` internally (re-entry would deadlock a non-reentrant lock).

## 2026-04-23 — Personal-ledger import preset (first built-in preset)

- **Choice:** Introduced `data/meta/import_presets.json` with a single preset `personal_ledger_v1` matching the user's real Excel layout (DD/MM/YYYY dates, combined "Tags" column, daily "Total" summary rows, balance-adjust rows with zero amount and empty payment). The `POST /api/import/{upload_id}/map` endpoint now accepts an optional `preset_id` that overrides mapping/date_format/row_filters. Synthetic mapping targets `__payment_dual`, `__tags_combined`, `__cash_snapshot`, `__online_snapshot` are recognised by the committer's preset pre-processor (documented in `normalizer.py` header). `Total` rows are skipped but their declared totals feed a per-day `checksum_report` in the dry-run (mismatch > ₹1 = `match: false`). Balance-adjust rows are written to `balances.csv` with `reason="manual_adjust"`, NOT to `expenses.csv`. Future presets (e.g. SBI bank statement, HDFC credit card) can be added by appending to `import_presets.json`.
- **Why:** The user's actual spreadsheet doesn't match any single importer assumption — it mixes expense rows, daily summaries, and balance adjustments in one table with non-normalised tags ("Travel, Needs" vs "Wants, Miscellaneous" vs single "Wants"). Hardcoding a bespoke adapter would have worked for one layout; a preset-driven approach scales to the other statement formats the user will want later without further backend changes.

---

## 2026-04-23 — Frontend: luxury rebrand — **supersedes** trust-green-only and sharp-radii-everywhere

- **Choice:** The visual direction is now *private-banking-meets-sci-fi*. Deep emerald remains the action colour but yields the "emotional hero" moment to a subdued gold (`#B8934A` light / `#D4AB67` dark). Warm-paper `#FAF8F3` replaces slate-grey `#F7F8FA` as the light background. Radii relax by one step on cards (10 → 8 px) and relax further on modals (10 → 12 px); inputs/buttons stay at 6 px. Chips remain pill.
- **Supersedes:** `2026-04-23 — Frontend: brand accent is trust-green, no purple` and `2026-04-23 — Frontend: sharp radii (2–10 px), pill chips` (kept below for history).
- **Why:** The earlier direction was *correct but characterless*. "Trust green on slate with sharp radii" reads as a generic fintech starter — not the portfolio piece the product wants to be. The luxury direction introduces a specific, recognisable tone (editorial, not SaaS) without abandoning the no-purple rule: emerald and gold both carry "wealth" without the AI-design cliché. Dark mode was the loudest failure of the previous system (Material-grey surfaces, no visible elevation); the new three-layer onyx scheme is the single biggest fix.

## 2026-04-23 — Frontend: typography — serif display + sans body + mono numbers

- **Choice:** Fraunces (variable serif, 300–600) for h1–h4, KPI values, modal and page titles; Inter Tight for body/navigation/forms/table cells; JetBrains Mono for every numeric context with `font-variant-numeric: tabular-nums lining-nums`. Fraunces and Inter Tight loaded from Google Fonts with `preconnect`, `preload`, and `display=swap`.
- **Supersedes:** the implicit "Inter for everything" in `DESIGN_SYSTEM.md` v1.
- **Why:** Typography is the single biggest lever between "SaaS template" and "editorial product". A serif-for-display pairing is the move that separates FT/Bloomberg/Robb Report-style interfaces from the Vercel-template look. Inter Tight (not Inter) gives a slightly more condensed rhythm in dense tables while remaining a Google Fonts default; the two families read as intentionally paired rather than accidentally mixed. Mono numerals are retained (they were already right) because column alignment is non-negotiable in a ledger.

## 2026-04-23 — Frontend: dark mode rebuilt with 3-layer surface elevation + gold hairlines

- **Choice:** Dark theme no longer uses a single darker shade of light surfaces. It defines three layers — `#0A0906` page / `#14110D` cards / `#1C1814` modals & dropdowns / `#24201A` hover-on-modal — plus subtle **gold hairline borders** (`rgba(212, 171, 103, 0.12–0.32)`) as the primary elevation cue. Shadows in dark mode are mostly invisible on near-black; we lean on `inset 0 1px 0 rgba(240, 234, 224, 0.04)` top-highlights and a very soft `box-shadow: 0 0 28px rgba(212, 171, 103, 0.10)` gold glow on focused inputs and modals.
- **Supersedes:** `:root[data-theme="dark"]` block in the earlier `tokens.css` that just darkened slate values (kept in git history).
- **Why:** The user called the old dark mode "not proper", and they were right. Cards were indistinguishable from the page background; there was no visible Z-elevation. The new system is designed dark-first (not inverted from light) so cards, modals, and dropdowns each communicate their place in the stack through *border* rather than shadow — which is how high-end dark UIs actually work (Bloomberg, Stripe CLI, Linear). All text on all three surface layers passes WCAG AA or better (see `DESIGN_SYSTEM.md` → Elevation → Contrast table).

## 2026-04-23 — Frontend: glassmorphism reserved for topbar + modals, gated on `@supports`

- **Choice:** `backdrop-filter: blur(12–14 px) saturate(1.3–1.4)` applied only to `.topbar`, `.modal-backdrop`, `.card--glass`, `.listbox`, and `.handsontable.listbox` / `.handsontable.htDropdown`. Every use is wrapped in `@supports (backdrop-filter: blur(12px)) or (-webkit-backdrop-filter: blur(12px))` so older browsers get a solid-surface fallback automatically.
- **Why:** Generic AI-design guidance bans glassmorphism because it's overused. The nuance here: when applied *only* to floating UI over a warm paper / warm onyx background, and paired with a gold hairline, blur reads as "restrained depth" rather than as a cliché. It is not used on cards, buttons, KPIs, or any static chrome. The `@supports` gate means no content-area visual breakage on browsers that don't support it.

## 2026-04-23 — Frontend: gold (`#B8934A` light / `#D4AB67` dark) is a rare-accent token, not a brand colour

- **Choice:** Gold appears on: (1) the focus ring, (2) the hero KPI's left rule, (3) selected-row hairline in the grid, (4) `chip--premium`, (5) dark-mode borders (as a hairline tint, never saturated), (6) brand mark halo. It is **never** used as a fill for CTAs, chips, or surfaces. Primary actions remain emerald.
- **Why:** Saturating UI with gold is the fastest way to tip from "private banking" into "casino". Gold only works as a finishing touch. Emerald stays primary because it is semantically truthful (growth, money) and because it pairs with warm paper and warm onyx equally well.

---

## 2026-04-23 — M2: Groq settings read outside the `FINEYE_` prefix

- **Choice:** `GROQ_API_KEY`, `GROQ_MODEL`, `GROQ_BASE_URL` are declared on `Settings` with `validation_alias=` so they come from the raw env var names (matches `.env.example`).
- **Why:** The plan's `.env.example` writes `GROQ_API_KEY=...` without the `FINEYE_` prefix. Adding aliases keeps a single `Settings` class and lets pydantic-settings still read `.env` without breaking the existing `FINEYE_*` convention.

## 2026-04-23 — M2: Voice router owns `/api/uniques` + teach endpoint

- **Choice:** Both `/api/expense/parse` and `/api/uniques[/teach]` live in `app/routers/voice.py`, prefix `/api`.
- **Why:** The teach flow only exists to support the voice pipeline (and the grid-row correction UI). Co-locating avoids a one-endpoint `uniques` router. A simple `threading.Lock` + atomic tmp-then-replace write keeps concurrent teach calls safe (tested).

## 2026-04-23 — M2: Missing-required-fields in LLM output = clarify (not 422)

- **Choice:** When `action="expense"` but `expense_name`/`type_category`/`payment_method`/`amount` is null, the router returns `status="clarify"` with a "Missing: …" question rather than 422.
- **Why:** 422 is reserved for "LLM output failed JSON/schema validation twice" per the plan. A well-formed parse that happens to lack fields is a *model deficiency we can recover from by asking the user*. UI treats clarify as a benign prompt, not an error.

## 2026-04-23 — M2: GroqLLMClient does its own retry, not a global middleware

- **Choice:** One repair retry lives inside `GroqLLMClient.parse_expense`. Router never sees the malformed first response.
- **Why:** Retry semantics are tied to the JSON-schema validation, which is provider-specific. Wrapping it in HTTP middleware would blur concerns. The client raises `LLMParseError` (carrying raw + transcript) only after two failures, which the router maps to 422.

---

## 2026-04-23 — LLM provider correction: Groq (not xAI Grok)

**Decision:** Use **Groq** (groq.com — fast LPU inference of open-weight models) as the M2 LLM provider, NOT xAI's Grok.

**Why:** Original plan wording said "Grok" which was transcribed from user's voice/intent as xAI Grok. User clarified the intent was Groq. Groq gives sub-second latency on Llama 3.3 70B / Mixtral, OpenAI-compatible API, generous free tier — better fit for a voice → expense parse flow where perceived latency matters.

**Changes applied:**
- `.env.example`: `XAI_API_KEY` / `XAI_MODEL=grok-beta` → `GROQ_API_KEY` / `GROQ_MODEL=llama-3.3-70b-versatile` / `GROQ_BASE_URL=https://api.groq.com/openai/v1`
- `app/services/llm.py`: docstring references updated
- `templates/settings.html`: Integration field renamed (label, input id/name, placeholder `gsk_...`)
- `docs/PLAN-fineye-finance-ai.md`: all occurrences of "Grok" → "Groq"; Q1 endpoint + model updated; §10 tech stack updated

**Impact on M2 (voice pipeline):** Client is `httpx.AsyncClient` against `https://api.groq.com/openai/v1/chat/completions` (OpenAI-compatible). Use `response_format={"type": "json_object"}` for strict JSON. Default model `llama-3.3-70b-versatile`; `llama-3.1-8b-instant` available as a faster/cheaper fallback. No SDK lock-in.

---

## 2026-04-23 — Backend: `date` field aliased to `date_t` in expense models

- **Choice:** `from datetime import date as date_t  # noqa: N813` in `app/models/expense.py`; every `date: date_t` field uses the alias.
- **Why:** With `from __future__ import annotations` on Python 3.14, pydantic's deferred annotation evaluation resolves the field name `date` to the field itself (shadowing the imported type), causing `TypeError: unsupported operand type(s) for |: 'NoneType' and 'NoneType'` at model-build time for `date | None`. Aliasing avoids the shadow without renaming fields (which would break API contracts).

## 2026-04-23 — Backend: `/health` returns both `ok` and `status`

- **Choice:** `{"ok": "true", "status": "ok", "version": ..., "tz": ...}`.
- **Why:** Plan asks for `{ok, version, tz}`; I also keep `status` for uptime-probe tooling that expects the classic `status: "ok"` field. Both are cheap.

## 2026-04-23 — Backend: Ruff `B008`, `SIM105`, `SIM117` ignored globally

- **Choice:** Config-level ignore.
- **Why:** FastAPI's `Depends(...)` in argument defaults is idiomatic (B008). Crash-injection tests deliberately use `try/except/pass` to simulate a mid-write failure (SIM105). Nested `with` blocks (file-lock + file-open) read more naturally one-per-line (SIM117).

## 2026-04-23 — Backend: `StrEnum` over `class X(str, Enum)`

- **Choice:** `PaymentMethod(StrEnum)`, `Source(StrEnum)` in `app/models/common.py`.
- **Why:** Python 3.11+ idiom; avoids MRO ambiguity and the UP042 lint hit. No behaviour change at runtime.

---

## 2026-04-23 — Frontend: brand accent is trust-green, no purple

- **Choice:** Primary accent is `#108A5C` (light) / `#34D399` (dark). Enjoyment category uses muted magenta `#B5446E`, not purple.
- **Why:** Neutral slate surfaces keep saturated colour semantic; green reads as "growth / trust" without being the overused fintech cyan-blue. Purple is the #1 AI-design cliché and is banned brand-wide.

## 2026-04-23 — Frontend: sharp radii (2–10 px), pill chips

- **Choice:** Cards at 10 px, buttons/inputs at 6 px. Chips alone use `border-radius: 999px`.
- **Why:** Financial tools earn trust via precision. Consumer-lifestyle "16 – 32 px friendly" radii undermine that. Chips stay pill so they read as labels, not buttons.

## 2026-04-23 — Frontend: Handsontable community via CDN

- **Choice:** Handsontable 14.x with `licenseKey: "non-commercial-and-evaluation"` loaded from jsDelivr.
- **Why:** Plan §6 names Handsontable as the primary candidate. Excel-like keyboard nav, dropdown editors, and CSV paste are free out of the box. A skin file (`handsontable-skin.css`) rethemes cells + dropdowns to FinEye tokens so the grid doesn't look generic.

## 2026-04-23 — Frontend: sidebar + topbar on desktop, collapsed nav on mobile

- **Choice:** 240 px left sidebar + 56 px topbar on ≥ 860 px; mobile collapses the sidebar into a horizontal chip-nav row above main content.
- **Why:** 12 pages don't fit a top-only nav comfortably. Sidebar groups (`Ledger`, `Planning`, `Budgets`, `Tools`) give shape to the IA.

## 2026-04-23 — Frontend: voice button is visible everywhere but stubbed until M2

- **Choice:** Topbar voice PTT button renders on every page. Click triggers a toast explaining voice capture ships in M2. Home hero has a larger voice button for marketing-feel screenshots.
- **Why:** Plan calls voice out as the hero feature. Shipping a placeholder that looks complete but communicates the milestone lets portfolio screenshots land before the STT pipeline exists.

## 2026-04-23 — Frontend: M2/M4/M5 pages render the full shell + empty state

- **Choice:** `/charts`, `/budgets/monthly` exist in the nav and render the full page chrome with a polished `empty_state` naming the milestone (M4 / M5) and a CTA to a still-functional page.
- **Why:** Plan says those pages should be "minimal scaffolds". Full-chrome empty states keep navigation consistent and let reviewers click every link.

## 2026-04-23 — Frontend: theme persistence via `data-theme` on `<html>`

- **Choice:** `data-theme="light" | "dark"` overrides. Absence = follow `prefers-color-scheme`. Saved to `localStorage.fineye.theme`. Set before CSS parses in `app.js` to avoid FOUC.
- **Why:** Gives users an explicit toggle without fighting OS preference; keeps server-rendered templates stateless.

---

## 2026-04-23 — Package metadata: hatchling + PEP 621

- **Choice:** `hatchling` as the build backend in `pyproject.toml`.
- **Alternatives considered:** `setuptools`, `poetry-core`, `pdm-backend`, `flit_core`.
- **Why:** Modern, minimal, zero-config, works out of the box with `pip install -e .` which is how the plan's runbook boots the app. No lockfile coupling (matches the plan's preference for `.env` + lightweight tooling).

## 2026-04-23 — `ulid-py` instead of `python-ulid`

- **Choice:** `ulid-py` (imported as `import ulid; ulid.new()`).
- **Alternatives considered:** `python-ulid`, UUIDv7.
- **Why:** The plan's §10 specifies `ulid-py`. Both generate sortable IDs; keeping the plan's choice.

## 2026-04-23 — Single global WAL file, not per-table

- **Choice:** One `.wal/wal.jsonl` file (append-only) with a sibling `.wal/wal_applied.jsonl` of cleared entry IDs.
- **Alternatives considered:** Per-table WAL file (e.g., `.wal/expenses.jsonl`), per-day WAL file.
- **Why:** Single writer per process (single-user app) means lock contention on one file is a non-issue; simpler replay (natural total ordering); `WriteAheadLog.compact()` can be called periodically to prune.

## 2026-04-23 — Added `drafts.csv` to the schema set

- **Choice:** `drafts.csv` is a first-class table with schema, used by `on_invalid="draft"`.
- **Alternatives considered:** Keep drafts as plain JSONL outside the ledger.
- **Why:** Drafts need the same atomic write + WAL guarantees as real data (they're user-visible rows pending fix). Reusing the LedgerWriter gives this for free.

## 2026-04-23 — Added `import_batch_id` column to every importable table

- **Choice:** Every table in `IMPORTABLE_TABLES` has an `import_batch_id` column; the column is also present on `drafts` for rollback symmetry.
- **Alternatives considered:** A separate `import_rows.csv` lookup table mapping (table, row_id) → batch_id.
- **Why:** Inline column = O(1) rollback via `delete_where(column="import_batch_id", value=batch_id)`. The lookup-table approach requires a join on every rollback and a second WAL-protected mutation.

## 2026-04-23 — `.dedup_keys.jsonl` is global, not per-table

- **Choice:** One SHA1-per-line file in `data/.dedup_keys.jsonl` covering every importable table. Keys are namespaced (`expenses` uses its spec'd composite, other tables prefix with their name).
- **Alternatives considered:** Per-table dedup files.
- **Why:** Simpler ops (one file to truncate if a user wants a clean slate). Key collisions across tables are impossible given the namespacing.

## 2026-04-23 — Timezone cache TTL: 5 seconds

- **Choice:** `app.services.tz` caches the parsed tz name for 5 seconds.
- **Alternatives considered:** 1s (too aggressive), 60s (too slow for tests), invalidate-only.
- **Why:** The plan specifies ~5s; balances test friendliness with production cheapness. `PATCH /api/settings` invalidates the cache immediately.

## 2026-04-23 — `get_llm_client()` is a plain function, not a `Depends`

- **Choice:** `app.services.llm.get_llm_client()` returns the client directly; `app.deps.get_llm_client()` re-exports.
- **Alternatives considered:** Wire as a FastAPI `Depends` returning an async context.
- **Why:** The LLM client is a process-wide singleton; it holds a long-lived `httpx.AsyncClient` in M2. A `Depends` wrapper just adds indirection. Tests swap with `monkeypatch.setattr`.

## 2026-04-23 — No `sheets.py` no-op stub shipped in M0

- **Choice:** Seam deferred. `LedgerWriter` provides the only integration point; the Sheets worker in M6 will subscribe after every `append/update/delete` (e.g., via a post-write hook or observer list).
- **Alternatives considered:** Ship an empty `SheetsSyncQueue` today.
- **Why:** Prefer to design the seam only when M6 lands — premature abstraction risk. The LedgerWriter is the natural hook.

## 2026-04-23 — Home route renders live KPIs

- **Choice:** `GET /` computes today/month totals + current balances server-side and passes to template.
- **Alternatives considered:** Static template + client-side fetch.
- **Why:** The frontend agent's `templates/index.html` expects `kpis` and `recent_expenses` as template context. Server-render keeps first-paint fast and avoids a flash of empty KPIs. The server is local-only; latency is not a concern.
