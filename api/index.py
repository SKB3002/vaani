"""Vercel serverless entry point."""
import sys
import traceback

try:
    from app.main import app  # noqa: F401
except Exception:
    # Re-expose a minimal ASGI app that returns the traceback so we can debug.
    _tb = traceback.format_exc()

    async def app(scope, receive, send):  # type: ignore[misc]
        if scope["type"] == "http":
            body = f"Startup error:\n{_tb}".encode()
            await send({
                "type": "http.response.start",
                "status": 500,
                "headers": [(b"content-type", b"text/plain"), (b"content-length", str(len(body)).encode())],
            })
            await send({"type": "http.response.body", "body": body})
