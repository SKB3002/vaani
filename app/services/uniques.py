"""Helpers for reading/writing data/uniques.json — vendors, aliases, people, tags.

Centralised so routers/services don't reach into voice.py internals. The on-disk
file shape is `{vendors: {}, aliases: {}, people: [], tags: []}`.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings

_DEFAULT: dict[str, Any] = {
    "vendors": {},
    "aliases": {},
    "people": [],
    "tags": [],
}

_LOCK = threading.Lock()


def _path() -> Path:
    return get_settings().resolved_data_dir() / "uniques.json"


def load() -> dict[str, Any]:
    p = _path()
    if not p.exists():
        return {**_DEFAULT}
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {**_DEFAULT}
    if not isinstance(data, dict):
        return {**_DEFAULT}
    for k, v in _DEFAULT.items():
        data.setdefault(k, v if not isinstance(v, (dict, list)) else type(v)())
    return data


def save(data: dict[str, Any]) -> None:
    p = _path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(p)


def add_tag(tag: str) -> list[str]:
    """Append a tag (deduped, case-insensitive). Returns the new tags list."""
    norm = tag.strip()
    if not norm:
        return list_tags()
    with _LOCK:
        data = load()
        tags: list[str] = list(data.get("tags") or [])
        if not any(t.lower() == norm.lower() for t in tags):
            tags.append(norm)
            data["tags"] = tags
            save(data)
        return tags


def remove_tag(tag: str) -> list[str]:
    """Remove a tag (case-insensitive). Returns the new tags list."""
    norm = tag.strip().lower()
    if not norm:
        return list_tags()
    with _LOCK:
        data = load()
        tags: list[str] = [t for t in (data.get("tags") or []) if t.lower() != norm]
        data["tags"] = tags
        save(data)
        return tags


def list_tags() -> list[str]:
    raw = load().get("tags") or []
    return [str(t) for t in raw]
