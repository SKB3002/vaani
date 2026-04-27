"""Import committer — dedup via sha1, batch audit, rollback support.

Also hosts the preset pre-processor: when a caller passes ``row_filters`` /
synthetic mapping targets (``__payment_dual``, ``__tags_combined``,
``__cash_snapshot``, ``__online_snapshot``), we:

- skip rows whose payment cell is ``"Total"`` (daily summary rows)
- detect balance-adjust rows (zero amount, empty payment, non-zero cash delta)
  and route them to ``balances.csv`` instead of ``expenses.csv``
- emit a per-day checksum report comparing our computed daily total to the
  "Total" summary row the user typed
"""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

import pandas as pd
import ulid
from pydantic import ValidationError

from app.models.expense import ExpenseIn
from app.services.imports import normalizer as norm
from app.services.ledger import LedgerWriter
from app.services.tz import now_utc

DEDUP_FILE = ".dedup_keys.jsonl"


def _dedup_path(data_dir: Path) -> Path:
    return data_dir / DEDUP_FILE


def load_dedup_keys(data_dir: Path) -> set[str]:
    p = _dedup_path(data_dir)
    if not p.exists():
        return set()
    with open(p, encoding="utf-8") as f:
        return {line.strip() for line in f if line.strip()}


def append_dedup_keys(data_dir: Path, keys: list[str]) -> None:
    if not keys:
        return
    p = _dedup_path(data_dir)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        for key in keys:
            f.write(key + "\n")


def expense_dedup_key(
    row_date: date,
    expense_name: str,
    amount: float,
    payment_method: str,
    person_name: str | None,
) -> str:
    payload = (
        f"{row_date.isoformat()}|"
        f"{expense_name.lower().strip()}|"
        f"{amount:.2f}|"
        f"{payment_method}|"
        f"{person_name or ''}"
    )
    return hashlib.sha1(payload.encode("utf-8")).hexdigest()


def generic_dedup_key(parts: list[str]) -> str:
    return hashlib.sha1("|".join(parts).encode("utf-8")).hexdigest()


# ---------- per-target normalizers ----------


