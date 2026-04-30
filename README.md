# 🎙️ Vaani — Voice-Driven Personal Finance Tracker

> Your money. Your data. Your rules.

Vaani is an open-source, self-hostable personal finance tracker with **voice-driven expense capture**, **AI categorization**, and a **bring-your-own-keys** architecture. Talk to it like a friend — *"spent 250 on chai with Rohan, he'll pay me back"* — and watch it transcribe, categorize, split, and sync everything to your own cloud.

🌟 **Star this repo** if you believe personal financial data shouldn't live on someone else's servers.

---

## ✨ Why Vaani?

Every finance app I tried had the same problems:

- ❌ My financial data lived on *their* servers
- ❌ Categories designed for the US, not India (no UPI splits, no "paid for friend" tracking)
- ❌ No voice input — manually typing every expense kills consistency
- ❌ Subscription paywalls for basic features
- ❌ No clean way to own or export data

So I built one that respects your data ownership and actually understands how you spend.

---

## 🚀 Features

### 🎙️ Voice-First Capture
Just speak naturally. Groq's LPU transcribes (Whisper) and LLaMA 3.3 categorizes — sub-second response.
> *"Spent 450 on Zomato dinner with Anjali"* → Food · ₹450 · Paid for: Anjali · Split: 50/50

### 🇮🇳 INR-First, India-Aware
- UPI splits, cash + online tracking
- "Paid for someone" / "paid by someone" workflows
- Custom categories that match how Indians actually spend

### 📊 Real Budget Engine (not just "you spent X")
- Carry-forward rules with priority overflow
- Monthly caps (Medical, Emergency)
- Automatic Table C recomputation on every change
- Visual dashboards (Chart.js)

### 📈 Investment + Goals + Wishlist Tracking
- Month-over-month investment growth
- Savings goals with progress charts
- Prioritized wishlist with "can I afford it?" indicators

### 🔐 BYOK (Bring Your Own Keys) Architecture
- **Your own Supabase** — your data lives in your Postgres, not mine
- **Your own Google Sheet** — automatic backup mirror
- **Your own Groq API key** — voice AI you control
- No vendor lock-in. No SaaS. No surveillance.

### ⚡ Zero Data Loss
- Write-Ahead Log (WAL) + atomic CSV writes locally
- Dual-write to Supabase
- Works **offline** (CSV fallback), syncs when online
- Same patterns Postgres uses, applied at the app layer

### 📥 Import & Export
- CSV/Excel import with smart column mapping
- Full export to CSV
- Google Sheets bidirectional backup

---

## 🛠️ Tech Stack

| Layer | Tech |
|-------|------|
| Backend | FastAPI, Pydantic, pandas |
| Database | Supabase (Postgres) — BYOK |
| AI | Groq (Whisper + LLaMA 3.3 70B) |
| Frontend | Handsontable, Chart.js, vanilla JS |
| Storage | CSV ledger + WAL (local) / Supabase (cloud) |
| Hosting | Vercel serverless / Self-hosted Docker |
| Backup | Google Sheets API |

