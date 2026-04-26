# FinEye — Personal Finance with AI (v1 Plan)

**Plan file:** `docs/PLAN-fineye-finance-ai.md`
**Mode:** Planning only (no code)
**Status:** Draft for user review
**Date:** 2026-04-23

---

## 1. Goals & Non-Goals

### Goals (v1 ships)
- Voice-driven expense capture: user speaks → STT → Groq LLM parses → row appended to ledger.
- Deterministic pandas/CSV ledger with a fixed schema for expenses.
- Manual spreadsheet-like entry in an HTML dashboard (expenses, investments, wishlist, goals, budget rules).
- Seed-balance page (cash balance, online balance) with ATM-withdrawal auto-transfer logic.
- Rule-driven **Budget Overflow Engine** (per-category cap → next month carry → Medical → Emergency Monthly).
- Investment monthly grid with user-defined columns on the fly.
- Need/Wish table with target + saved-so-far.
- Three goal/budget tables (A Goals, B Saved, C Budget/Overflow).
- Visualization dashboard with a **chart registry** (config-driven) so new charts declare data+agg without code changes.
- Google Sheets backup with local write-ahead so no entry is lost on outage.
- Adaptive "uniques" dictionary passed to Groq for vendor/category recognition.

### Non-Goals (explicitly out of v1)
- Multi-user / multi-tenant. v1 is **single-user, local-first**.
- Mobile-native app (v1 is HTML dashboard; mobile browsers OK).
- Bank/credit-card OAuth feed ingestion.
- OCR of receipts.
- Tax computation, GST/TDS, or investment advice.
- Real-time collaborative editing.
- Currency other than a single user-configured currency (default INR).
- Offline-first PWA (v1 assumes laptop is online most of the time; write-ahead buffers short outages only).
- Auth/SSO. v1 runs on localhost only, no login.

---

## 2. Open Questions (Socratic Gate — needs user decisions)

These are material ambiguities. Defaults are proposed but should be confirmed.

| # | Question | Default proposal | Impact if wrong |
|---|----------|------------------|-----------------|
| Q1 | **Groq API** — which model? | `llama-3.3-70b-versatile` via `https://api.groq.com/openai/v1` (OpenAI-compatible); key in `.env` as `GROQ_API_KEY` | Model choice affects JSON-mode quality + speed |
| Q2 | **STT provider** — browser Web Speech API vs cloud (Whisper / Deepgram / Google STT)? | Web Speech API (free, Chrome/Edge) with fallback to OpenAI Whisper local | Affects accuracy, offline capability, cost |
| Q3 | **Single-user confirmed?** Any auth needed? | Yes, single-user; no auth; localhost only | Multi-user would change storage layer |
| Q4 | **Device scope** — desktop only, or mobile browser too? | Desktop Chromium + responsive for mobile web | Affects voice capture permissions flow |
| Q5 | **Currency & locale** | INR, `en-IN`, timezone `Asia/Kolkata` | Affects number formatting + daily-cutoff |
| Q6 | **Day boundary for "daily total"** | Local midnight `Asia/Kolkata` | Affects report alignment |
| Q7 | **"Uniques" list format** — JSON file, YAML, or table in dashboard? | `data/uniques.json` editable from UI + pandas-backed | Affects prompt injection & admin UX |
| Q8 | **Budget caps configuration** — per-category static JSON vs editable table? | Editable table in UI, persisted to `budgets.csv` | Affects engine complexity |
| Q9 | **Google Sheets auth** — Service Account (JSON key) or OAuth user flow? | Service Account (simpler for single-user, Sheet pre-shared) | OAuth adds refresh-token plumbing |
| Q10 | **Sheets conflict resolution** — local is source of truth? | Yes. Sheets = mirror. Local wins on divergence. | If Sheets is source of truth, sync is bi-dir |
| Q11 | **Offline behavior** | Write-ahead log (`.wal/` JSONL); flush on reconnect | Affects UX when offline |
| Q12 | **Investment "add column on the fly"** — does it apply retroactively to prior months (null-backfill) or forward only? | Forward-only; new col = NaN in prior rows | Affects schema migration |
| Q13 | **Goals Table A vs B** — are these two distinct tables or two views of one? | Two distinct tables with separate CSVs (A = overview, B = source-breakdown) | Data-model divergence |
| Q14 | **Carry-buffer cap per category** — user-defined per category? | Yes; `carry_cap` column in budgets.csv | Affects overflow engine |
| Q15 | **Medical & Emergency caps** — fixed or user-editable? | User-editable, persisted in `meta.json` | — |
| Q16 | **Groq response language** | English; JSON strict mode | — |
| Q17 | **Voice command trigger** | Push-to-talk button (hold to record) | Always-on listening = privacy risk |
| Q18 | **Charts library** | Chart.js (light) primary; Plotly optional for advanced | Bundle size vs feature set |

---

## 3. Architecture Overview

```
┌──────────────────────────────────────────────────────────────────────────────┐
│                            BROWSER (HTML Dashboard)                          │
│  ┌─────────────┐  ┌──────────────┐  ┌────────────┐  ┌─────────────────────┐  │
│  │ Voice Btn   │  │ Expense Grid │  │ Invest Grid│  │ Charts (registry)   │  │
│  │ (PTT STT)   │  │ (spreadsheet)│  │ (dyn cols) │  │ pie / stack / donut │  │
│  └──────┬──────┘  └──────┬───────┘  └──────┬─────┘  └──────────┬──────────┘  │
│         │                │                 │                    │             │
│         ▼                ▼                 ▼                    ▼             │
│                       fetch /api/*   (JSON over HTTP)                         │
└──────────────────────────────────────┬───────────────────────────────────────┘
                                       │
┌──────────────────────────────────────▼───────────────────────────────────────┐
│                          FASTAPI BACKEND (localhost:8000)                     │
│                                                                               │
│  /api/voice/transcribe  ──► STT adapter (WebSpeech | Whisper)                │
│         │                                                                     │
│         ▼                                                                     │
│  /api/expense/parse  ──► Groq Parser ──► JSON Schema Validator               │
│         │                   ▲                                                 │
│         │                   │ uniques.json (dictionary injection)             │
│         ▼                                                                     │
│  Ledger Writer  ──►  WAL (.wal/*.jsonl)  ──►  pandas DataFrame ──► CSV       │
│         │                                              │                      │
│         │                                              ▼                      │
│         │                                     Budget Overflow Engine          │
│         │                                              │                      │
│         ▼                                              ▼                      │
│  Google Sheets Sync Worker (async)  ◄───────── recompute tables               │
│         │                                                                     │
│         ▼                                                                     │
│   Google Sheets (mirror/backup)                                               │
└───────────────────────────────────────────────────────────────────────────────┘

DATA FLOW (voice happy path):
  speak → WebSpeech → text → POST /api/expense/parse → Groq → JSON →
  validate → WAL append → pandas append → CSV flush → Sheets queue → UI refresh
```

