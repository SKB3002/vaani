"""Health + liveness probe."""
from __future__ import annotations

from fastapi import APIRouter

from app import __version__
from app.config import get_settings
from app.services.tz import user_tz_name

router = APIRouter()


@router.get("/health")
def health() -> dict[str, str]:
    return {"ok": "true", "status": "ok", "version": __version__, "tz": user_tz_name()}


@router.get("/api/debug/config")
def debug_config() -> dict[str, object]:
    """Diagnostic: shows what config Vercel is using + what Supabase returns."""
    cfg = get_settings()
    out: dict[str, object] = {
        "storage_backend": cfg.STORAGE_BACKEND,
        "owner_id": cfg.OWNER_ID or "(empty)",
        "supabase_configured": cfg.supabase_configured,
        "db_host": cfg.DB_HOST or "(empty)",
        "db_user": cfg.DB_USER,
        "db_name": cfg.DB_NAME,
    }
    if cfg.supabase_configured:
        try:
            import psycopg2
            conn = psycopg2.connect(cfg.supabase_dsn)
            try:
                with conn.cursor() as cur:
                    cur.execute("SELECT DISTINCT user_id FROM expenses LIMIT 5")
                    distinct_users = [str(r[0]) for r in cur.fetchall()]
                    cur.execute(
                        "SELECT COUNT(*) FROM expenses WHERE user_id = %s",
                        [cfg.OWNER_ID],
                    )
                    matching_rows = cur.fetchone()[0]
                    cur.execute("SELECT COUNT(*) FROM expenses")
                    total_rows = cur.fetchone()[0]
            finally:
                conn.close()
            out["expenses_total_rows"] = total_rows
            out["expenses_rows_for_owner"] = matching_rows
            out["distinct_user_ids_in_expenses"] = distinct_users
        except Exception as e:
            out["db_error"] = f"{type(e).__name__}: {e}"
    return out
