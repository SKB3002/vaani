# PLAN — AI Monthly Briefing + Ask-Your-Ledger RAG Chat

> **Status:** Approved by user (Socratic Gate complete). Atomic delivery — both features ship together.
> **Window:** ~10–14 person-days.
> **LLM:** Groq `openai/gpt-oss-120b` (analysis model, separate from existing `GROQ_MODEL` used for routing/extraction).
> **Mode:** Two LLM calls per chat turn (planner + narrator). No SSE streaming in v1.

---

## 1. Goals

Concrete, measurable acceptance criteria. Each is independently verifiable.

| # | Goal | Measurement |
|---|------|-------------|
| G1 | Monthly Briefing renders for any month with expense data | `GET /api/insights/monthly?month=YYYY-MM` returns 200 with `stats_bundle`, `narration`, `cache_hit` keys |
| G2 | Ask-Your-Ledger chat answers questions over the ledger using a planner→executor→narrator pipeline | `POST /api/insights/chat` returns `{plan, result_table, narration, cache_hit}` for all 8 v1 primitives |
| G3 | **p50 latency on cached briefing/chat answer < 200 ms** | Measured locally with warm cache (no LLM call); excludes network round-trip |
| G4 | **p95 cold-cache LLM end-to-end < 8 s** | Measured against Groq `gpt-oss-120b`; bundle aggregation + 1–2 LLM calls + render |
| G5 | **Narration-only contract enforced by automated test** | Unit test that asserts narrator output contains zero digit characters (regex `\d`) — numbers live in stat-refs the frontend re-binds |
| G6 | Cache invalidation is automatic when underlying data changes | Editing any expense in month M causes the next call to `?month=M` to have `cache_hit=false` |
| G7 | Graceful degradation when Groq is unreachable | With `GROQ_API_KEY=""`, briefing endpoint returns `{stats_bundle, narration: null, cache_hit: false}` and chat returns `{plan, result_table, narration: null}` |
| G8 | CSV mode and Supabase mode behave identically for cache | Parity test asserts the same hash → same payload from both backends |
| G9 | Planner rejects unknown primitives | Asking "delete all expenses" returns `{error: "could not interpret question", suggestions: [...]}` rather than executing anything |
| G10 | Conversation context is preserved across turns | Last 4 user-question summaries are passed to the planner; verified by integration test |

---

## 2. Non-goals (explicitly out of scope)

These will NOT be built in this plan. Each is a deliberate deferral.

- ❌ **SSE / streaming responses** — two-call structure is already chosen; streaming is a v1.1 follow-up
- ❌ **Multi-user RLS** — `OWNER_ID` continues to be a single env-configured UUID; no per-user auth
- ❌ **Agentic writes** — LLM cannot mutate the ledger. Narration is read-only by design (digit-regex contract enforces this at output level too)
- ❌ **Separate microservice / new deployment target** — runs inside existing FastAPI app on Vercel + local
- ❌ **OpenAI / Anthropic / Gemini providers** — Groq only; provider abstraction is out of scope
- ❌ **Voice integration with chat** — voice stays bound to the existing `/voice` flow (expense entry); chat is text-only in v1
- ❌ **Mobile push notifications** for monthly briefing — manual/scheduled cron is also out of scope; user opens the page to trigger
- ❌ **Cross-month comparative dashboards** beyond what the aggregator already provides — no new chart pages
- ❌ **Saved chat threads** — conversation history is session-scoped only (lost on browser close)
- ❌ **Embeddings / vector search** — the "RAG" here is structured retrieval over pandas, not semantic search

---

## 3. Architecture overview

```
                                    ┌──────────────────────────────────────┐
                                    │  LedgerWriter (app/services/ledger)  │
                                    │  on_change(observer) ──┐             │
                                    └────────────────────────┼─────────────┘
                                                             │
                                                             ▼
                                          ┌──────────────────────────────────┐
                                          │  insights_cache invalidator      │
                                          │  (app/services/insights/cache)   │
                                          │  - Deletes rows whose key_hash   │
                                          │    derives from the changed     │
                                          │    table+month                  │
                                          └──────────┬───────────────────────┘
                                                     │ writes
                                                     ▼
                                ┌────────────────────────────────────────────┐
                                │         insights_cache (table)             │
                                │  CSV: data/insights_cache.csv              │
                                │  + Supabase mirror: insights_cache         │
                                │  (id, owner_id, kind, key_hash,            │
                                │   payload_json, created_at, expires_at)    │
                                └────────────▲──────────────┬─────────────────┘
                                             │ stores       │ reads
                                             │ hash → JSON  │
                                             │              ▼
┌──────────────────────────────┐   ┌─────────┴───────────────────────────┐
│   LedgerWriter.read(table)   │──▶│  MonthlyStatsBundle / TrendStats    │
│   (pandas DataFrames)        │   │  aggregator                         │
└──────────────────────────────┘   │  (app/services/insights/aggregator) │
                                   │   - month slice                     │
                                   │   - trailing-3 window               │
                                   │   - trailing-12 window (YoY)        │
                                   │   - top categories / merchants     │
                                   │   - anomalies                       │
                                   └────────────────┬────────────────────┘
                                                    │ JSON-canonicalized
                                                    ▼
                                   ┌─────────────────────────────────────┐
                                   │  narrator (insights/narrator.py)    │
                                   │  - prompt: bundle as JSON           │
                                   │  - calls llm_client (gpt-oss-120b)  │
                                   │  - validates: no \d in output      │
                                   │  - retries once on violation        │
                                   └────────────────┬────────────────────┘
                                                    │ {summary, highlights, stat_refs}
                                                    ▼
                                   ┌─────────────────────────────────────┐
                                   │  Frontend: stat_ref_render.js       │
                                   │  - Walks DOM for {{stat:foo}}      │
                                   │  - Replaces with bundle.foo value   │
                                   │  - Formats per locale (₹, %, etc.)  │
                                   └─────────────────────────────────────┘
```