> ⚠️ **Handsontable license note:** the grid uses [Handsontable](https://handsontable.com)
> under its non-commercial / evaluation license. Vaani's own code is MIT, but commercial
> forks will need a Handsontable commercial license. A freemium tier for projects like
> this is reportedly on the way — until then, keep deployments personal / non-commercial.

---

## 🏃 Quick Start (Local)

### 1. Clone & install

```bash
git clone https://github.com/SKB3002/vaani.git
cd vaani
pip install -e ".[dev]"
```

### 2. Configure

Copy `.env.example` → `.env` and fill in:

```bash
# Local CSV mode works without any keys
FINEYE_DATA_DIR=data
FINEYE_LOG_LEVEL=INFO

# Optional: Voice + AI categorization
GROQ_API_KEY=gsk_...   # Get free key at console.groq.com

# Optional: Cloud sync (BYOK Supabase)
DB_HOST=db.YOUR_PROJECT.supabase.co
DB_PASSWORD=your-supabase-db-password
FINEYE_OWNER_ID=your-uuid-here   # any UUID, identifies your data

# Optional: Google Sheets backup
GOOGLE_SHEETS_ENABLED=false
GOOGLE_SHEETS_CREDENTIALS_PATH=path/to/service-account.json
GOOGLE_SHEETS_SPREADSHEET_ID=your-sheet-id
```

### 3. Bootstrap & run

```bash
python -m scripts.bootstrap_cli
python -m uvicorn app.main:app --reload
```

Open [http://localhost:8000](http://localhost:8000) → start logging expenses.

API docs: [http://localhost:8000/docs](http://localhost:8000/docs)

### 4. (Optional) Seed demo data

```bash
python -m scripts.seed
```

---

## ☁️ Deploy Your Own (Vercel + Supabase)

Want your own hosted Vaani? Takes ~10 minutes.

### Step 1: Set up Supabase
1. Create a free project at [supabase.com](https://supabase.com)
2. Run `scripts/supabase_schema.sql` in the SQL Editor
3. Note your DB host, password, and project ref

### Step 2: Fork & deploy to Vercel
1. Fork this repo
2. Import to [Vercel](https://vercel.com) (one click)
3. Set environment variables:

| Variable | Value |
|----------|-------|
| `FINEYE_STORAGE_BACKEND` | `supabase` |
| `DB_HOST` | `aws-1-<region>.pooler.supabase.com` (use Transaction Pooler) |
| `DB_PORT` | `6543` |
| `DB_USER` | `postgres.YOUR_PROJECT_REF` |
| `DB_PASSWORD` | (your Supabase DB password) |
| `DB_NAME` | `postgres` |
| `FINEYE_OWNER_ID` | any UUID (your "user id") |
| `FINEYE_APP_PASSWORD` | (set a password to protect your app) |
| `GROQ_API_KEY` | (optional, for voice) |

4. Deploy. That's it.

> **⚠️ Pooler region:** Use `aws-1-<region>` (not `aws-0-`). Find yours in Supabase → Settings → Database → Transaction pooler.

### Step 3: Migrate existing data (optional)

If you already have local CSV data:

```bash
python -m scripts.migrate_to_supabase
```

---

## 📁 Project Structure

```
fineeye/
├── app/
│   ├── main.py              # FastAPI app + lifespan
│   ├── routers/             # API endpoints (expenses, budgets, goals, ...)
│   ├── services/            # Business logic (ledger, budget runner, voice)
│   ├── storage/             # CSV + Supabase + WAL
│   ├── models/              # Pydantic models
│   └── middleware/          # Password gate
├── api/
│   └── index.py             # Vercel serverless entry
├── static/                  # Frontend (JS, CSS)
├── templates/               # Jinja2 HTML
├── scripts/                 # Bootstrap, seed, migrations
├── tests/                   # pytest suite
└── data/                    # Local CSV ledger (gitignored)
```

---

## 🧪 Development

```bash
# Run tests
python -m pytest -x

# Lint & type-check
python -m ruff check .
python -m mypy app/

# Run with auto-reload
python -m uvicorn app.main:app --reload
```

> If your Python `Scripts/` dir is on `PATH`, drop the `python -m` prefix.

---

## 🤝 Contributing

Pull requests welcome! Areas where I'd love help:

- 📱 Mobile-friendly UI improvements
- 🌍 i18n (currently English/Hindi-friendly, INR-first)
- 🧪 More test coverage on the budget overflow engine
- 🔌 Integrations (Splitwise, bank statement parsers, ...)
- 📊 New chart types

Please open an issue first for big features.

---

## 🗺️ Roadmap

- [x] Voice-driven expense capture
- [x] AI categorization (Groq)
- [x] Budget engine with carry-forward + caps
- [x] Investment + goals + wishlist
- [x] Supabase BYOK sync
- [x] Google Sheets backup
- [x] Vercel deployment
- [ ] Multi-user with Supabase Auth + RLS
- [ ] Mobile app (React Native)
- [ ] Bank statement auto-import
- [ ] Recurring expense detection
- [ ] Tax-ready reports

---

## 📜 License

Vaani's own code is MIT — fork it, ship it, sell it. Just don't pretend you wrote it.

Bundled dependencies keep their own licenses; notably **Handsontable is not MIT** (see the note under Tech Stack).

---

## 🙏 Built With

Big thanks to the teams behind [FastAPI](https://fastapi.tiangolo.com), [Supabase](https://supabase.com), [Groq](https://groq.com), [Handsontable](https://handsontable.com), and [Chart.js](https://chartjs.org).

---

## 📬 Contact / Showcase

Built by [@SKB3002](https://github.com/SKB3002).

Using Vaani? **Drop a ⭐ on the repo** and tag me — I'd love to see how you've customized it.

Found a bug? [Open an issue](https://github.com/SKB3002/vaani/issues).

---

> *"The best personal finance tool is the one that respects your data."*