def _normalize_expense_row(raw: dict[str, Any], date_format: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []

    parsed_date = norm.parse_date(raw.get("date"), date_format)
    if parsed_date is None:
        errors.append("invalid or missing date")

    name = norm.clean_string(raw.get("expense_name"))
    if not name:
        name = norm.clean_string(raw.get("vendor"))  # fallback: Location column
    if not name:
        errors.append("missing expense_name")

    amount = norm.clean_amount(raw.get("amount"))
    if amount is None or amount <= 0:
        errors.append("invalid amount")

    pm = norm.normalize_payment_method(raw.get("payment_method"))
    if pm is None:
        errors.append("invalid payment_method")

    tc = norm.combine_type_category(
        raw.get("type_category"), raw.get("type"), raw.get("category")
    )
    if tc is None:
        tc = "Need, Miscellaneous"  # default when category is absent

    paid_for_method = norm.clean_string(raw.get("paid_for_method"))
    adjustment_type = norm.clean_string(raw.get("adjustment_type"))

    if pm == "paid_for" and not paid_for_method:
        paid_for_method = "online"
    if pm == "adjusted" and not adjustment_type:
        adjustment_type = "cash_to_online"  # sensible default for ATM withdrawals

    person = norm.clean_string(raw.get("person_name"))
    paid_for = norm.coerce_bool(raw.get("paid_for_someone"))
    paid_by = norm.coerce_bool(raw.get("paid_by_someone"))

    if errors:
        return None, errors

    # If a preset has a Vendor column, fold it into notes (no schema column for vendor).
    notes = norm.clean_string(raw.get("notes"))
    vendor = norm.clean_string(raw.get("vendor"))
    if vendor and not notes:
        notes = f"vendor: {vendor}"
    elif vendor and notes:
        notes = f"{notes} | vendor: {vendor}"

    candidate: dict[str, Any] = {
        "date": parsed_date,
        "expense_name": name,
        "type_category": tc,
        "payment_method": pm,
        "paid_for_someone": paid_for,
        "paid_by_someone": paid_by,
        "person_name": person,
        "amount": amount,
        "source": "import",
        "notes": notes,
        "paid_for_method": paid_for_method,
        "adjustment_type": adjustment_type,
    }
    try:
        model = ExpenseIn(**candidate)
    except ValidationError as e:
        return None, [str(err.get("msg", "invalid")) for err in e.errors()]
    return model.model_dump(mode="json"), []


def _normalize_investment_row(raw: dict[str, Any], _: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    month = norm.clean_string(raw.get("month"))
    if not month:
        errors.append("missing month")

    row: dict[str, Any] = {"month": month}
    numeric_cols = [
        "long_term",
        "mid_long_term",
        "emergency_fund",
        "bike_savings_wants",
        "misc_spend_save",
        "fixed_deposits",
    ]
    total = 0.0
    any_numeric = False
    for col in numeric_cols:
        val = norm.clean_amount(raw.get(col))
        if val is not None:
            row[col] = val
            total += val
            any_numeric = True
        else:
            row[col] = None
    if not any_numeric:
        errors.append("no numeric columns found")
    row["total"] = total
    if errors:
        return None, errors
    return row, []


def _normalize_wishlist_row(raw: dict[str, Any], _: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    item = norm.clean_string(raw.get("item"))
    if not item:
        errors.append("missing item")
    target = norm.clean_amount(raw.get("target_amount"))
    if target is None or target <= 0:
        errors.append("invalid target_amount")
    if errors:
        return None, errors
    row = {
        "item": item,
        "target_amount": target,
        "saved_so_far": norm.clean_amount(raw.get("saved_so_far")) or 0.0,
        "priority": norm.clean_string(raw.get("priority")),
        "source": "import",
        "created_at": now_utc().isoformat(),
        "status": norm.clean_string(raw.get("status")) or "active",
    }
    return row, []


def _normalize_goal_row(raw: dict[str, Any], _: str | None) -> tuple[dict[str, Any] | None, list[str]]:
    errors: list[str] = []
    name = norm.clean_string(raw.get("goal_name"))
    if not name:
        errors.append("missing goal_name")
    target = norm.clean_amount(raw.get("target_amount"))
    if target is None or target <= 0:
        errors.append("invalid target_amount")
    if errors:
        return None, errors
    current: float = norm.clean_amount(raw.get("current_amount")) or 0.0
    monthly: float = norm.clean_amount(raw.get("monthly_contribution")) or 0.0
    assert target is not None  # guarded above
    pct = (current / target * 100.0) if target > 0 else 0.0
    months_left = int(max(0.0, (target - current) / monthly)) if monthly > 0 else 0
    row: dict[str, Any] = {
        "goal_name": name,
        "target_amount": target,
        "current_amount": current,
        "monthly_contribution": monthly,
        "pct_complete": round(pct, 2),
        "months_left": months_left,
        "status": norm.clean_string(raw.get("status")) or "active",
    }
    return row, []


NORMALIZERS = {
    "expenses": _normalize_expense_row,
    "investments": _normalize_investment_row,
    "wishlist": _normalize_wishlist_row,
    "goals_a": _normalize_goal_row,
    "goals_b": _normalize_goal_row,
}


def dedup_key_for(target_table: str, normalized_row: dict[str, Any]) -> str:
    if target_table == "expenses":
        return expense_dedup_key(
            row_date=_as_date(normalized_row["date"]),
            expense_name=normalized_row["expense_name"],
            amount=float(normalized_row["amount"]),
            payment_method=normalized_row["payment_method"],
            person_name=normalized_row.get("person_name"),
        )
    if target_table == "investments":
        return generic_dedup_key(["investments", str(normalized_row["month"])])
    if target_table == "wishlist":
        return generic_dedup_key(
            ["wishlist", normalized_row["item"], f"{normalized_row['target_amount']:.2f}"]
        )
    if target_table in {"goals_a", "goals_b"}:
        return generic_dedup_key(
            [target_table, normalized_row["goal_name"], f"{normalized_row['target_amount']:.2f}"]
        )
    return generic_dedup_key([target_table, json.dumps(normalized_row, sort_keys=True, default=str)])


def _as_date(value: Any) -> date:
    if isinstance(value, date):
        return value
    return norm.parse_date(value) or date.today()


# ---------- dry run / commit ----------


@dataclass
class ChecksumEntry:
    day: str
    computed_total: float
    declared_total: float
    match: bool
    delta: float

    def to_dict(self) -> dict[str, Any]:
        return {
            "day": self.day,
            "computed_total": round(self.computed_total, 2),
            "declared_total": round(self.declared_total, 2),
            "match": self.match,
            "delta": round(self.delta, 2),
        }


class DryRunOutcome:
    def __init__(
        self,
        rows: list[dict[str, Any]],
        errors: list[tuple[int, list[str]]],
        duplicates: list[int],
        balance_adjusts: list[dict[str, Any]] | None = None,
        checksum_report: list[ChecksumEntry] | None = None,
        warnings: list[tuple[int, list[str]]] | None = None,
        skipped: list[int] | None = None,
    ) -> None:
        self.rows = rows  # normalized, valid, NOT yet inserted (includes dedup_key in __meta)
        self.errors = errors
        self.duplicates = duplicates
        self.balance_adjusts: list[dict[str, Any]] = balance_adjusts or []
        self.checksum_report: list[ChecksumEntry] = checksum_report or []
        self.warnings: list[tuple[int, list[str]]] = warnings or []
        self.skipped: list[int] = skipped or []


SYNTHETIC_TARGETS = {
    "__payment_dual",
    "__tags_combined",
    "__cash_snapshot",
    "__online_snapshot",
}


@dataclass
class _PresetRunState:
    """Mutable state threaded through preset preprocessing."""

    last_cash: float | None = None
    last_online: float | None = None
    # per-day aggregation
    computed_by_day: dict[str, float] = field(default_factory=dict)
    declared_by_day: dict[str, float] = field(default_factory=dict)
    balance_adjusts: list[dict[str, Any]] = field(default_factory=list)
    skipped: list[int] = field(default_factory=list)
    warnings: list[tuple[int, list[str]]] = field(default_factory=list)


def _preset_preprocess_row(
    idx: int,
    record: dict[str, Any],
    row_filters: dict[str, Any],
    date_format: str | None,
    state: _PresetRunState,
) -> tuple[dict[str, Any] | None, bool]:
    """Apply preset filters to a single row.

    Returns ``(processed_row, skip_row)`` — if skip_row is True, the caller must
    NOT feed the row to the target-table normalizer (it was a summary or a
    balance-adjust). If a processed_row is returned with skip_row=False, the
    synthetic keys have been resolved into real target keys.
    """
    # synthetic keys may appear as mapping targets; pull them out now
    # clean_string immediately so pandas NaN never reaches downstream None checks
    payment_dual = norm.clean_string(record.pop("__payment_dual", None))
    tags_combined = norm.clean_string(record.pop("__tags_combined", None))
    cash_snapshot = record.pop("__cash_snapshot", None)
    online_snapshot = record.pop("__online_snapshot", None)

    skip_values = {v.lower() for v in row_filters.get("skip_when_payment_equals", [])}
    detect_balance_adjust = bool(row_filters.get("detect_balance_adjust", False))
    skip_when_amount_zero = bool(row_filters.get("skip_when_amount_zero", False))

    # --- Blank placeholder-row detection (zero-spend days with no data) ---
    if skip_when_amount_zero and payment_dual is None and tags_combined is None:
        raw_name = norm.clean_string(record.get("expense_name"))
        raw_amount = norm.clean_amount(record.get("amount"))
        if raw_name is None and (raw_amount is None or raw_amount == 0.0):
            state.skipped.append(idx)
            return None, True

    # --- Total-row detection ---
    raw_payment = payment_dual  # already cleaned above
    is_total_row = raw_payment is not None and raw_payment.lower() in skip_values
    if not is_total_row and raw_payment is not None:
        pm_mapped = norm.parse_payment_dual(raw_payment)
        if pm_mapped == norm.PAYMENT_TOTAL_SENTINEL:
            is_total_row = True

    # derive the row's day for checksum aggregation
    parsed_date = norm.parse_date(record.get("date"), date_format)
    day_iso = parsed_date.isoformat() if parsed_date else None

    if is_total_row:
        # Record the declared total (amount column on the Total row)
        declared = norm.clean_amount(record.get("amount"))
        if day_iso and declared is not None:
            state.declared_by_day[day_iso] = declared
        # Update last-known snapshots from the Total row when present
        cs = norm.clean_amount(cash_snapshot)
        os_ = norm.clean_amount(online_snapshot)
        if cs is not None:
            state.last_cash = cs
        if os_ is not None:
            state.last_online = os_
        state.skipped.append(idx)
        return None, True

    # --- Balance-adjust detection ---
    amount = norm.clean_amount(record.get("amount"))
    if detect_balance_adjust and (amount is None or amount == 0) and not raw_payment:
        cs = norm.clean_amount(cash_snapshot)
        os_ = norm.clean_amount(online_snapshot)
        if cs is not None or os_ is not None:
            cash_delta = 0.0 if cs is None or state.last_cash is None else cs - state.last_cash
            online_delta = 0.0 if os_ is None or state.last_online is None else os_ - state.last_online
            if abs(cash_delta) > 0.01 or abs(online_delta) > 0.01:
                state.balance_adjusts.append(
                    {
                        "asof": (parsed_date.isoformat() if parsed_date else now_utc().isoformat()),
                        "cash_balance": cs if cs is not None else state.last_cash or 0.0,
                        "online_balance": os_ if os_ is not None else state.last_online or 0.0,
                        "reason": "manual_adjust",
                    }
                )
                if cs is not None:
                    state.last_cash = cs
                if os_ is not None:
                    state.last_online = os_
                state.skipped.append(idx)
                return None, True

    # --- Regular expense row ---
    warnings: list[str] = []
    if raw_payment is not None:
        pm = norm.parse_payment_dual(raw_payment)
        if pm and pm != norm.PAYMENT_TOTAL_SENTINEL:
            record["payment_method"] = pm
    if tags_combined is not None:
        tc, tag_warnings = norm.parse_combined_tags(tags_combined)
        if tc:
            record["type_category"] = tc
        warnings.extend(tag_warnings)

    # update last-known snapshots from rolling balances
    cs = norm.clean_amount(cash_snapshot)
    os_ = norm.clean_amount(online_snapshot)
    if cs is not None:
        state.last_cash = cs
    if os_ is not None:
        state.last_online = os_

    # accumulate per-day computed total
    if day_iso and amount is not None and amount > 0:
        state.computed_by_day[day_iso] = state.computed_by_day.get(day_iso, 0.0) + amount

    if warnings:
        state.warnings.append((idx, warnings))

    return record, False


def _build_checksum_report(state: _PresetRunState) -> list[ChecksumEntry]:
    report: list[ChecksumEntry] = []
    days = set(state.computed_by_day) | set(state.declared_by_day)
    for day in sorted(days):
        computed = state.computed_by_day.get(day, 0.0)
        declared = state.declared_by_day.get(day, 0.0)
        delta = computed - declared
        report.append(
            ChecksumEntry(
                day=day,
                computed_total=computed,
                declared_total=declared,
                match=abs(delta) <= 1.0,
                delta=delta,
            )
        )
    return report


def dry_run(
    df: pd.DataFrame,
    target_table: str,
    mapping: dict[str, str],
    date_format: str | None,
    data_dir: Path,
    row_filters: dict[str, Any] | None = None,
) -> DryRunOutcome:
    if target_table not in NORMALIZERS:
        raise ValueError(f"unsupported target table: {target_table}")

    normalizer = NORMALIZERS[target_table]
    dedup_seen = load_dedup_keys(data_dir)

    good_rows: list[dict[str, Any]] = []
    errors: list[tuple[int, list[str]]] = []
    duplicates: list[int] = []

    filters = row_filters or {}
    preset_active = any(v in SYNTHETIC_TARGETS for v in mapping.values()) or bool(filters)
    state = _PresetRunState()

    # Apply mapping: relabel columns to schema names (synthetic names pass through)
    renamed = df.rename(columns=mapping)

    for idx, record in enumerate(renamed.to_dict(orient="records")):
        if preset_active:
            processed, skip_row = _preset_preprocess_row(
                idx, record, filters, date_format, state
            )
            if skip_row:
                continue
            record = processed if processed is not None else record

        normalized, row_errors = normalizer(record, date_format)
        if normalized is None:
            errors.append((idx, row_errors))
            continue
        key = dedup_key_for(target_table, normalized)
        if key in dedup_seen:
            duplicates.append(idx)
            continue
        normalized["__dedup_key"] = key
        normalized["__row_index"] = idx
        good_rows.append(normalized)
        dedup_seen.add(key)  # prevent in-batch duplicates too

    checksum_report = _build_checksum_report(state) if preset_active else []
    return DryRunOutcome(
        rows=good_rows,
        errors=errors,
        duplicates=duplicates,
        balance_adjusts=state.balance_adjusts,
        checksum_report=checksum_report,
        warnings=state.warnings,
        skipped=state.skipped,
    )


def commit(
    outcome: DryRunOutcome,
    target_table: str,
    on_invalid: str,
    batch_id: str,
    ledger: LedgerWriter,
    data_dir: Path,
) -> dict[str, int]:
    if on_invalid == "abort" and outcome.errors:
        raise ValueError(f"{len(outcome.errors)} invalid rows; aborting")

    inserted = 0
    drafted = 0
    error_count = len(outcome.errors)
    balance_rows_written = 0

    new_dedup_keys: list[str] = []
    for row in outcome.rows:
        key = row.pop("__dedup_key", None)
        row.pop("__row_index", None)
        full_row = _build_full_row(target_table, row, batch_id)
        ledger.append(target_table, full_row)
        inserted += 1
        if key:
            new_dedup_keys.append(key)
    append_dedup_keys(data_dir, new_dedup_keys)

    # Preset-driven balance-adjust rows go to balances.csv
    for adj in outcome.balance_adjusts:
        ledger.append(
            "balances",
            {
                "asof": adj["asof"],
                "cash_balance": float(adj["cash_balance"]),
                "online_balance": float(adj["online_balance"]),
                "reason": adj.get("reason", "manual_adjust"),
            },
        )
        balance_rows_written += 1

    if on_invalid == "draft":
        for row_idx, row_errors in outcome.errors:
            draft_row = {
                "id": str(ulid.new()),
                "target_table": target_table,
                "row_json": json.dumps({"row_index": row_idx}, default=str),
                "errors": "; ".join(row_errors),
                "source_filename": "",
                "created_at": now_utc().isoformat(),
                "import_batch_id": batch_id,
            }
            ledger.append("drafts", draft_row)
            drafted += 1

    return {
        "inserted": inserted,
        "duplicates": len(outcome.duplicates),
        "drafted": drafted,
        "errors": error_count,
        "balance_adjusts": balance_rows_written,
        "skipped": len(outcome.skipped),
    }


def _build_full_row(target_table: str, row: dict[str, Any], batch_id: str) -> dict[str, Any]:
    if target_table == "expenses":
        return {
            "id": str(ulid.new()),
            "date": _as_date(row["date"]).isoformat(),
            "created_at": now_utc().isoformat(),
            "expense_name": row["expense_name"],
            "type_category": row["type_category"],
            "payment_method": row["payment_method"],
            "paid_for_someone": bool(row.get("paid_for_someone")),
            "paid_by_someone": bool(row.get("paid_by_someone")),
            "person_name": row.get("person_name"),
            "amount": float(row["amount"]),
            "cash_balance_after": 0.0,
            "online_balance_after": 0.0,
            "source": "import",
            "raw_transcript": None,
            "notes": row.get("notes"),
            "import_batch_id": batch_id,
            "paid_for_method": row.get("paid_for_method"),
            "adjustment_type": row.get("adjustment_type"),
        }
    if target_table == "investments":
        full = {**row, "import_batch_id": batch_id}
        return full
    if target_table == "wishlist":
        return {
            "id": str(ulid.new()),
            "item": row["item"],
            "target_amount": float(row["target_amount"]),
            "saved_so_far": float(row.get("saved_so_far") or 0.0),
            "priority": row.get("priority"),
            "source": "import",
            "created_at": row.get("created_at") or now_utc().isoformat(),
            "status": row.get("status") or "active",
            "import_batch_id": batch_id,
        }
    if target_table in {"goals_a", "goals_b"}:
        return {
            "goal_id": str(ulid.new()),
            "goal_name": row["goal_name"],
            "target_amount": float(row["target_amount"]),
            "current_amount": float(row.get("current_amount") or 0.0),
            "monthly_contribution": float(row.get("monthly_contribution") or 0.0),
            "pct_complete": float(row.get("pct_complete") or 0.0),
            "months_left": int(row.get("months_left") or 0),
            "status": row.get("status") or "active",
            "import_batch_id": batch_id,
            # goals_b extra fields default to 0
            "manual_saved": float(row.get("current_amount") or 0.0),
            "auto_added": 0.0,
            "total_saved": float(row.get("current_amount") or 0.0),
        }
    raise ValueError(f"unsupported target table: {target_table}")


def write_batch_meta(
    data_dir: Path,
    batch_id: str,
    source_filename: str,
    file_sha256: str,
    sheet_name: str | None,
    target_table: str,
    mapping: dict[str, str],
    row_counts: dict[str, int],
) -> Path:
    imports_dir = data_dir / "imports"
    imports_dir.mkdir(parents=True, exist_ok=True)
    meta = {
        "batch_id": batch_id,
        "source_filename": source_filename,
        "sha256": file_sha256,
        "sheet_name": sheet_name,
        "target_table": target_table,
        "mapping": mapping,
        "row_counts": row_counts,
        "imported_at": now_utc().isoformat(),
    }
    p = imports_dir / f"{batch_id}.meta.json"
    p.write_text(json.dumps(meta, indent=2, default=str), encoding="utf-8")
    return p


def load_batch_meta(data_dir: Path, batch_id: str) -> dict[str, Any] | None:
    p = data_dir / "imports" / f"{batch_id}.meta.json"
    if not p.exists():
        return None
    data: dict[str, Any] = json.loads(p.read_text(encoding="utf-8"))
    return data


def rollback_batch(ledger: LedgerWriter, data_dir: Path, batch_id: str) -> dict[str, int]:
    meta = load_batch_meta(data_dir, batch_id)
    if meta is None:
        return {"removed": 0}
    target = meta["target_table"]
    removed = ledger.delete_where(target, "import_batch_id", batch_id)
    # also remove drafts tagged to the batch
    ledger.delete_where("drafts", "import_batch_id", batch_id)
    # delete meta file
    (data_dir / "imports" / f"{batch_id}.meta.json").unlink(missing_ok=True)
    return {"removed": removed}