**Layering, in plain English:**

1. **The aggregator** owns *what data the LLM sees*. It always pulls a month slice plus trailing-3-month and trailing-12-month windows so the narrator can talk about trends ("higher than the last three months", "down vs last year"). The bundle is a deterministic JSON document.
2. **The cache** owns *narration JSON only*. It is keyed on `sha256(canonicalized bundle)`. If the bundle is byte-identical, the cached narration is reusable. The cache does NOT store raw stats — those are recomputed cheaply by pandas every request, which keeps the source of truth in the ledger.
3. **The two are decoupled** — the aggregator can evolve (add trailing-6, add MoM% column) and that will simply produce a new hash, naturally invalidating old narration without any explicit cache flush.
4. **`on_change` invalidation** is a defense-in-depth mechanism, not the primary one. The hash already invalidates whenever the bundle changes. The observer additionally prunes obviously stale rows (same month, anything in `expenses`, `balances`, `investments`) so cache rows don't pile up.

---

## 4. Shared infrastructure

Files both features reuse. Each has a single, narrow responsibility.

| File (absolute path) | Responsibility |
|---|---|
| `c:\Suyash_Projects\vaani\app\services\insights\__init__.py` | Package marker; re-exports public types |
| `c:\Suyash_Projects\vaani\app\services\insights\aggregator.py` | `MonthlyStatsBundle` (briefing) and `TrendStatsBundle` (chat). Pure functions over `LedgerWriter.read(...)`. Returns Pydantic models with deterministic JSON serialization. Includes trailing-3 and trailing-12 window slices for trend analysis. |
| `c:\Suyash_Projects\vaani\app\services\insights\cache.py` | Persistent narration cache. Dual-mode: CSV (`data/insights_cache.csv`) in csv backend, direct upsert into `insights_cache` table in supabase backend. Mirrors `BudgetRunner._write_table_c` pattern (writes via `LedgerWriter` so observers fire). Public API: `get(kind, key_hash) -> dict\|None`, `put(kind, key_hash, payload_json, ttl_days) -> None`, `invalidate_for_table(table_name, month=None) -> int`. |
| `c:\Suyash_Projects\vaani\app\services\insights\llm_client.py` | Thin wrapper around the existing `GroqLLMClient` (`app/services/llm.py`) that exposes `chat_completion(messages, model=settings.GROQ_ANALYSIS_MODEL, max_tokens=...)` for arbitrary chat-completion calls (the existing client is currently scoped to extraction/whisper). Reuses the same `httpx.AsyncClient`, retry policy, and timeout config. |
| `c:\Suyash_Projects\vaani\app\services\insights\narrator.py` | Narration-only contract enforcement. Builds the system prompt that says "use {{stat:slug}} placeholders, never write digits". Calls llm_client. **Validates the response with `re.search(r"\d", text)`** — if any digit is found, retries once with `"Your previous response contained digits. Use {{stat:...}} placeholders only."` appended. On second failure, raises `NarrationContractError` and the route returns `narration=null`. |
| `c:\Suyash_Projects\vaani\static\js\partials\stat_ref_render.js` | Frontend stat-ref re-binder. Exports `renderStatRefs(rootEl, bundle)`. Walks text nodes, replaces `{{stat:foo.bar}}` with `bundle.foo.bar` formatted via `Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })` for amounts and percent format for ratios. Idempotent — safe to call twice. |
| `c:\Suyash_Projects\vaani\app\storage\schemas.py` | Add `INSIGHTS_CACHE` schema entry (see §9 for exact columns/dtypes). Append to `SCHEMAS` dict. Do NOT add to `IMPORTABLE_TABLES`. |
| `c:\Suyash_Projects\vaani\sql\002_insights_cache.sql` | Supabase migration. Single `CREATE TABLE insights_cache (...)` with composite unique index on `(owner_id, kind, key_hash)` and btree on `expires_at` for TTL pruning. |
| `c:\Suyash_Projects\vaani\app\services\charts\safe_query.py` | Extend with the 8 named primitives the chat planner can call (see §6). Each primitive is a pure function `(df_dict: dict[str, DataFrame], **args) -> ResultFrame`. The existing safe-query DSL stays for chart routes — the new primitives are a parallel namespace exposed to the planner. |