**Component responsibilities:**

| Component | Responsibility |
|-----------|----------------|
| Browser UI | Capture input (voice/manual), render grids & charts |
| STT adapter | Pluggable: `web_speech` (client) or `whisper` (server) |
| Groq Parser | Prompt + uniques injection + JSON-mode call, retry |
| JSON Validator | pydantic schema; rejects malformed; one retry with repair prompt |
| Ledger Writer | Transactional: WAL → pandas → CSV (fsync) → enqueue Sheets |
| Budget Engine | Pure function `(expenses, budgets, meta) → table_C` |
| Sheets Sync | Async worker; exponential backoff; idempotent by row_id |
| Chart Registry | Reads `charts.yaml`; each entry = {source, group_by, agg, type} |

---

## 4. Data Model (pandas schemas)

All tables live as CSV under `data/`. Primary keys are ULIDs (sortable, collision-free).

### 4.1 `expenses.csv`

**IMPORTANT — single ledger of truth.** `expenses.csv` records **every outflow the user makes**, regardless of whether it is a Need, a Want, or an Investment. Examples that MUST land here:

- Buying groceries (`type_category="Need, Food & Drinks"`)
- A movie ticket (`type_category="Want, Enjoyment"`)
- Monthly SIP debit, FD creation, crypto buy, NPS contribution — all stored as `type_category="Investment, Miscellaneous"` (or an Investment sub-category if added later)
- Wishlist contributions (money moved toward a wishlist item) also appear here with `type_category="Investment, Miscellaneous"` and a `notes` reference to the wishlist item id

This is deliberate: the dashboard's cumulative Need/Want/Investment pie (§8) reads **solely** from `expenses.csv`. If investment spends were split into a separate table, the pie would lie.

**Relationship to `investments.csv` (§4.3):** `investments.csv` is a **monthly planning/aggregate** table the user fills from the dashboard — it is NOT a per-transaction ledger. The two are **independent**: `expenses.csv` records what actually went out of cash/online, while `investments.csv` records the user's intended/categorised monthly split across Long Term, Mid/Long Term, Emergency Fund, etc. A reconciliation view in M4 will show divergence if the user wants it, but neither table is derived from the other.



| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `id` | str (ULID) | No | PK |
| `date` | date (ISO) | No | Local date `YYYY-MM-DD` |
| `created_at` | datetime UTC | No | Audit |
| `expense_name` | str | No | Free text |
| `type_category` | str | No | `"Need, Food & Drinks"` combined (comma + space), regex `^(Need\|Want\|Investment), (Food & Drinks\|Travel\|Enjoyment\|Miscellaneous)$`. Storage flipped from `:`-joined to `, `-joined on 2026-04-23 (idempotent bootstrap migration). |
| `payment_method` | str | No | 5-value enum (2026-04-23): `paid` (online/upi/card/gpay/phonepe), `paid_cash`, `paid_by` (someone else paid), `paid_for` (user paid for someone), `adjusted` (balance transfer — does NOT write an expense row) |
| `paid_for_method` | str | Yes | `cash` \| `online`. Required iff `payment_method == "paid_for"`. |
| `adjustment_type` | str | Yes | `cash_to_online` \| `online_to_cash`. Required iff `payment_method == "adjusted"` (but adjusted rows are never written to expenses.csv — captured for audit completeness when carried in-flight). |
| `paid_for_someone` | bool | No | **DEPRECATED 2026-04-23** — kept for backward-compat reads; derived from `payment_method == "paid_for"` on new writes. |
| `paid_by_someone` | bool | No | **DEPRECATED 2026-04-23** — kept for backward-compat reads; derived from `payment_method == "paid_by"`. |
| `person_name` | str | Yes | required if `payment_method` is `paid_by` or `paid_for`. |
| `amount` | float | No | positive; INR |
| `cash_balance_after` | float | No | snapshot |
| `online_balance_after` | float | No | snapshot |
| `source` | str | No | enum: `voice`, `manual`, `atm_transfer` |
| `raw_transcript` | str | Yes | original voice text (for audit) |
| `notes` | str | Yes | |
| `custom_tag` | str | Yes | **Added M4 (2026-04-23):** user-defined tag consumed by the Budget Overflow Engine for rules that don't map to a Need/Want/Investment suffix (e.g. `electricity`, `utilities`). Nullable; additive schema change. |

**Indexes (logical):** `date`, `(date, type_category)`.

**Daily / monthly totals** are derived views, not stored columns:
- `daily_total(d) = expenses[date == d].amount.sum()`
- `monthly_total(m) = expenses[date.month == m].amount.sum()`

### 4.2 `balances.csv` (seed + running)

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `asof` | datetime UTC | No | |
| `cash_balance` | float | No | |
| `online_balance` | float | No | |
| `reason` | str | No | `seed`, `expense`, `atm_withdraw`, `manual_adjust` |

Append-only. Current balance = last row.

### 4.3 `investments.csv`

Fixed starting columns + user-defined columns.

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `month` | str | No | PK. `YYYY-MM` |
| `long_term` | float | Yes | |
| `mid_long_term` | float | Yes | |
| `emergency_fund` | float | Yes | |
| `bike_savings_wants` | float | Yes | |
| `misc_spend_save` | float | Yes | |
| `fixed_deposits` | float | Yes | |
| `total` | float | No | computed = sum of numeric columns excluding `total` |
| `<user_col_*>` | float | Yes | dynamically added; registered in `investment_columns.json` |

User-defined columns are persisted via a **column registry**:

```json
// data/meta/investment_columns.json
{
  "columns": [
    {"key": "long_term", "label": "Long Term", "builtin": true},
    {"key": "crypto", "label": "Crypto", "builtin": false, "added_at": "2026-05-01"}
  ]
}
```

Forward-only: new columns NaN for prior months.

> **Note (2026-04-23):** User-defined columns are no longer an investments-only feature. Every CSV table (expenses, wishlist, goals_a, goals_b, balances, budget_rules, investments) supports them via the universal registry at `data/meta/user_columns/{table}.json` and the `/api/tables/{table}/columns` endpoints. The legacy `investment_columns.json` file is auto-migrated on first read and preserved. Delete removes from the registry only — CSV column is retained (audit safety).

### 4.4 `wishlist.csv` (Need/Wish)

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `id` | str (ULID) | No | PK |
| `item` | str | No | |
| `target_amount` | float | No | |
| `saved_so_far` | float | No | default 0 |
| `priority` | str | Yes | `high`/`med`/`low` |
| `notes` | str | Yes | free text (added M3) |
| `link` | str | Yes | optional URL (added M3) |
| `source` | str | No | `manual` / `ai` |
| `created_at` | datetime | No | |
| `status` | str | No | `active`/`achieved`/`abandoned` |

**Contribution semantics (M3):** `POST /api/wishlist/{id}/contribute` with `source="expense"` bumps `saved_so_far` AND writes a row to `expenses.csv` with `type_category="Investment, Miscellaneous"` and `notes="wishlist:{id}"` per §4.1 single-ledger principle. `source="manual"` only updates `saved_so_far`. When `saved_so_far >= target_amount`, status auto-flips to `achieved`. Contributions get fresh ULIDs (no dedup-key) so repeating the same amount is allowed.

### 4.5 `goals_a.csv` — Overview

| Column | Type | Nullable |
|--------|------|----------|
| `goal_id` (PK) | str | No |
| `goal_name` | str | No |
| `target_amount` | float | No |
| `current_amount` | float | No |
| `monthly_contribution` | float | No |
| `pct_complete` | float | No (derived) |
| `months_left` | int | No (derived) |
| `status` | str | No |

### 4.6 `goals_b.csv` — Source Breakdown

| Column | Type | Nullable |
|--------|------|----------|
| `goal_id` (PK) | str | No |
| `goal_name` | str | No |
| `target_amount` | float | No |
| `manual_saved` | float | No |
| `auto_added` | float | No |
| `total_saved` | float | No (derived = manual + auto) |
| `monthly_contribution` | float | No |
| `pct_complete` | float | No (derived) |
| `months_left` | int | No (derived) |
| `status` | str | No |

### 4.7 `budget_rules.csv` (user-editable)

| Column | Type | Nullable | Notes |
|--------|------|----------|-------|
| `category` | str | No | PK. matches `type_category` suffix or custom |
| `monthly_budget` | float | No | e.g., ₹3000 |
| `carry_cap` | float | No | upper bound after rollover |
| `priority` | int | No | eval order |

### 4.8 `budget_table_c.csv` — computed monthly snapshot

| Column | Type | Nullable |
|--------|------|----------|
| `month` (PK) | str | No |
| `category` (PK) | str | No |
| `budget` | float | No |
| `actual` | float | No |
| `remaining` | float | No |
| `carry_buffer` | float | No |
| `overflow` | float | No |
| `to_medical` | float | No |
| `to_emergency` | float | No |
| `med_balance` | float | No |
| `emerg_balance` | float | No |

Recomputed from `expenses` + `budget_rules` + `meta.caps` on every write.

### 4.9 `meta.json`

```json
{
  "currency": "INR",
  "timezone": "Asia/Kolkata",
  "caps": {
    "medical_upper_cap": 10000,
    "emergency_monthly_cap": 5000
  }
}
```

### 4.10a Timezone & Locale Handling

- **Default:** `Asia/Kolkata` (IST, UTC+5:30), locale `en-IN`, currency `INR`.
- **User-settable:** Settings page exposes timezone (IANA picker) and locale. Persists to `meta.json`.
- **Server:** all `datetime` values stored in UTC; "date" columns are the user's **local** date resolved via `zoneinfo.ZoneInfo(meta.timezone)`.
- **Day boundary:** daily totals cut over at local midnight in the user's configured TZ.
- **Client:** browser never derives dates locally — always receives pre-formatted strings from server to avoid DST/locale drift.
- **Migration:** changing TZ after data exists does **not** rewrite historical `date` columns (they remain as recorded). A warning banner explains this.

### 4.10b Excel / CSV Import (bulk ingestion of existing sheets)

User can upload an existing Excel (`.xlsx`, `.xls`) or CSV file to seed any of the four data tables (expenses, investments, wishlist, goals). Implemented as a **multi-step wizard**:

**Step 1 — Upload & Sniff**
- `POST /api/import/upload` — accepts multipart file ≤ 10 MB.
- Backend reads with `pandas.read_excel` (via `openpyxl`) or `pandas.read_csv` (auto-detect delimiter, encoding via `chardet`).
- If `.xlsx` has multiple sheets, user picks one.
- Response: first 20 rows + detected columns + dtype guesses.

**Step 2 — Column Mapping**
- UI shows source columns → target schema dropdowns.
- Backend pre-suggests mappings by fuzzy name match (`rapidfuzz`) — e.g. source `"Amt"` → target `amount` (confidence 0.87).
- Unmapped target columns fall back to defaults (e.g., `source="import"`, `created_at=now`, ULID auto-assigned).

**Step 3 — Value Normalisation & Dry-Run Validation**
- Date parsing: try `dateutil`, fall back to user-specified format string.
- Enum coercion: `"need"`, `"NEED"`, `"Need "` → `Need`. Unmatched values flagged.
- Payment-method heuristic: if value contains "upi/card/online/net" → `paid`; else `cash`.
- `type_category` auto-combine: if source has two separate columns, combine as `"{type}, {category}"`.
- Amount cleanup: strip `₹`, commas, spaces; parse float.
- Validation runs the **same pydantic schema** as voice pipeline (§5.3).
- Dry-run report: `N valid rows`, `M invalid rows` with reasons (table view, downloadable).

