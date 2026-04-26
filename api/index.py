"""Vercel serverless entry point.

Vercel's Python runtime looks for `app` in api/index.py.
We simply re-export the FastAPI application object.
"""
from app.main import app  # noqa: F401