---

## 5. Feature 1: Monthly Briefing

### Route

```
GET /api/insights/monthly?month=YYYY-MM
→ 200 {
    stats_bundle: { ... full MonthlyStatsBundle JSON ... },
    narration:    { summary: "...", highlights: ["..."], stat_refs: {...} } | null,
    cache_hit:    bool
  }
→ 422 if month is malformed
→ 200 with empty bundle if month exists but has no expenses
```

### Page

- **Template:** `c:\Suyash_Projects\vaani\templates\insights_monthly.html` (extends `base.html`)
- **JS:** `c:\Suyash_Projects\vaani\static\js\insights_monthly.js`
- **URL:** `/insights/monthly` (default = current month). Query string `?month=YYYY-MM` deep-links to a specific month.
- **Layout:** Hero section with the narration `summary` rendered after stat-ref rebinding, then a list of `highlights`, then the existing-style stat cards filled directly from `stats_bundle` (no LLM dependency for the cards — they always render even if narration is null).

### Prompt template contract (NOT the prompt itself, just I/O shape)

**Input to narrator:**
```json
{
  "month": "2026-04",
  "currency_symbol": "₹",
  "totals":   { "spent": ..., "income": ..., "saved": ... },
  "by_category": [ {"name": "...", "amount": ..., "share": ...}, ... ],
  "trailing_3":  { "avg_spent": ..., "delta_pct": ... },
  "trailing_12": { "yoy_delta_pct": ..., "ranking": ... },
  "top_merchants":   [ ... ],
  "anomalies":       [ ... ],
  "goal_progress":   [ ... ]
}
```

**Required output schema (validated by Pydantic):**
```json
{
  "summary":    "string, 2–4 sentences, no digits",
  "highlights": ["string", "string", ...],   // 3–6 bullets, no digits
  "stat_refs":  { "totals.spent": "{{stat:totals.spent}}", ... }
}
```

The narrator's system prompt instructs it to use `{{stat:totals.spent}}` style placeholders. Frontend resolves these against `stats_bundle` at render time.

### Cache key formula

```
key_hash = sha256(
  json.dumps(
    {
      "kind": "monthly_briefing",
      "month": "YYYY-MM",
      "bundle": MonthlyStatsBundle.model_dump(mode="json"),
      "prompt_version": "v1",
      "model": settings.GROQ_ANALYSIS_MODEL
    },
    sort_keys=True,
    separators=(",", ":")
  ).encode("utf-8")
).hexdigest()
```

The `prompt_version` and `model` fields ensure that bumping the prompt or switching models naturally invalidates everything without a manual flush.

### Edge cases

| Case | Behavior |
|---|---|
| Empty month (no expenses at all) | Bundle returns `totals.spent=0`, `by_category=[]`, etc. Narrator is **not** called. Page shows "No expenses recorded for this month." |
| Partial month (current/in-progress) | Aggregator computes "month-to-date" totals and trailing windows up to *yesterday*. Narration prompt receives `is_partial: true` so it can phrase things like "so far this month". |
| Groq unreachable / `GROQ_API_KEY=""` | Endpoint returns `{stats_bundle, narration: null, cache_hit: false}`. Page renders cards from bundle and shows a small inline note: "AI summary unavailable." |
| Cache miss | Compute bundle → call narrator → write cache → return `cache_hit=false` |
| Cache hit (fresh) | Return cached payload, `cache_hit=true`. No LLM call. |
| Cache hit but stale (`now - created_at > 30 days`, even if data unchanged) | Treat as miss; regenerate to refresh tone/phrasing. Old row is overwritten via upsert on `(owner_id, kind, key_hash)`. |
| Narration contract violation (digits leaked) | One retry. On second failure, return `narration=null` and log a warning. |

---

## 6. Feature 2: Ask-Your-Ledger RAG chat

### Route

```
POST /api/insights/chat
Body: {
  question: "string",
  history:  [ { role: "user"|"assistant", question: "string", summary: "string" }, ... ]   // last 4 turns max
}
→ 200 {
    plan:         { primitive: "compare_periods", args: {...} },
    result_table: { columns: [...], rows: [...], meta: {...} },
    narration:    { summary, highlights, stat_refs } | null,
    cache_hit:    bool
  }
→ 200 {
    plan: null,
    result_table: null,
    narration: null,
    error: "could not interpret question",
    suggestions: ["Try: How does April compare to March?", ...]
  }
```

### Page