**Step 4 — Commit**
- User chooses: **skip invalid** / **abort on any invalid** / **import invalid as drafts** (into `drafts.csv` for manual fix).
- **Idempotency:** ULID assigned from a deterministic hash of `(date, expense_name, amount, payment_method, person_name)` so re-uploading the same file does not double-insert. Existing IDs are skipped with a "N duplicates ignored" summary.
- Write path: same WAL → pandas → CSV → Sheets queue as the rest of the app.

**Step 5 — Audit Trail**
- Each imported batch logs to `data/imports/{batch_id}.meta.json` with: filename, sha256, row count, mapping, timestamp. Enables traceability + rollback (`DELETE /api/import/:batch_id` removes rows tagged with that batch).

**Endpoints:**
- `POST /api/import/upload` — returns `upload_id` + preview
- `POST /api/import/:upload_id/map` — persists column mapping, returns dry-run report
- `POST /api/import/:upload_id/commit` — writes rows, returns summary
- `DELETE /api/import/:batch_id` — rollback

**Import Presets (added 2026-04-23):**

For layouts the user imports repeatedly (e.g. their personal Excel ledger, bank statements), column mapping is captured once as a **preset** in `data/meta/import_presets.json`. `POST /api/import/{upload_id}/map` accepts `preset_id` which overrides `mapping`, `date_format`, and `row_filters`. `GET /api/import/presets` lists available presets.

The first shipped preset is `personal_ledger_v1` — matches the user's real ledger:

```json
{
  "id": "personal_ledger_v1",
  "label": "My personal ledger (DD/MM/YYYY - combined tags - daily totals)",
  "target_table": "expenses",
  "date_format": "%d/%m/%Y",
  "column_mapping": {
    "Date": "date",
    "Vendor": "vendor",
    "Payment": "__payment_dual",
    "Tags": "__tags_combined",
    "Item": "expense_name",
    "Amount": "amount",
    "Cash balance": "__cash_snapshot",
    "Online balance": "__online_snapshot"
  },
  "row_filters": {
    "skip_when_payment_equals": ["Total"],
    "detect_balance_adjust": true
  }
}
```

Synthetic target names (`__payment_dual`, `__tags_combined`, `__cash_snapshot`, `__online_snapshot`) are interpreted by the committer's preset pre-processor — they trigger: tag parsing with plural/singular canonicalisation ("Wants" → "Want", "Travel" ↔ "Transport"), `Paid`/`Paid Cash` payment disambiguation, daily-Total-row skipping with per-day checksum capture, and balance-adjust-row detection (zero amount + empty payment + non-zero cash delta → `balances.csv` with `reason="manual_adjust"`). The dry-run report gains a `checksum_report` field with `{day, computed_total, declared_total, match, delta}` entries.

**Target-table support matrix (v1):**

| Target | Supported | Notes |
|--------|-----------|-------|
| `expenses` | ✓ | full wizard, dedup, drafts branch |
| `investments` | ✓ | row = month; user columns auto-registered |
| `wishlist` | ✓ | `source="import"` |
| `goals_a` / `goals_b` | ✓ | shared uploader |
| `balances` | ✗ | seed via dedicated page only (single source of truth) |

### 4.11 `uniques.json` (adaptive dictionary)

```json
{
  "vendors": {
    "zomato": {"category": "Food & Drinks", "type": "Want"},
    "swiggy instamart": {"category": "Miscellaneous", "type": "Need"},
    "hpcl": {"category": "Travel", "type": "Need"}
  },
  "aliases": {
    "bros": "Brotherhood Cafe",
    "petrol": "HPCL"
  }
}
```

---

## 5. Voice → Ledger Pipeline

### 5.1 STT
- **Primary:** Web Speech API (client-side, Chrome/Edge). Free, low latency.
- **Fallback:** OpenAI Whisper (local `faster-whisper` on server) if browser unsupported or accuracy poor.
- Push-to-talk; audio never stored, only transcript.

### 5.2 Prompt Design for Groq

System prompt (truncated):
```
You are an expense parser. Input: one spoken transcript.
Output: strict JSON matching SCHEMA. No prose, no markdown fences.
Use the provided `uniques` dictionary to resolve vendors and aliases.
If the user says "withdrew cash from ATM", emit action="atm_transfer".
If amount missing → set "needs_clarification": true with "question".
```

User message payload:
```json
{
  "transcript": "spent 250 at zomato with upi",
  "today": "2026-04-23",
  "currency": "INR",
  "uniques": { ...current uniques.json... },
  "last_known_balances": {"cash": 1200.0, "online": 45000.0}
}
```

### 5.3 Strict JSON Output Schema (pydantic)

```python
class ParsedExpense(BaseModel):
    action: Literal["expense", "atm_transfer", "clarify"]
    date: date
    expense_name: Optional[str]
    type_category: Optional[str]  # regex validated, "Type, Category" (comma + space)
    payment_method: Optional[Literal["paid", "paid_cash", "paid_by", "paid_for", "adjusted"]]
    paid_for_method: Optional[Literal["cash", "online"]] = None   # iff payment_method=="paid_for"
    adjustment_type: Optional[Literal["cash_to_online", "online_to_cash"]] = None  # iff payment_method=="adjusted"
    paid_for_someone: bool = False   # deprecated; derived from payment_method
    paid_by_someone: bool = False    # deprecated; derived from payment_method
    person_name: Optional[str]
    amount: Optional[float]
    atm_amount: Optional[float]  # set when action=atm_transfer
    needs_clarification: bool = False
    question: Optional[str]
    confidence: float  # 0..1
```

### 5.4 Validation & Retry
1. Call Groq with `response_format={"type":"json_object"}`.
2. Parse → `ParsedExpense`.
3. On `ValidationError` or JSONDecodeError → **one retry** with repair prompt: *"Your previous output failed validation: {err}. Return valid JSON only."*
4. On second failure → return HTTP 422 to UI with raw transcript preserved; user edits manually.

### 5.5 Uniques adaptation
- `uniques.json` is injected into every Groq call (few-shot dictionary).
- UI has "Teach" button: when AI misclassifies and user corrects inline, we PATCH `uniques.json` with the new mapping.
- Size guard: if `uniques` exceeds ~4KB, compress to only entries used in last 90 days.

