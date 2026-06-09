"""Helpers for reading/writing data/uniques.json — vendors, aliases, people, tags.

Centralised so routers/services don't reach into voice.py internals. The on-disk
file shape is `{vendors: {}, aliases: {}, people: [], tags: []}`.
"""
from __future__ import annotations

import errno
import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.config import get_settings

logger = logging.getLogger(__name__)

_DEFAULT: dict[str, Any] = {
    "vendors": {},
    "aliases": {},
    "people": [],
    "tags": [],
    # tag -> "Need" | "Want" | "Investment". Lets the grouped Table C view roll a
    # custom tag's spend into its parent type, and gives the LLM a hint about what
    # the tag means. Built-in type_categories don't need an entry here (their type
    # is the prefix before the comma).
    "tag_types": {},
}

VALID_TAG_TYPES = ("Need", "Want", "Investment")

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
    """Persist uniques.json atomically.

    On a read-only filesystem (Vercel/EROFS in supabase mode) the write is
    skipped instead of crashing the request — uniques is a local-mode store, and
    in supabase mode tags are sourced from budget_rules instead (see budgets
    router). Any other OSError still propagates.
    """
    p = _path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        if getattr(e, "errno", None) == errno.EROFS:
            logger.warning("uniques.json not persisted (read-only filesystem): %s", p)
            return
        raise


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
        # Keep tag_types in lockstep so a removed tag leaves no orphan type entry.
        data["tag_types"] = {
            k: v for k, v in (data.get("tag_types") or {}).items() if str(k).lower() != norm
        }
        save(data)
        return tags


def list_tags() -> list[str]:
    raw = load().get("tags") or []
    return [str(t) for t in raw]


def set_tag_type(tag: str, type_: str) -> None:
    """Record (or update) the Need/Want/Investment type for a tag.

    Stored under `tag_types` so the grouped Table C view and the LLM both know
    what a custom tag rolls up into. No-op for blank input.
    """
    norm = tag.strip()
    if not norm:
        return
    if type_ not in VALID_TAG_TYPES:
        raise ValueError(f"invalid tag type: {type_!r}; expected one of {VALID_TAG_TYPES}")
    with _LOCK:
        data = load()
        types: dict[str, str] = dict(data.get("tag_types") or {})
        # Reconcile casing: drop any prior entry that differs only by case.
        for existing in [k for k in types if k.lower() == norm.lower() and k != norm]:
            del types[existing]
        types[norm] = type_
        data["tag_types"] = types
        save(data)


def get_tag_type(tag: str) -> str | None:
    """Return the recorded type for a tag (case-insensitive), or None."""
    norm = tag.strip().lower()
    if not norm:
        return None
    types: dict[str, Any] = load().get("tag_types") or {}
    for k, v in types.items():
        if str(k).lower() == norm:
            return str(v)
    return None


def list_tags_with_types() -> list[dict[str, str | None]]:
    """Return tags as `[{name, type}]`, type = recorded Need/Want/Investment or None."""
    types: dict[str, Any] = load().get("tag_types") or {}
    lower = {str(k).lower(): str(v) for k, v in types.items()}
    return [{"name": t, "type": lower.get(t.lower())} for t in list_tags()]


def remove_tag_type(tag: str) -> None:
    """Drop a tag's recorded type (case-insensitive). Paired with remove_tag."""
    norm = tag.strip().lower()
    if not norm:
        return
    with _LOCK:
        data = load()
        types: dict[str, str] = {
            k: v for k, v in (data.get("tag_types") or {}).items() if str(k).lower() != norm
        }
        data["tag_types"] = types
        save(data)