- **Template:** `c:\Suyash_Projects\vaani\templates\insights_chat.html`
- **JS:** `c:\Suyash_Projects\vaani\static\js\insights_chat.js`
- **URL:** `/insights/chat`
- **Layout:** Single-column thread. User input pinned to bottom. Each assistant turn renders narration `summary` (post-rebind) on top, an expandable "show data" panel containing `result_table` as a small HTML table, and a footer chip showing which primitive was used (helps debugging and builds user trust).

### Allowlist of v1 primitives

These are the **only** functions the planner is allowed to choose. Any other `primitive` value fails Pydantic validation and the planner is asked to retry once.

| # | Primitive | Args | Returns |
|---|---|---|---|
| 1 | `compare_periods` | `period_a: "YYYY-MM"`, `period_b: "YYYY-MM"`, `group_by: "category"\|"merchant"\|"payment_method"\|"none"` | Two-column comparison table with delta and delta_pct (covers MoM, QoQ, YoY by choice of periods) |
| 2 | `top_n_merchants` | `period: "YYYY-MM"\|"YYYY"`, `n: int = 10` | Top-N rows of merchants by amount |
| 3 | `category_trend` | `category: str`, `n_months: int = 12` | Time series of one category over the last N months |
| 4 | `goal_progress` | `goal_id: str\|"all"` | Goal name, target, current, % complete, months_left |
| 5 | `cumulative_by_category` | `period: "YYYY-MM"\|"YYYY"`, `type: "Need"\|"Want"\|"Investment"\|null` | Cumulative spend by category (optionally filtered by need/want/investment) |
| 6 | `anomaly_summary` | `period: "YYYY-MM"`, `top_n: int = 5` | Top-N expenses that are >2σ above their category mean for the period |
| 7 | `payment_method_breakdown` | `period: "YYYY-MM"\|"YYYY"` | Cash vs Online vs UPI vs etc. with amounts and shares |
| 8 | `person_split_summary` | `period: "YYYY-MM"\|"YYYY"` | `paid_for_someone` and `paid_by_someone` aggregations grouped by `person_name` |

### Stage 1 — Planner (LLM call #1)

- **Model:** `settings.GROQ_ANALYSIS_MODEL`
- **System prompt includes:** schema summary (column names of `expenses`, `goals_b`, etc.), the 8 primitive signatures, "you must return JSON matching `QueryPlan`", current date, last 4 user-question summaries from `history` (NOT full results — that bloat is the whole reason for summarising).
- **User message:** the raw question.
- **Output:** `QueryPlan { primitive: Literal[...], args: dict }` validated by Pydantic.
- **On invalid output:** one retry with the validation error appended ("Your previous output failed validation: <error>. Return valid JSON."). On second failure: route returns `{error, suggestions}`.
- **Token budget:** ~1.5k in / ~200 out.

### Stage 2 — Executor (no LLM)

- Pure pandas. Reads the relevant tables via `LedgerWriter.read(...)`. Calls the matching function in `app/services/charts/safe_query.py`. Returns `ResultFrame { columns, rows, meta }`.
- **Hard guarantees:** no I/O outside `LedgerWriter`, no eval, no string-built queries. Every primitive is a hand-written pandas function.
- **Failure:** if the primitive raises (e.g., unknown category), wrap into `{error: "execution failed: <msg>"}` and skip narrator.

### Stage 3 — Narrator (LLM call #2)

- **Model:** `settings.GROQ_ANALYSIS_MODEL`
- **System prompt:** narration-only contract (no digits, use `{{stat:row.col}}` placeholders that reference `result_table.rows[i].col`).
- **User message:** original question + `result_table` as compact JSON.
- **Output:** same `{summary, highlights, stat_refs}` schema as briefing.
- **Same digit-regex validator + 1 retry** as briefing.
- **Token budget:** ~1k in / ~400 out.

### Conversation history

- Server-side **session storage only** (FastAPI session middleware backed by a signed cookie — no separate DB). On Vercel this is per-function-invocation; the client passes `history` back on each request, which is the source of truth.
- Cap: last 4 turns.
- For each historical turn, send only `{ role, question, summary }` to the planner — never the full `result_table`. This keeps planner prompt under 2k tokens regardless of conversation depth.

### Caching for chat