### 5.6 ATM Withdrawal Special Case
Triggered when `action == "atm_transfer"`:
- Do NOT write to `expenses.csv`.
- Append to `balances.csv`:
  - `cash_balance += atm_amount`
  - `online_balance -= atm_amount`
  - `reason = "atm_withdraw"`
- No double-entry into expenses ledger.

---

## 6. Manual Entry UI (HTML Dashboard)

Pages (single-page app or multi-page — Jinja2 server-rendered + vanilla JS / HTMX):

| Page | Route | Content |
|------|-------|---------|
| Dashboard Home | `/` | KPIs (daily/monthly total), last 5 entries, voice button |
| Expenses Grid | `/expenses` | Spreadsheet-like grid (Handsontable or ag-Grid community); inline edit; bulk paste |
| Balances Seed | `/balances` | Two inputs (cash/online) + history table |
| Investments | `/investments` | Monthly grid; "+ Add Column" button (opens modal: label, key) |
| Wishlist | `/wishlist` | Table with add/edit/delete; AI-added rows flagged |
| Goals A | `/goals/overview` | Table A |
| Goals B | `/goals/sources` | Table B |
| Budget Rules | `/budgets` | Per-category budget + carry_cap editor; Medical/Emergency caps |
| Budget Table C | `/budgets/monthly` | Read-only computed view |
| Charts | `/charts` | Registry-driven chart grid |
| Settings | `/settings` | Currency, tz, Groq key, Sheets URL, uniques editor |

Grid library choice: **Handsontable community** (MIT-ish for personal use) or **ag-Grid Community**. Decide in M1.

### 6.1 Dropdown-Chip Inputs for Fixed-Variable Columns

For manual entry, columns whose values are drawn from a **closed/curated vocabulary** must render as a **dropdown-chip cell editor**, not a free-text input. This keeps manual entries aligned with the same vocabulary Groq uses, so charts and aggregations don't fragment on typos (e.g., "travel" vs "Travel" vs "travl").

| Column | Source of options | Behavior |
|--------|-------------------|----------|
| `expense_type` (Need / Want / Investment) | Fixed enum in `meta.json` | Single-select chip. No free-text. |
| `category` (Food & Drinks / Travel / Enjoyment / Misc) | Fixed enum in `meta.json` | Single-select chip. No free-text. |
| `payment_method` (paid / paid_cash / paid_by / paid_for / adjusted) | Fixed 5-value enum (2026-04-23) | Single-select chip. Variants: `hot-chip-cell--paid`, `--cash`, `--paid-by`, `--paid-for`, `--adjusted`. |
| `paid_for_method` (cash / online) | Fixed enum | Conditional column: shows `—` unless `payment_method == "paid_for"`. |
| `adjustment_type` (cash_to_online / online_to_cash) | Fixed enum | Conditional column: shows `—` unless `payment_method == "adjusted"`. Auto-focus on flip. |
| `person` (counterparty name) | `uniques.json → people[]` | **Searchable** chip with autocomplete; typing a new name offers "+ Add 'X' to people" which writes back to `uniques.json`. |
| `vendor` / `expense_name` aliases (optional) | `uniques.json → vendors[]` | Same as `person` — searchable chip with inline-add. |

Rules:
- Chips render inside Handsontable/ag-Grid cells using each lib's native dropdown editor (Handsontable `type: 'dropdown'` / ag-Grid `agRichSelectCellEditor`).
- Writes go through the same validation path as the voice pipeline (§5), so no row can be saved with a value outside the vocabulary for a closed-enum column.
- Inline-adds to `uniques.json` are **forward-only** (same rule as investment columns) — deleting a person from the uniques list does not touch historical rows.
- Investment grid's user-defined columns are **free-text numeric** — chips do not apply there.
- Settings page (`/settings`) exposes a "Vocabulary" editor: view/reorder/rename closed enums (with a confirmation warning since renaming affects charts), and manage `people` / `vendors` lists with a usage count column.

Open question **Q19** (append to §2 Open Questions): *Should closed enums (expense_type, category, paid_type, paid_direction) be user-editable from Settings, or locked at code level?* Default proposal: **locked for v1** (simpler; avoids breaking chart registry); people/vendors remain user-editable.

---

## 7. Budget Overflow Engine

### 7.1 Inputs
- `expenses_month`: rows for month M
- `budget_rules`: category → {monthly_budget, carry_cap}
- `prior_carry`: category → carried-in from M-1
- `meta.caps`: medical_upper_cap, emergency_monthly_cap
- `med_balance_in`, `emerg_balance_in`: from M-1

### 7.2 Algorithm (pseudo-code)

```
function compute_month(M, rules, expenses, prior_carry, caps, med_in, emerg_in):
    rows = []
    med_balance = med_in
    emerg_balance = emerg_in

    for rule in sorted(rules, by=priority):
        cat = rule.category
        budget_effective = rule.monthly_budget + prior_carry.get(cat, 0)
        actual = sum(expenses where category == cat)
        remaining = budget_effective - actual

        if remaining <= 0:
            carry_next = 0
            overflow = 0
        else:
            carry_next = min(remaining, rule.carry_cap)
            overflow = remaining - carry_next   # beyond carry_cap

        to_medical = 0
        to_emergency = 0
        if overflow > 0:
            med_room = caps.medical_upper_cap - med_balance
            to_medical = min(overflow, max(med_room, 0))
            med_balance += to_medical
            residual = overflow - to_medical

            if residual > 0:
                emerg_room = caps.emergency_monthly_cap - emerg_balance
                to_emergency = min(residual, max(emerg_room, 0))
                emerg_balance += to_emergency
                # any remainder beyond both caps is "lost" — log warning

        rows.append({
            month: M, category: cat,
            budget: rule.monthly_budget,
            actual, remaining,
            carry_buffer: carry_next,
            overflow,
            to_medical, to_emergency,
            med_balance, emerg_balance
        })
        next_carry[cat] = carry_next

    return rows, next_carry, med_balance, emerg_balance
```

### 7.3 Worked Example (user's ₹3000 / ₹2500 case)

Rule: `electricity`, `monthly_budget=3000`, `carry_cap=4000`.
Caps: `medical_upper_cap=10000`, `emergency_monthly_cap=5000`.

