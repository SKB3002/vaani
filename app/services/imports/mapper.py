"""Fuzzy source-column → target-column mapping using rapidfuzz."""
from __future__ import annotations

from rapidfuzz import fuzz, process

from app.storage.schemas import SCHEMAS

# Hints: alternate names users often use, scored against schema targets.
_ALIASES: dict[str, list[str]] = {
    "date": ["date", "txn_date", "transaction_date", "when", "dt"],
    "amount": ["amount", "amt", "value", "price", "cost", "inr", "rupees"],
    "expense_name": ["expense_name", "name", "description", "desc", "item", "vendor", "merchant"],
    "type_category": ["type_category", "type", "category", "cat"],
    "payment_method": ["payment_method", "payment", "method", "paid_via", "mode"],
    "person_name": ["person_name", "person", "counterparty", "who"],
    "notes": ["notes", "note", "remarks", "comment"],
    "month": ["month", "period", "yyyymm"],
    "item": ["item", "name", "title", "wish"],
    "target_amount": ["target_amount", "target", "goal", "target_value"],
    "saved_so_far": ["saved_so_far", "saved", "collected"],
    "priority": ["priority", "prio"],
    "goal_id": ["goal_id", "id"],
    "goal_name": ["goal_name", "name", "title"],
}


def suggest_mapping(
    source_columns: list[str], target_table: str
) -> tuple[dict[str, str], dict[str, float]]:
    """Return (mapping source->target, confidence per source)."""
    schema = SCHEMAS[target_table]
    target_cols = [c for c in schema["columns"] if c != "import_batch_id"]
    mapping: dict[str, str] = {}
    confidence: dict[str, float] = {}

    for source in source_columns:
        best_target = None
        best_score = 0.0
        src_norm = str(source).strip().lower()
        for target in target_cols:
            candidates = [target.lower(), *_ALIASES.get(target, [])]
            match = process.extractOne(src_norm, candidates, scorer=fuzz.WRatio)
            if match is None:
                continue
            score = float(match[1])
            if score > best_score:
                best_score = score
                best_target = target
        if best_target is not None and best_score >= 60.0:
            mapping[source] = best_target
            confidence[source] = round(best_score / 100.0, 3)
    return mapping, confidence
