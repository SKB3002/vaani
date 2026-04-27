"""Vercel serverless entry point."""
import traceback

try:
    from app.main import app
except Exception:
    _tb = traceback.format_exc()
    from fastapi import FastAPI
    app = FastAPI()

    @app.get("/{path:path}")
    async def _startup_error(path: str = ""):
        from fastapi.responses import PlainTextResponse
        return PlainTextResponse(f"Startup error:\n{_tb}", status_code=500)
