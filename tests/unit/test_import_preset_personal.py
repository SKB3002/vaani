"""Personal-ledger preset — tag parsing, payment-dual, Total-row skip, checksum."""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from app.services.imports import committer, presets
from app.services.imports import normalizer as n


def test_preset_loads(tmp_workspace: Path) -> None:
    data_dir = tmp_workspace / "data"
    items = presets.load_presets(data_dir)
    assert any(p["id"] == "personal_ledger_v1" for p in items)
    preset = presets.get_preset(data_dir, "personal_ledger_v1")
    assert preset is not None
    assert preset["target_table"] == "expenses"
    assert preset["date_format"] == "%d/%m/%Y"
    assert preset["column_mapping"]["Payment"] == "__payment_dual"
    assert preset["row_filters"]["detect_balance_adjust"] is True


def test_tags_wants_miscellaneous_both_orders() -> None:
    tc, warnings = n.parse_combined_tags("Wants, Miscellaneous")
    assert tc == "Want, Miscellaneous"
    tc2, _ = n.parse_combined_tags("Miscellaneous, Wants")
    assert tc2 == "Want, Miscellaneous"


def test_tags_plural_singular_canonicalise() -> None:
    assert n.parse_combined_tags("Travel, Needs")[0] == "Need, Travel"
    assert n.parse_combined_tags("Food & Drinks, Wants")[0] == "Want, Food & Drinks"
    assert n.parse_combined_tags("Investments, Miscellaneous")[0] == "Investment, Miscellaneous"


def test_tags_alias_categories() -> None:
    assert n.parse_combined_tags("Transport, Needs")[0] == "Need, Travel"
    assert n.parse_combined_tags("Fun, Wants")[0] == "Want, Enjoyment"
    assert n.parse_combined_tags("Misc, Wants")[0] == "Want, Miscellaneous"


def test_tags_single_wants_defaults_to_misc_with_warning() -> None:
    tc, warnings = n.parse_combined_tags("Wants")
    assert tc == "Want, Miscellaneous"
    assert any("Miscellaneous" in w for w in warnings)


def test_tags_unknown_returns_none() -> None:
    tc, errs = n.parse_combined_tags("BogusTag")
    assert tc is None
    assert errs


def test_payment_dual_mapping() -> None:
    assert n.parse_payment_dual("Paid Cash") == "paid_cash"
    assert n.parse_payment_dual("paid  cash") == "paid_cash"  # extra whitespace
    assert n.parse_payment_dual("Paid") == "paid"
    assert n.parse_payment_dual("PAID") == "paid"
    assert n.parse_payment_dual("Total") == n.PAYMENT_TOTAL_SENTINEL
    assert n.parse_payment_dual("") is None
    assert n.parse_payment_dual(None) is None


def test_dry_run_with_preset_skips_totals_and_captures_balance_adjust(
    tmp_workspace: Path, personal_ledger_xlsx: Path
) -> None:
    preset = presets.get_preset(tmp_workspace / "data", "personal_ledger_v1")
    assert preset is not None
    df = pd.read_excel(personal_ledger_xlsx)

    outcome = committer.dry_run(
        df=df,
        target_table="expenses",
        mapping=preset["column_mapping"],
        date_format=preset["date_format"],
        data_dir=tmp_workspace / "data",
        row_filters=preset["row_filters"],
    )

    # 4 valid expenses (2 on 14th + 2 on 15th)
    assert len(outcome.rows) == 4
    # 2 Total rows skipped + 1 balance-adjust row skipped = 3
    assert len(outcome.skipped) == 3
    # 1 balance-adjust captured
    assert len(outcome.balance_adjusts) == 1
    adj = outcome.balance_adjusts[0]
    assert adj["reason"] == "manual_adjust"
    # cash snapshot 1498 (last Total had 498; adjust row has 1498 -> delta +1000)
    assert abs(adj["cash_balance"] - 1498.0) < 0.01

    # checksum report per day — should match (computed == declared)
    checksums_by_day = {c.day: c for c in outcome.checksum_report}
    assert "2026-04-14" in checksums_by_day
    assert "2026-04-15" in checksums_by_day
    assert checksums_by_day["2026-04-14"].match is True
    assert checksums_by_day["2026-04-14"].computed_total == 950.0
    assert checksums_by_day["2026-04-14"].declared_total == 950.0
    assert checksums_by_day["2026-04-15"].match is True
    assert checksums_by_day["2026-04-15"].computed_total == 3399.0


def test_dry_run_canonical_type_category_values(
    tmp_workspace: Path, personal_ledger_xlsx: Path
) -> None:
    preset = presets.get_preset(tmp_workspace / "data", "personal_ledger_v1")
    assert preset is not None
    df = pd.read_excel(personal_ledger_xlsx)

    outcome = committer.dry_run(
        df=df,
        target_table="expenses",
        mapping=preset["column_mapping"],
        date_format=preset["date_format"],
        data_dir=tmp_workspace / "data",
        row_filters=preset["row_filters"],
    )
    type_cats = {r["type_category"] for r in outcome.rows}
    assert "Need, Travel" in type_cats
    assert "Want, Food & Drinks" in type_cats
    assert "Want, Miscellaneous" in type_cats  # from the single "Wants" row


def test_dry_run_without_preset_is_unchanged(tmp_workspace: Path) -> None:
    """Sanity check: absence of preset = original behaviour."""
    df = pd.DataFrame(
        {
            "Date": ["2026-04-20"],
            "Description": ["Zomato"],
            "Type": ["Want"],
            "Category": ["Food & Drinks"],
            "Method": ["UPI"],
            "Amt": ["450"],
        }
    )
    outcome = committer.dry_run(
        df=df,
        target_table="expenses",
        mapping={
            "Date": "date",
            "Description": "expense_name",
            "Type": "type",
            "Category": "category",
            "Method": "payment_method",
            "Amt": "amount",
        },
        date_format=None,
        data_dir=tmp_workspace / "data",
    )
    assert len(outcome.rows) == 1
    assert outcome.checksum_report == []
    assert outcome.balance_adjusts == []
