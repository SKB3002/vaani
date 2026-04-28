"""Vercel serverless entry point."""
import sys
import os
import traceback


def _load_app():
    try:
        from app.main import app as _app
        return _app, None
    except Exception:
        return None, traceback.format_exc()


_real_app, _startup_error = _load_app()

_diag = (
    f"python={sys.version}\n"
    f"path={sys.path}\n"
    f"env_keys={[k for k in os.environ if 'FINEYE' in k or k in ('DB_HOST','GROQ_API_KEY')]}\n"
    f"storage={os.environ.get('FINEYE_STORAGE_BACKEND','NOT SET')}\n"
    f"startup_error={_startup_error or 'none'}\n"
    f"app_type={type(_real_app).__name__}\n"
)


async def app(scope, receive, send):
    if _real_app is not None and _startup_error is None:
        await _real_app(scope, receive, send)
        return
    if scope["type"] != "http":
        return
    body = _diag.encode()
    await send({
        "type": "http.response.start",
        "status": 500,
        "headers": [
            [b"content-type", b"text/plain; charset=utf-8"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({"type": "http.response.body", "body": body})