**Month 1:** actual = ₹2500 → remaining = ₹500 → carry_next = min(500, 4000) = ₹500, overflow = 0.
**Month 2:** budget_effective = 3000 + 500 = 3500. Say actual = ₹2000 → remaining = 1500 → carry_next = min(1500, 4000) = 1500, overflow = 0.
**Month 3:** budget_effective = 4500. Say actual = ₹200 → remaining = 4300 → carry_next = min(4300, 4000) = 4000, **overflow = 300** → to_medical = min(300, 10000) = 300 → med_balance = 300, to_emergency = 0.
**Month 4:** over many months, med_balance hits 10000 cap; subsequent overflow flows to emergency up to its ₹5000 monthly cap. Anything beyond both is logged as `warning: overflow_lost` in `table_c.notes`.

### 7.4 Determinism
- Pure function. No DB, no I/O. Re-runnable from raw CSVs.
- Unit-testable with fixtures.

---

## 8. Visualizations (Rule-Driven Registry)

### 8.1 Registry file — `data/meta/charts.yaml`

```yaml
charts:
  - id: cumulative_types_pie
    title: "Cumulative Need/Want/Investment"
    source: expenses
    type: pie
    group_by: "type"      # parsed from type_category prefix
    agg: sum(amount)
    filter: "all"

  - id: monthly_stack
    title: "Monthly Expenses by Type"
    source: expenses
    type: stacked_bar
    x: month
    series: type
    agg: sum(amount)

  - id: category_donut
    title: "Category Breakdown"
    source: expenses
    type: donut
    group_by: "category"
    agg: sum(amount)

  - id: goal_tracking
    title: "Goal Progress"
    source: goals_a
    type: horizontal_bar
    x: goal_name
    series: [current_amount, target_amount]

  - id: daily_spend_line
    title: "Daily Spend (Last 30 Days)"
    source: expenses
    type: line
    x: date
    time_bucket: day
    agg: sum(amount)
    filter: "date >= '2026-03-24'"

  - id: top_vendors
    title: "Top 10 Vendors (Last 90 Days)"
    source: expenses
    type: bar
    x: expense_name
    agg: sum(amount)
    top_n: 10
    top_n_other: true
    order_by: value_desc
    filter: "date >= '2026-01-23'"
```

### 8.2 Runtime
A generic renderer reads `charts.yaml`, groups/aggregates the named CSV via pandas, and emits Chart.js config JSON at `/api/charts/:id`.

Adding a new chart = append YAML entry. No code changes.

### 8.3 Library
- **Primary:** Chart.js 4 (light, declarative).
- **Secondary:** Plotly.js when a chart needs interactivity (hover drilldowns).

---

## 9. Storage & Backup

### 9.1 On-disk layout
```
data/
  expenses.csv
  balances.csv
  investments.csv
  wishlist.csv
  goals_a.csv
  goals_b.csv
  budget_rules.csv
  budget_table_c.csv
  meta.json
  uniques.json
  meta/
    investment_columns.json
    charts.yaml
.wal/
  2026-04-23.jsonl         # append-only write-ahead log
```

### 9.2 Read/Write pattern
- Read: `pd.read_csv(path, dtype=SCHEMA, parse_dates=["date"])` on first use; in-memory cache invalidated on write.
- Write: WAL append → mutate DataFrame → `df.to_csv(path + ".tmp")` → `os.replace()` (atomic on POSIX; on Windows, atomic rename works for same volume) → remove WAL entry on confirm.
- Lock: single-process FastAPI; use `threading.Lock` per-file.

### 9.3 Google Sheets Sync

**Auth:** Service Account JSON, Sheet pre-shared with service account email.
**Library:** `gspread` + `google-auth`.

**Sync protocol:**
1. Each CSV maps to one Sheet tab (same name).
2. After every successful local write, enqueue a `SyncJob(tab, row_id, op)` into an in-memory asyncio queue.
3. Background worker processes queue:
   - On `append` or `update`: upsert by `id` column (ULID).
   - Retries with exponential backoff (1s, 2s, 4s, 8s, 30s cap).
   - Failures logged to `.wal/sheets_pending.jsonl` — replayed on next startup.
4. **Conflict resolution:** local CSV is source of truth. Sheets is a **mirror**. On startup, if Sheets has rows not in local (manual edits in Sheet), prompt user: "Pull X rows from Sheet?" (v1 default: log + ignore; nothing overwrites local silently).
5. **"No data is ever lost" guarantee:** entry is considered durable once CSV write succeeds AND WAL entry cleared. Sheets outage never blocks the write path.

---

## 10. Tech Stack Recommendation

| Layer | Pick | One-liner | Open? |
|-------|------|-----------|-------|
| Language (backend) | Python 3.11+ | pandas is first-class, great for this workload | — |
| Web framework | FastAPI | async, pydantic built-in, auto docs | — |
| Templating | Jinja2 + HTMX | minimal JS, fast to build | Could swap for React if complexity grows |
| Grid component | Handsontable (community) | Excel-like feel out of box | vs ag-Grid — decide M1 |
| Charts | Chart.js 4 | lightweight, declarative | Plotly if interactivity needed |
| STT | Web Speech API | zero cost, decent for English | Fallback: faster-whisper local |
| LLM | Groq (`llama-3.3-70b-versatile`) | fast LPU inference, OpenAI-compatible API | Q1 — confirm model |
| LLM call lib | `httpx` (async) | no SDK lock-in | — |
| Validation | pydantic v2 | strict JSON parsing | — |
| Storage | CSV + pandas | brief mandates it | Could upgrade to SQLite if scale bites |
| Backup | Google Sheets via `gspread` | user-specified | — |
| IDs | `ulid-py` | sortable, no collisions | — |
| Process mgmt | `uvicorn` | standard | — |
| Config | `.env` via `python-dotenv` | simple | — |
| Tests | `pytest` + `hypothesis` | property tests for overflow engine | — |

---

## 11. Task Breakdown (Milestones)

### M0 — Scaffold (½ day)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M0.1 | Init repo, `pyproject.toml`, FastAPI skeleton, `.env.example` | backend-specialist | — | 1h | empty dir → runnable `uvicorn app:app` → `curl localhost:8000/health` → 200 |
| M0.2 | Create `data/` + empty CSV headers via bootstrap script | database-architect | M0.1 | 30m | schema list → 8 CSVs with headers → `head -1 data/*.csv` matches schemas |
| M0.3 | Static HTML dashboard shell (nav + empty pages) | frontend-specialist | M0.1 | 1h | mockup → Jinja2 base template + 10 routes returning placeholder → visit each URL, see page |

