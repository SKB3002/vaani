# Contributing to Vaani

Thanks for your interest in contributing! Vaani is an open-source personal finance tracker built for people who want full control over their data. Every contribution — bug reports, features, docs, tests — makes the project better for everyone.

---

## 🚀 Quick Start for Contributors

### 1. Fork & clone

```bash
git clone https://github.com/YOUR_USERNAME/vaani.git
cd vaani
git remote add upstream https://github.com/SKB3002/vaani.git
```

### 2. Set up dev environment

```bash
pip install -e ".[dev]"
cp .env.example .env   # local CSV mode works without any keys
python -m scripts.bootstrap_cli
python -m uvicorn app.main:app --reload
```

### 3. Run the test suite

```bash
python -m pytest -x          # tests
python -m ruff check .       # lint
python -m mypy app/          # type-check
```

All three must pass before opening a PR.

---

## 🐛 Reporting Bugs

Open an [issue](https://github.com/SKB3002/vaani/issues) with:

- **What happened** — actual behavior
- **What you expected** — intended behavior
- **Steps to reproduce** — minimal example
- **Environment** — OS, Python version, deployment (local / Vercel / Docker)
- **Logs** — relevant tracebacks or browser console errors

For security issues, please email instead of opening a public issue.

---

## ✨ Suggesting Features

Open an issue with the `enhancement` label first — let's discuss the design before you write code. This avoids wasted effort on things that don't fit the project's direction.

Good feature proposals include:
- The problem you're trying to solve
- Why existing features don't cover it
- A rough sketch of the API/UX

---

## 🛠️ Areas We Need Help With

| Area | Difficulty | Notes |
|------|-----------|-------|
| 📱 Mobile-friendly UI | Medium | Responsive Handsontable, touch-friendly forms |
| 🌍 i18n | Medium | Currently English/Hindi-friendly, INR-first — needs proper i18n framework |
| 🧪 Test coverage on budget overflow | Easy-Medium | `app/services/budget/overflow.py` — edge cases around carry caps |
| 🔌 Bank statement parsers | Hard | HDFC / SBI / ICICI CSV/PDF imports |
| 🔌 Splitwise integration | Medium | Sync "paid for someone" entries |
| 📊 New chart types | Easy | Sankey diagrams, treemaps for spending breakdown |
| 📥 Recurring expense detection | Hard | Auto-detect patterns from past data |
| 🔐 Multi-user with Supabase Auth + RLS | Hard | Major architectural change |
| 📚 Tutorial videos / blog posts | Easy | Help others self-host |

Tag an issue with `good-first-issue` if you're starting out.

---

## 📐 Code Standards

### Python style

- **Formatter:** ruff (config in `pyproject.toml`)
- **Type hints:** required on all new functions and methods
- **Docstrings:** required on public functions/classes — keep them concise
- **Imports:** stdlib → third-party → local, separated by blank lines
- **Naming:** `snake_case` for functions/variables, `PascalCase` for classes

### Frontend (JS)

- Vanilla JS — no frameworks (yet)
- Use `const` / `let`, never `var`
- Keep modules in `static/js/` focused on one domain (`expenses.js`, `budgets.js`, etc.)

### Database / Storage

- **Never break the WAL contract.** All writes must go through `LedgerWriter` — no direct CSV / Supabase writes from routers.
- New schemas go in `app/storage/schemas.py` with both CSV columns and Postgres equivalents.
- If you add a new table, update the Supabase migration in `scripts/supabase_schema.sql`.

### Tests

- Unit tests for services in `tests/unit/`
- Integration tests for routers in `tests/integration/`
- Use `pytest` fixtures from `conftest.py` — don't rebuild app instances per test
- Aim for ≥80% coverage on new code

---

## 🔄 Pull Request Process

1. **Branch off `main`:** `git checkout -b feat/your-feature` or `fix/your-bug`
2. **Make focused commits:** one logical change per commit
3. **Write a clear commit message:**
   ```
   feat(budgets): add quarterly carry-forward option

   Allows users to carry budget overflow across 3-month windows
   instead of just monthly. Closes #42.
   ```
4. **Run the full test suite:** `pytest -x && ruff check . && mypy app/`
5. **Update docs:** README, CHANGELOG (if it exists), inline comments
6. **Push & open a PR** against `main`
7. **Fill out the PR template** — what changed, why, how to test
8. **Respond to review comments** — discussion is welcome

PRs should be **small and focused**. Big refactors get rejected. If you want to refactor, open an issue first.

---

## 🧭 Architecture Principles

When writing code, keep these in mind:

1. **Data ownership is sacred.** Users bring their own keys. Never add a path that sends data to *our* servers.
2. **Zero data loss.** Every write goes through WAL + atomic CSV (local) or upsert (Supabase). No exceptions.
3. **Offline-first.** The app must work without internet. Cloud sync is a mirror, not a dependency.
4. **INR-first.** Default to ₹, IST, Indian payment methods (UPI, cash, online). i18n is welcome but don't break the defaults.
5. **No subscriptions.** Vaani will always be free, open-source, and self-hostable.

---

## 📜 License

By contributing, you agree that your contributions will be licensed under the [MIT License](LICENSE).

---

## 💬 Questions?

- Open a [discussion](https://github.com/SKB3002/vaani/discussions) for general questions
- Tag [@SKB3002](https://github.com/SKB3002) on issues for direct attention
- Drop a ⭐ on the repo if you find Vaani useful — it really helps!

Happy hacking! 🚀
