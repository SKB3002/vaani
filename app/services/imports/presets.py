"""Import presets — built-in column mappings for known spreadsheet layouts.

Presets are loaded from ``data/meta/import_presets.json``. The first preset the
app ships with is ``personal_ledger_v1`` — matches the user's own Excel layout
(DD/MM/YYYY dates, combined "Tags" column, daily "Total" rows, balance-adjust
rows with zero amount).
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, TypedDict


class Preset(TypedDict, total=False):
    id: str
    label: str
    target_table: str
    date_format: str
    column_mapping: dict[str, str]
    row_filters: dict[str, Any]


def _presets_path(data_dir: Path) -> Path:
    return data_dir / "meta" / "import_presets.json"


def load_presets(data_dir: Path) -> list[Preset]:
    path = _presets_path(data_dir)
    if not path.exists():
        return []
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    presets = raw.get("presets", [])
    return list(presets)


def get_preset(data_dir: Path, preset_id: str) -> Preset | None:
    for p in load_presets(data_dir):
        if p.get("id") == preset_id:
            return p
    return None