### M1 — Manual Expense Grid + CSV (1 day)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M1.1 | Ledger Writer module (pandas + WAL + atomic CSV) | backend-specialist | M0.2 | 3h | `LedgerWriter.append(row)` → row in CSV + WAL cleared → pytest: kill mid-write, recover |
| M1.2 | `/api/expenses` CRUD endpoints | backend-specialist | M1.1 | 2h | OpenAPI spec → endpoints → `curl POST` adds row, GET returns it |
| M1.3 | Expense grid UI (Handsontable) with inline add/edit | frontend-specialist | M1.2 | 3h | design → editable grid → type row, tab out, see it in CSV |
| M1.4 | Balances seed page + `/api/balances` | backend-specialist + frontend-specialist | M1.1 | 2h | form → balances.csv append → UI shows current balance |
| M1.5 | Daily/monthly totals computed view | backend-specialist | M1.1 | 1h | expenses → `/api/reports/totals?date=` → JSON → pytest fixtures |

### M2 — Voice + Groq Pipeline (1–2 days)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M2.1 | Web Speech STT integration (push-to-talk button) | frontend-specialist | M0.3 | 2h | mic press → transcript shown in UI → speak, text appears |
| M2.2 | Groq client (`httpx`) with prompt template | backend-specialist | M1.1, Q1 | 3h | transcript → Groq → raw JSON → mocked test passes |
| M2.3 | pydantic `ParsedExpense` schema + validator + retry | backend-specialist | M2.2 | 2h | raw Groq output → valid obj OR 422 → property tests |
| M2.4 | `/api/expense/parse` endpoint wiring STT→Groq→Ledger | backend-specialist | M2.3, M1.1 | 2h | transcript POST → row in expenses.csv → e2e curl test |
| M2.5 | ATM-transfer branch (no expense row, balances mutation) | backend-specialist | M2.4 | 1h | "withdrew 2000 from ATM" → balances.csv +/- 2000 → UI reflects |
| M2.6 | Uniques injection + "Teach" inline correction UI | frontend + backend | M2.4 | 2h | correct a row → uniques.json updated → next similar transcript classified correctly |

### M3 — Investments + Wishlist (1 day)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M3.1 | Investment CSV + column registry | database-architect | M0.2 | 1h | registry JSON → read/write works, total computed |
| M3.2 | Investment grid UI with "+ Add Column" modal | frontend-specialist | M3.1 | 3h | click add col → new NaN column → persists on reload |
| M3.3 | Wishlist CRUD API + UI | backend + frontend | M1.1 | 2h | add item → row + UI list |

### M4 — Budget Overflow Engine (1 day)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M4.1 | Pure `overflow.compute_month` function | backend-specialist | M1.1 | 3h | fixtures → rows → hypothesis tests incl. ₹3000/₹2500 scenario |
| M4.2 | Budget rules CRUD + caps editor UI | backend + frontend | M4.1 | 2h | edit budget → persists → re-run engine reflects |
| M4.3 | Table C page (read-only computed) | frontend-specialist | M4.1 | 1h | page renders current month rows |
| M4.4 | Goals A & B CRUD + derived fields | backend + frontend | M1.1 | 3h | add goal → pct/months_left computed correctly |

### M5 — Dashboards (1 day)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M5.1 | `charts.yaml` loader + generic aggregator | backend-specialist | M1.1 | 2h | YAML entry → `/api/charts/:id` JSON → snapshot test |
| M5.2 | Chart.js renderer page consuming registry | frontend-specialist | M5.1 | 2h | visit /charts → 4 default charts render |
| M5.3 | Add 4 default charts (pie, stack, donut, goal-bar) | frontend-specialist | M5.2 | 1h | YAML entries → charts appear without code change |

### M6 — Google Sheets Backup (½ day)
| # | Task | Agent | Deps | Effort | INPUT → OUTPUT → VERIFY |
|---|------|-------|------|--------|-------------------------|
| M6.1 | Service-account auth + `gspread` wrapper | backend-specialist | Q9 | 2h | creds → can read/write test tab |
| M6.2 | Async sync worker + retry queue + pending WAL | backend-specialist | M6.1, M1.1 | 3h | unplug wifi, add expense, reconnect → row appears in Sheet |
| M6.3 | Startup reconciler ("pull unknown rows" prompt) | backend-specialist | M6.2 | 1h | add row in Sheet manually → start app → prompted to import |

### Phase X — Verification (½ day)
- Lint: `ruff check .`, `mypy .`
- `python .../vulnerability-scanner/scripts/security_scan.py .`
- `python .../frontend-design/scripts/ux_audit.py .`
- `python .../frontend-design/scripts/accessibility_checker.py .`
- `python .../performance-profiling/scripts/lighthouse_audit.py http://localhost:8000`
- Manual runbook (see §13).

---

## 12. Dependency Graph (ASCII DAG)

```
                           ┌────────┐
                           │ M0.1   │ (scaffold)
                           └───┬────┘
                ┌──────────────┼──────────────┐
                ▼              ▼              ▼
             ┌────┐         ┌────┐         ┌────┐
             │M0.2│         │M0.3│         │ Q1 │
             └─┬──┘         └─┬──┘         └─┬──┘
               │              │              │
               ▼              ▼              │
             ┌────┐         ┌────┐           │
             │M1.1│ ◄───────┤M2.1│           │
             └─┬──┘         └─┬──┘           │
     ┌─────────┼────────┐     │              │
     ▼         ▼        ▼     ▼              ▼
  ┌────┐   ┌────┐   ┌────┐  ┌────┐         ┌────┐
  │M1.2│   │M1.4│   │M1.5│  │M2.2│ ◄───────┤ Q1 │
  └─┬──┘   └────┘   └────┘  └─┬──┘         └────┘
    ▼                          ▼
  ┌────┐                     ┌────┐
  │M1.3│                     │M2.3│
  └────┘                     └─┬──┘
                               ▼
                             ┌────┐
                             │M2.4│
                             └─┬──┘
                    ┌──────────┼──────────┐
                    ▼          ▼          ▼
                 ┌────┐     ┌────┐     ┌────┐
                 │M2.5│     │M2.6│     │M3.1│
                 └────┘     └────┘     └─┬──┘
                                         ▼
                                      ┌────┐
                                      │M3.2│
                                      └────┘
            ┌── M3.3 ◄── M1.1
            ▼
         ┌────┐
         │M4.1│ ◄── M1.1
         └─┬──┘
    ┌──────┼──────┐
    ▼      ▼      ▼
 ┌────┐ ┌────┐ ┌────┐
 │M4.2│ │M4.3│ │M4.4│
 └────┘ └────┘ └────┘

 M5.1 ◄── M1.1
  │
  ▼
 M5.2 ──► M5.3

 M6.1 ◄── Q9
  │
  ▼
 M6.2 ◄── M1.1
  │
  ▼
 M6.3

 Phase X ◄── (all of M1..M6)
```

