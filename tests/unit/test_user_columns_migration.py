"""Legacy investment_columns.json one-shot migration."""
from __future__ import annotations

import json
from pathlib import Path

from app.storage import user_columns


def test_legacy_investment_columns_migrates_on_first_read(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    legacy = data / "meta" / "investment_columns.json"
    # overwrite bootstrap defaults with a mix of builtin + custom entries
    legacy.write_text(
        json.dumps(
            {
                "columns": [
                    {"key": "long_term", "label": "Long Term", "builtin": True},
                    {"key": "crypto", "label": "Crypto", "builtin": False, "added_at": "2026-05-01"},
                    {"key": "nps", "label": "NPS", "builtin": False},
                ]
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    cols = user_columns.list_user_columns(data, "investments")
    keys = [c["key"] for c in cols]
    assert "crypto" in keys
    assert "nps" in keys
    assert "long_term" not in keys  # builtin — not migrated into user registry

    # Legacy file must be PRESERVED (never delete audit data)
    assert legacy.exists()

    # And the new registry file now exists
    new_path = data / "meta" / "user_columns" / "investments.json"
    assert new_path.exists()

    # Second call is idempotent (no duplicate entries)
    cols2 = user_columns.list_user_columns(data, "investments")
    assert [c["key"] for c in cols2] == keys


def test_no_legacy_file_means_empty_registry(tmp_workspace: Path) -> None:
    data = tmp_workspace / "data"
    (data / "meta" / "investment_columns.json").unlink(missing_ok=True)
    cols = user_columns.list_user_columns(data, "investments")
    assert cols == []
