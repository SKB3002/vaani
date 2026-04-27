"""Vercel serverless entry point."""
import traceback


def _load_app():
    try:
        from app.main import app as _app
        return _app, None
    except Exception:
        return None, traceback.format_exc()


_real_app, _startup_error = _load_app()


async def app(scope, receive, send):
    if _real_app is not None:
        await _real_app(scope, receive, send)
        return
    if scope["type"] != "http":
        return
    body = f"Startup error:\n{_startup_error}".encode()
    await send({
        "type": "http.response.start",
        "status": 500,
        "headers": [
            [b"content-type", b"text/plain; charset=utf-8"],
            [b"content-length", str(len(body)).encode()],
        ],
    })
    await send({"type": "http.response.body", "body": body})