---

## 13. Verification Checklist (per milestone)

### M0
- [ ] `uvicorn` starts, `/health` returns 200
- [ ] All 8 CSV files exist with correct headers
- [ ] 10 dashboard routes render without error

### M1
- [ ] Add expense via grid → appears in `expenses.csv`
- [ ] Kill app mid-write → on restart, WAL replayed, no data loss
- [ ] `/api/reports/totals?date=today` returns correct sum
- [ ] Seed balances page updates `balances.csv`

### M2
- [ ] Voice button captures speech, shows transcript
- [ ] Spoken expense → row in CSV within 3s
- [ ] "Withdrew 2000 from ATM" → balances update, no expense row
- [ ] Malformed Groq output → retried once, then 422 with transcript preserved
- [ ] Teach a new vendor → next similar utterance classified correctly

### M3
- [ ] Investments monthly grid loads
- [ ] Add column "Crypto" → appears in grid and CSV header
- [ ] Wishlist CRUD works; AI-source flag visible

### M4
- [x] Overflow engine: ₹3000/₹2500 scenario produces carry=₹500
- [x] Carry-cap reached → overflow → Medical
- [x] Medical cap reached → overflow → Emergency monthly
- [x] Property tests (hypothesis) pass 1000 runs
- [x] Goals A & B show correct pct_complete and months_left

### M5
- [x] 4 default charts render on `/charts` (+ 2 bonus: daily line, top vendors)
- [x] Adding a new YAML entry → chart appears on reload without code change
- [x] Filter sanitiser rejects code-execution payloads (AST walk)
- [x] Empty data produces `meta.empty=true` payload, not a 500

### M6
- [ ] Local write succeeds even with Sheets offline
- [ ] Reconnect → pending rows synced
- [ ] Startup reconciler prompts for unknown Sheet rows

### Phase X
- [ ] `ruff check .` clean
- [ ] `mypy .` clean
- [ ] `security_scan.py` no criticals
- [ ] `ux_audit.py` no blockers
- [ ] `accessibility_checker.py` no criticals
- [ ] `lighthouse_audit.py` score ≥ 85 perf / ≥ 90 a11y
- [ ] Manual runbook (seed → speak 5 expenses → check totals → verify Sheet mirror) passes

---

## 14. Risks & Mitigations

| # | Risk | Likelihood | Impact | Mitigation |
|---|------|-----------|--------|------------|
| R1 | Groq JSON mode inconsistencies | Med | High | pydantic strict + one retry + fallback to manual edit UI |
| R2 | Web Speech API browser coverage | Med | Med | Server-side Whisper fallback behind same `/api/voice/transcribe` |
| R3 | CSV corruption on crash | Low | High | WAL + atomic rename, unit-tested recovery |
| R4 | Sheets rate limits / outage | Med | Low | Async queue + pending WAL; local remains source of truth |
| R5 | Uniques JSON grows unbounded | Low | Med | 90-day LRU compression before prompt injection |
| R6 | Overflow engine ambiguity when caps hit zero | Med | Med | Explicit `warning: overflow_lost` logged; unit tests for edge cases |
| R7 | User adds investment column retroactively | Med | Low | Forward-only policy documented; Q12 flagged |
| R8 | Timezone bugs around midnight | Med | Med | All dates `Asia/Kolkata` via `zoneinfo`; tests at 23:59/00:01 |
| R9 | Voice privacy (always-on mic) | Low | High | Push-to-talk only; no audio persisted |
| R10 | LLM cost overrun | Low | Low | Log token usage per call; alert on daily budget |

---

## 15a. Portfolio / LinkedIn Polish (v1 must-haves)

This project is being built as a public portfolio piece. The following are **shippable-quality requirements**, not nice-to-haves:

- **README.md** — hero screenshot, one-line pitch, feature bullets with GIFs, architecture diagram, setup-in-5-minutes, tech-stack badges, demo link, "Why I built this" paragraph.
- **Design language** — owned by `hub:ui-ux-pro-max`: consistent palette (dark + light), typography scale, spacing tokens, chip/button/input components, chart color family, motion guidelines (subtle, respect `prefers-reduced-motion`).
- **Screenshots & GIF** — captured during Phase X and placed in `docs/media/`.
- **Public demo** — dockerized so reviewer can `docker run -p 8000:8000 fineye` with a seeded fixture.
- **LICENSE** — MIT (default).
- **CONTRIBUTING.md + `.github/ISSUE_TEMPLATE/`** — minimal, signals open-source intent.
- **Accessibility** — Lighthouse a11y ≥ 95; all chips keyboard-operable; `aria-live` on voice transcript.
- **Responsive** — dashboard usable on ≥ 360 px wide (phone) without horizontal scroll on core pages.
- **Empty states** — every grid/page has a friendly "no data yet — try the voice button / import an Excel" state.
- **Sample data** — `fixtures/seed.py` loads ~60 days of realistic expenses so cold-start screenshots look good.
- **Privacy note** — README explains: localhost only, no audio persisted, API key in `.env`.

---

## 16. Out-of-Scope Reminders (Revisit Post-v1)

- Bi-directional Sheets editing as source of truth
- Bank/UPI API ingestion
- Mobile PWA offline-first
- Multi-currency
- Multi-user with auth
- Receipt OCR
- Forecasting/ML on spending trends
- Encrypted at-rest CSV
