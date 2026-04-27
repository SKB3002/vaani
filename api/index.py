"""Vercel serverless entry point."""
import traceback

_startup_error = None

try:
    from app.main import app
except Exception:
    _startup_error = traceback.format_exc()
    app = None  # defined below

if app is None:
    _err = _startup_error or "unknown startup error"

    async def app(scope, receive, send):
        if scope["type"] != "http":
            return
        body = f"Startup error:\n{_err}".encode()
        await send({
            "type": "http.response.start",
            "status": 500,
            "headers": [
                [b"content-type", b"text/plain; charset=utf-8"],
                [b"content-length", str(len(body)).encode()],
            ],
        })
        await send({"type": "http.response.body", "body": body})
