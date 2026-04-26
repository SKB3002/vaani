# Google Sheets Backup — Setup

FinEye mirrors every local CSV write to a Google Sheets spreadsheet you own.
Local CSVs stay the source of truth; Sheets is a mirror. A Sheets outage
never blocks writes — jobs queue and drain automatically on reconnect.

## 1. Create a Google Cloud project

1. Open https://console.cloud.google.com/
2. Create a new project (any name — e.g. `fineye-backup`).

## 2. Enable the Sheets API

1. In the Cloud Console, go to **APIs & Services -> Library**.
2. Search **Google Sheets API** and click **Enable**.

## 3. Create a service account + key

1. **APIs & Services -> Credentials -> Create credentials -> Service account**.
2. Name it (e.g. `fineye-sync`). No roles needed.
3. Open the created service account, **Keys -> Add key -> Create new key -> JSON**.
4. A JSON file downloads. Keep it secret.

## 4. Share your spreadsheet with the service account

1. Create a blank Google Sheet (any name — FinEye will create tabs on demand).
2. Copy the spreadsheet ID from the URL:
   `https://docs.google.com/spreadsheets/d/<SPREADSHEET_ID>/edit`
3. Click **Share** and paste the service account's email
   (looks like `fineye-sync@<project>.iam.gserviceaccount.com`).
4. Give it **Editor** access.

## 5. Drop the key file locally

```
c:\Suyash_Projects\fineeye\data\.secrets\service_account.json
```

Create `data\.secrets\` if missing. The `.gitignore` already excludes `data/**`
so the key will not be committed. Do not commit it regardless.

## 6. Configure `.env`

Add to your `.env` (at project root):

```
GOOGLE_SHEETS_ENABLED=true
GOOGLE_SHEETS_CREDENTIALS_PATH=data/.secrets/service_account.json
GOOGLE_SHEETS_SPREADSHEET_ID=<paste SPREADSHEET_ID from step 4>

# optional tuning
GOOGLE_SHEETS_MAX_RETRIES=6
GOOGLE_SHEETS_BACKOFF_BASE=1.0
```

## 7. Restart and test

```bash
uvicorn app.main:app --reload
```

Open `http://localhost:8000/settings`. In the Integrations card:

- **Test Connection** -> should show the spreadsheet title and tab count.
- **Full sync now** -> uploads every existing CSV row to the corresponding tab.
- **Reconcile** -> reports rows that exist in the Sheet but not locally (v1
  does not auto-pull; explicit opt-in required).
- The status chip shows `Connected`, queue depth, and the last sync time.

## OAuth user flow (alternative)

If you do not want to share the Sheet with a service account, use an OAuth
user flow instead:

1. In **APIs & Services -> Credentials** create an **OAuth client ID** of type
   **Desktop app**.
2. Use `google-auth-oauthlib` (already declared in `pyproject.toml`) to
   complete the `InstalledAppFlow` once and cache the refresh token.
3. Swap `SheetsClient._ensure_opened` to load the `AuthorizedUser`
   credentials instead of `ServiceAccountCredentials`.

The service-account path is the default because it is simpler for single-user
local deployments (the intended FinEye topology per `PLAN-fineye-finance-ai.md` Q9).

## Troubleshooting

| Symptom | Fix |
|---------|-----|
| `SheetsClientError: service account credentials not found` | Check `GOOGLE_SHEETS_CREDENTIALS_PATH`. Path is resolved relative to the working directory. |
| `403 The caller does not have permission` | The service account's email is not shared on the Sheet as Editor. |
| `queue_depth` keeps rising | Your creds / network / quota is broken. Inspect `/api/sheets/status.last_error`. Fix, then `POST /api/sheets/drain-queue`. |
| Unknown rows appear in `/api/sheets/reconcile` | Someone edited the Sheet directly. Local wins by default; explicitly import per tab with `POST /api/sheets/reconcile/import?tab=X`. |