- Same cache table, `kind="chat_answer"`.
- Key includes the question, last-4 history summaries, and the `bundle_signature` (a hash of *all* tables' last-modified timestamps). If any underlying table has been modified, the bundle_signature changes and old chat answers naturally don't hit.
- TTL: 30 days (same as briefing).

---

## 7. Task breakdown

> **Effort confidence bands:** [H] = high (well-understood), [M] = medium (some unknowns), [L] = low (uncertain — may need spike).

### Task 1 — Add `INSIGHTS_CACHE` schema [H — 0.5 d]

- **Owner:** `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\storage\schemas.py` (append `INSIGHTS_CACHE` and add to `SCHEMAS`)
- **Blockers:** none
- **Verify:** `python -m pytest tests/unit/test_schemas.py` (existing schema tests still pass; add one for new entry)

### Task 2 — Supabase migration `002_insights_cache.sql` [H — 0.5 d]

- **Owner:** `hub:database-architect`
- **Files:**
  - `c:\Suyash_Projects\vaani\sql\002_insights_cache.sql` (new file)
- **Content:** `CREATE TABLE insights_cache(...)`, `CREATE UNIQUE INDEX idx_insights_cache_owner_kind_hash ON insights_cache(owner_id, kind, key_hash)`, `CREATE INDEX idx_insights_cache_expires ON insights_cache(expires_at)`
- **Blockers:** Task 1 (column list must match)
- **Verify:** Manually run against Supabase; `\d insights_cache` shows expected structure

### Task 3 — Extend `GroqLLMClient` for analysis-model chat completion [H — 1 d]

- **Owner:** `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\services\llm.py` (add `chat_completion` method or factor existing extraction call to share infra)
  - `c:\Suyash_Projects\vaani\app\services\insights\llm_client.py` (new — thin wrapper using `GROQ_ANALYSIS_MODEL`)
  - `c:\Suyash_Projects\vaani\app\config.py` (add `GROQ_ANALYSIS_MODEL`, `INSIGHTS_CACHE_TTL_DAYS`, `INSIGHTS_NARRATION_MAX_RETRIES` — see §10)
  - `c:\Suyash_Projects\vaani\app\deps.py` (add `get_insights_llm_client()` lru_cache singleton)
- **Blockers:** none
- **Verify:** unit test mocks httpx, asserts request body uses `gpt-oss-120b`; live test with `--live` flag actually hits Groq

### Task 4 — `MonthlyStatsBundle` aggregator with trailing windows [M — 2 d]

- **Owner:** `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\services\insights\__init__.py` (new)
  - `c:\Suyash_Projects\vaani\app\services\insights\aggregator.py` (new — Pydantic models + pandas functions)
- **Includes:** `MonthlyStatsBundle`, `TrendStatsBundle`, helpers `compute_monthly_bundle(month, ledger)`, `compute_trailing_3(month, ledger)`, `compute_trailing_12(month, ledger)`, `compute_anomalies(month, ledger)`. Confidence band M because anomaly detection thresholds may need tuning against real data.
- **Blockers:** none (reads existing `expenses` schema)
- **Verify:** `tests/unit/test_aggregator.py` with seeded fixture covering: empty month, single-expense month, normal month, current month (partial), 13-month trailing window

### Task 5 — Narration cache module + on_change invalidator [M — 1.5 d]

- **Owner:** `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\services\insights\cache.py` (new)
  - `c:\Suyash_Projects\vaani\app\main.py` (register cache invalidator as a third `on_change` observer)
- **API:** `InsightsCache.get(kind, key_hash)`, `.put(kind, key_hash, payload, ttl_days)`, `.invalidate_for_table(table, month=None)`, `.prune_expired()`
- **Dual-mode:** csv backend writes `data/insights_cache.csv` via `LedgerWriter` (so the cache itself goes through WAL — same crash-recovery guarantee). supabase backend short-circuits to direct upsert just like other tables. Follows `BudgetRunner._write_table_c` pattern exactly.
- **Blockers:** Task 1 (schema), Task 2 (Supabase table)
- **Verify:** `tests/unit/test_insights_cache.py` — `tmp_workspace` fixture, put → get round-trip, invalidate clears matching rows, expired rows pruned

### Task 6 — Narrator wrapper + digit-regex contract test [H — 1 d]

- **Owner:** `hub:test-engineer`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\services\insights\narrator.py` (new)
  - `c:\Suyash_Projects\vaani\app\services\insights\prompts\narrator_system.txt` (new — versioned system prompt)
  - `c:\Suyash_Projects\vaani\tests\unit\test_narrator_contract.py` (new)
- **Tests:**
  1. Mock LLM returns `"You spent ₹12,345 in April"` → narrator detects `\d`, retries
  2. Mock LLM retries with `"You spent {{stat:totals.spent}} in April"` → still has digits-in-month — note: the test must be written to also reject this. Decision: month names like "April" stay (no digits), but "2026" or "12,345" must not appear. Regex `\d` covers both.
  3. Two consecutive failures → raises `NarrationContractError`
  4. Successful first response → returned as-is
- **Blockers:** Task 3 (llm_client)
- **Verify:** `python -m pytest tests/unit/test_narrator_contract.py -x`

### Task 7 — Monthly Briefing route + Jinja page + JS [M — 1.5 d]

- **Owner:** `hub:frontend-specialist` + `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\routers\insights.py` (new — registers `/api/insights/monthly` and the `/insights/monthly` page handler)
  - `c:\Suyash_Projects\vaani\app\main.py` (mount the new router)
  - `c:\Suyash_Projects\vaani\templates\insights_monthly.html` (new)
  - `c:\Suyash_Projects\vaani\static\js\insights_monthly.js` (new)
- **Blockers:** Tasks 4, 5, 6
- **Verify:** `curl localhost:8000/api/insights/monthly?month=2026-04` returns the contract; manual browser check at `/insights/monthly`

### Task 8 — Stat-ref re-binder JS component [H — 1 d]

- **Owner:** `hub:frontend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\static\js\partials\stat_ref_render.js` (new — pure ES module)
  - `c:\Suyash_Projects\vaani\templates\base.html` (script tag include)
- **API:** `renderStatRefs(rootEl, bundle)` — walks text nodes, replaces `{{stat:path.to.value}}` using `Intl.NumberFormat('en-IN', { style: 'currency', currency: 'INR' })` for amounts, `Intl.NumberFormat('en-IN', { style: 'percent', maximumFractionDigits: 1 })` for ratios (path ending in `_pct` or `share`).
- **Blockers:** none (can be built in parallel with backend)
- **Verify:** browser console smoke test against a fixture HTML; idempotency check (calling twice produces identical DOM)

### Task 9 — Allowlist primitives extension to `safe_query.py` [M — 1.5 d]

- **Owner:** `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\services\charts\safe_query.py` (extend with the 8 primitives — see §6)
  - `c:\Suyash_Projects\vaani\tests\unit\test_safe_query_primitives.py` (new — 8 tests, one per primitive, with seeded fixtures)
- **Blockers:** none
- **Verify:** `python -m pytest tests/unit/test_safe_query_primitives.py -x`

### Task 10 — Chat planner + executor + narrator orchestration [M — 2 d]

- **Owner:** `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\services\insights\chat.py` (new — `QueryPlan` Pydantic model, `plan_query(question, history) -> QueryPlan`, `execute_plan(plan) -> ResultFrame`, `narrate_result(question, result) -> Narration`, top-level `answer_question(question, history) -> ChatResponse`)
  - `c:\Suyash_Projects\vaani\app\services\insights\prompts\planner_system.txt` (new — versioned)
  - `c:\Suyash_Projects\vaani\tests\unit\test_chat_planner.py` (new)
- **Tests:**
  1. Planner returns valid plan → executor runs → narrator runs → full ChatResponse
  2. Planner returns invalid primitive name → retries → still invalid → returns `{error, suggestions}`
  3. Planner returns valid primitive but executor fails → returns `{plan, result_table:null, narration:null, error: "execution failed"}`
- **Blockers:** Tasks 3, 5, 6, 9
- **Verify:** `python -m pytest tests/unit/test_chat_planner.py -x`

### Task 11 — Chat route + page + JS with conversation history [M — 1.5 d]

- **Owner:** `hub:frontend-specialist` + `hub:backend-specialist`
- **Files:**
  - `c:\Suyash_Projects\vaani\app\routers\insights.py` (extend with `POST /api/insights/chat` and `/insights/chat` page handler)
  - `c:\Suyash_Projects\vaani\templates\insights_chat.html` (new)
  - `c:\Suyash_Projects\vaani\static\js\insights_chat.js` (new — manages local `history[]` array, posts to API, renders results, calls `renderStatRefs` after each turn)
- **Blockers:** Tasks 8, 10
- **Verify:** manual browser test — ask 3 sequential questions, verify the third planner prompt sees summaries of the first two

### Task 12 — Vercel/Supabase parity tests + degradation tests [H — 1 d]

- **Owner:** `hub:test-engineer`
- **Files:**
  - `c:\Suyash_Projects\vaani\tests\e2e\test_insights_parity.py` (new — runs each route once with `FINEYE_STORAGE_BACKEND=csv` then with `=supabase`, asserts identical responses for identical input)
  - `c:\Suyash_Projects\vaani\tests\e2e\test_insights_degradation.py` (new — sets `GROQ_API_KEY=""`, asserts both routes return `narration=null` without raising)
- **Blockers:** Tasks 7, 11
- **Verify:** `python -m pytest tests/e2e/test_insights_parity.py tests/e2e/test_insights_degradation.py -x`

### Task 13 — Docs update — README + CLAUDE.md [H — 0.5 d]

- **Owner:** `hub:documentation-writer`
- **Files:**
  - `c:\Suyash_Projects\vaani\README.md` (add "AI Insights" section: monthly briefing + chat usage, env vars, screenshots)
  - `c:\Suyash_Projects\vaani\CLAUDE.md` (add subsection under "Architecture" explaining the aggregator/cache decoupling and `INSIGHTS_CACHE` as a derived table, and a note that `app.services.insights.*` is also strict-typed)
  - `c:\Suyash_Projects\vaani\pyproject.toml` (extend mypy strict scope to `app.services.insights.*`)
- **Blockers:** all preceding tasks (so we document what actually exists)
- **Verify:** read it back; ensure it answers "where does cached narration live?" and "what does the LLM see?" cleanly

---

## 8. Dependency graph

```
1 (schema) ──▶ 2 (supabase migration)
1 (schema) ──▶ 5 (cache module)
2          ──▶ 5
3 (llm_client) ──▶ 6 (narrator)
3              ──▶ 10 (chat orchestration)
4 (aggregator) ──▶ 7 (briefing route)
5              ──▶ 7
5              ──▶ 10
6              ──▶ 7
6              ──▶ 10
8 (stat-ref JS) ──▶ 7
8              ──▶ 11 (chat page)
9 (primitives) ──▶ 10
10             ──▶ 11
7              ──▶ 12 (parity tests)
11             ──▶ 12
12             ──▶ 13 (docs)
```

**Critical path:** 1 → 5 → 10 → 11 → 12 → 13 (≈ 7 days serial).
**Parallel lanes:**
- Lane A (data): 1, 2, 4, 5
- Lane B (LLM): 3, 6, 9
- Lane C (frontend): 8 (independent until Task 7/11)

A two-engineer split runs A+B concurrently, then converges on 7/10/11.

---

## 9. Schema changes

### 9.1 `app/storage/schemas.py` addition

```python
INSIGHTS_CACHE: TableSchema = {
    "columns": [
        "id",
        "owner_id",
        "kind",            # "monthly_briefing" | "chat_answer"
        "key_hash",        # sha256 hex digest of canonicalized payload
        "payload_json",    # serialized {summary, highlights, stat_refs, ...}
        "created_at",      # ISO 8601
        "expires_at",      # ISO 8601, created_at + INSIGHTS_CACHE_TTL_DAYS
    ],
    "dtypes": {
        "id": "string",
        "owner_id": "string",
        "kind": "string",
        "key_hash": "string",
        "payload_json": "string",
        "created_at": "string",
        "expires_at": "string",
    },
    "pk": "id",
}
```

Add `"insights_cache": INSIGHTS_CACHE` to the `SCHEMAS` dict. Do **not** add to `IMPORTABLE_TABLES`.

### 9.2 `sql/002_insights_cache.sql`

```sql
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
    ON insights_cache(owner_id, kind, key_hash);

CREATE INDEX IF NOT EXISTS idx_insights_cache_expires
    ON insights_cache(expires_at);
```

---

## 10. Config additions (`app/config.py`)

Append to the `Settings` class, following the existing alias-without-prefix pattern (since these mirror `GROQ_*` style):

```python
# AI Insights (briefing + chat)
GROQ_ANALYSIS_MODEL: str = Field(
    default="openai/gpt-oss-120b", validation_alias="GROQ_ANALYSIS_MODEL"
)
INSIGHTS_CACHE_TTL_DAYS: int = Field(
    default=30, validation_alias="INSIGHTS_CACHE_TTL_DAYS"
)
INSIGHTS_NARRATION_MAX_RETRIES: int = Field(
    default=1, validation_alias="INSIGHTS_NARRATION_MAX_RETRIES"
)
```

Update `.env.example` accordingly. No changes to `env_prefix`.

---

## 11. Verification checklist (run before declaring done)

Concrete, copy-pasteable. Each line is independently verifiable.

### 11.1 Briefing happy path

```bash
# First call — cold cache
curl -s "http://localhost:8000/api/insights/monthly?month=2026-04" | jq '.cache_hit, (.narration != null), (.stats_bundle != null)'
# Expected: false  true  true

# Second call — warm cache
curl -s "http://localhost:8000/api/insights/monthly?month=2026-04" | jq '.cache_hit'
# Expected: true
```

### 11.2 Cache invalidation via `on_change`

```bash
# Edit any expense in April via the UI or API, then:
curl -s "http://localhost:8000/api/insights/monthly?month=2026-04" | jq '.cache_hit'
# Expected: false  (observer pruned the matching cache row)
```

### 11.3 Graceful Groq degradation

```bash
GROQ_API_KEY="" python -m uvicorn app.main:app --port 8001 &
sleep 2
curl -s "http://localhost:8001/api/insights/monthly?month=2026-04" | jq '.narration'
# Expected: null
curl -s "http://localhost:8001/api/insights/monthly?month=2026-04" | jq '.stats_bundle != null'
# Expected: true
```

### 11.4 Narration-only contract test

```bash
python -m pytest tests/unit/test_narrator_contract.py -x -v
# Asserts: regex r"\d" never matches narrator output
```

### 11.5 Planner rejects unknown primitives

```bash
curl -s -X POST http://localhost:8000/api/insights/chat \
  -H "Content-Type: application/json" \
  -d '{"question":"delete all my expenses","history":[]}' | jq '.error'
# Expected: "could not interpret question"
```

### 11.6 CSV/Supabase parity

```bash
FINEYE_STORAGE_BACKEND=csv      python -m pytest tests/e2e/test_insights_parity.py -x
FINEYE_STORAGE_BACKEND=supabase python -m pytest tests/e2e/test_insights_parity.py -x
# Both pass; assertions compare response bodies for byte-identical questions
```

### 11.7 Lint, types, tests

```bash
python -m ruff check .
python -m mypy app/
python -m pytest -x
```

### 11.8 Latency smoke (manual)

```bash
# Cold cache
time curl -s "http://localhost:8000/api/insights/monthly?month=2026-04" >/dev/null
# Expect total < 8s p95

# Warm cache (run 5x, take median)
for i in 1 2 3 4 5; do
  time curl -s "http://localhost:8000/api/insights/monthly?month=2026-04" >/dev/null
done
# Expect median < 200 ms
```

---

## 12. Risks and mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | Planner failure rate > 10% on real-world questions | Medium | Medium | Log every failed question with full prompt to `data/insights_planner_failures.csv`. After 1 week, review and either expand the primitive allowlist (v1.1) or refine the planner system prompt. Visible in `/insights/chat` page footer as a "didn't catch that" UX. |
| R2 | Cold-cache p95 > 8 s | Medium | High | Aggregator parallelism: load `expenses`, `goals_b`, `investments`, `balances` concurrently with `asyncio.gather` instead of serial reads. If still slow, pre-compute trailing windows once per session. |
| R3 | Prompt drift across Groq model versions (gpt-oss-120b updates) | Low | High | Version system prompts as text files (`prompts/narrator_system.txt`, `prompts/planner_system.txt`). Snapshot LLM responses for a fixed prompt+input in a test fixture; CI fails if structure changes. Pin model name explicitly in cache key so any switch invalidates everything. |
| R4 | CSV ↔ Supabase parity drift over time | Medium | Medium | Mandatory parity test in CI (Task 12). Every new field added to `insights_cache` must have parity test coverage. PR template question: "Did you change `insights_cache` schema? If yes, ran parity test?" |
| R5 | `insights_cache` table grows unbounded (every unique question = a row) | High | Low | TTL-based pruning: `InsightsCache.prune_expired()` runs at startup and on every 100th write. Hard cap of 5,000 rows per `(owner_id, kind)` — oldest evicted first. |
| R6 | Narrator hallucinates a `{{stat:nonexistent.field}}` placeholder | Medium | Low | Frontend re-binder logs warning to console and renders the literal placeholder visibly so the bug is caught in QA, not silently broken. Do NOT crash the page. |
| R7 | Conversation history grows unboundedly client-side | Low | Low | Hard cap: client stores at most 20 turns; only the last 4 summaries are sent. |
| R8 | Two LLM calls per chat turn doubles cost vs single-call | Known | Low | Accepted trade-off — separation of concerns. Cache hit eliminates both calls. Monitor monthly Groq spend; if > $X, revisit single-call merged prompt as v1.1. |
| R9 | Vercel cold-start makes first request feel slow | Medium | Medium | Aggregator imports lazily (inside the route handler, not at module top level) so the cold-start payload stays small. Existing diagnostic wrapper in `api/index.py` already handles import failures. |
| R10 | Anomaly detection threshold (>2σ) is wrong for sparse categories | Medium | Low | Skip categories with fewer than 5 expenses in the trailing-12-month window. Document in narrator prompt that anomalies may be empty. |

---

## 13. Estimated effort

| Task | Effort (person-days) | Confidence |
|---|---|---|
| 1. Schema addition | 0.5 | H |
| 2. Supabase migration | 0.5 | H |
| 3. LLM client extension | 1.0 | H |
| 4. Aggregator with trailing windows | 2.0 | M |
| 5. Cache module + invalidator | 1.5 | M |
| 6. Narrator + contract test | 1.0 | H |
| 7. Briefing route + page + JS | 1.5 | M |
| 8. Stat-ref re-binder | 1.0 | H |
| 9. Safe-query primitives | 1.5 | M |
| 10. Chat orchestration | 2.0 | M |
| 11. Chat route + page + JS | 1.5 | M |
| 12. Parity + degradation tests | 1.0 | H |
| 13. Docs update | 0.5 | H |
| **TOTAL (serial worst case)** | **15.5** | |
| **With Lane A + Lane B parallel (one engineer on each, frontend interleaved)** | **10–12** | |
| **Buffer for unknowns / debugging** | **+1–2** | |
| **DELIVERY ESTIMATE** | **11–14 person-days** | matches the agreed window |

---

## ✅ Sign-off

- Plan author: project-planner agent
- Approved Socratic answers: Q1=A (persistent cache), Q2=B (atomic delivery), Q3=A (two LLM calls, no streaming)
- Ready for: Task 1 to begin
- File: `c:\Suyash_Projects\vaani\docs\PLAN-ai-briefing-and-rag.md`
