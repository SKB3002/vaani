"""Narrator - turns a ``MonthlyStatsBundle`` into prose with stat-ref tokens.

Anti-hallucination contract (Plan goal G5):

1. The LLM is given the deterministic stats bundle plus a flat list of
   allowed stat-ref keys.
2. The LLM returns JSON matching the ``Narration`` schema.
3. Every narrative field is scanned for digits - zero digits allowed.
4. Every ``{{stat_ref}}`` placeholder must reference a key in the allow-list.
5. On contract violation: retry once with the violation list appended to the
   conversation. On the second violation: raise ``NarrationContractError``.
6. On ``LLMTransportError`` (Groq unreachable, 5xx, network): return ``None``
   so the route can degrade gracefully (Plan goal G7). Bad-JSON / contract
   failures DO raise - those are bugs, not availability issues.

Caller is responsible for caching - this module never touches the cache.
"""
from __future__ import annotations

import json
import logging
import re
from typing import Any, Literal

from pydantic import BaseModel, Field, ValidationError

from app.services.insights.aggregator import MonthlyStatsBundle
from app.services.llm import (
    GroqLLMClient,
    LLMError,
    LLMParseError,
    LLMTransportError,
)
from app.services.prompts.insights_briefing import INSIGHTS_BRIEFING_SYSTEM

log = logging.getLogger("vaani.insights.narrator")

_DIGIT_RE = re.compile(r"\d")
_STAT_REF_RE = re.compile(r"\{\{([a-z0-9_]+)\}\}")
# Matches any ``{{...}}`` token so we can flag malformed placeholders too
# (e.g. ``{{Total Spend}}`` would not match _STAT_REF_RE but we still want
# to surface it as a violation).
_ANY_BRACE_RE = re.compile(r"\{\{([^}]*)\}\}")


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------


class NarrationContractError(LLMError):
    """Narrator output violated the digit-free / stat-ref contract twice."""

    def __init__(
        self, message: str, *, violations: list[str], raw: str
    ) -> None:
        super().__init__(message)
        self.violations = violations
        self.raw = raw


# ---------------------------------------------------------------------------
# Output schema
# ---------------------------------------------------------------------------


Tone = Literal["encouraging", "neutral", "warning"]


class NarrationSection(BaseModel):
    title: str
    narrative: str
    stat_refs: list[str] = Field(default_factory=list)


class Narration(BaseModel):
    headline: str
    tone: Tone
    sections: list[NarrationSection]


# ---------------------------------------------------------------------------
# Stat-ref extraction
# ---------------------------------------------------------------------------


def _slugify(text: str) -> str:
    """Lower-case, collapse non-alphanumerics into single underscores.

    Stable across calls - the frontend stat-ref renderer (Task 8) uses the
    same rule to look up bundle values. Examples::

        "Need, Food & Drinks" -> "need_food_drinks"
        "Want, Travel"        -> "want_travel"
        "Investment, Misc."   -> "investment_misc"
    """
    lowered = text.strip().lower()
    out = re.sub(r"[^a-z0-9]+", "_", lowered)
    return out.strip("_")


def extract_allowed_stat_refs(bundle: MonthlyStatsBundle) -> list[str]:
    """Return the flat allow-list of stat-ref keys for one bundle.

    Generous (typically ~50-100 keys) but stable: the same bundle shape
    always produces the same set. The frontend renderer (Task 8) consumes
    this list to know which keys to bind.

    Slug rules: see ``_slugify`` above. Index-based keys use ``_{i}`` with
    one-based indices so they read naturally in prose ("merchant_1_name").
    """
    keys: set[str] = set()

    # Top-level scalars.
    keys.update(
        [
            "month",
            "currency",
            "current_total",
            "previous_total",
            "trailing_3m_total",
            "trailing_3m_avg",
            "trailing_12m_total",
            "trailing_12m_avg",
            "txn_count",
            "previous_txn_count",
            "net_cashflow",
            "investment_total_current",
            "investment_total_prev_month",
            "investment_delta_pct",
            "investment_delta_abs",
        ]
    )

    # Per-category totals + deltas (vs previous month, vs 3m avg).
    for cat in bundle.current_month.by_category:
        slug = _slugify(cat.category)
        if not slug:
            continue
        keys.add(f"{slug}_total")
        keys.add(f"{slug}_txn_count")

    for delta in bundle.category_deltas_vs_prev:
        slug = _slugify(delta.category)
        if not slug:
            continue
        keys.add(f"{slug}_delta_abs")
        keys.add(f"{slug}_delta_pct")
        keys.add(f"{slug}_previous")

    for delta in bundle.category_deltas_vs_3m_avg:
        slug = _slugify(delta.category)
        if not slug:
            continue
        keys.add(f"{slug}_vs_3m_delta_abs")
        keys.add(f"{slug}_vs_3m_delta_pct")

    # Top merchants (current month only).
    for i, merchant in enumerate(bundle.current_month.top_merchants[:5], start=1):
        keys.add(f"merchant_{i}_name")
        keys.add(f"merchant_{i}_total")
        keys.add(f"merchant_{i}_count")
        name = str(merchant.get("name", ""))
        slug = _slugify(name)
        if slug:
            keys.add(f"merchant_{slug}_total")

    # Goals.
    for i, goal in enumerate(bundle.goals, start=1):
        keys.add(f"goal_{i}_name")
        keys.add(f"goal_{i}_pct_complete")
        keys.add(f"goal_{i}_target")
        keys.add(f"goal_{i}_current")
        slug = _slugify(goal.goal_name)
        if slug:
            keys.add(f"goal_{slug}_pct_complete")

    # Budget rows.
    for util in bundle.budget_utilisation:
        slug = _slugify(util.category)
        if not slug:
            continue
        keys.add(f"budget_{slug}_budgeted")
        keys.add(f"budget_{slug}_actual")
        keys.add(f"budget_{slug}_remaining")
        keys.add(f"budget_{slug}_utilisation_pct")

    # Largest transactions.
    for i in range(1, len(bundle.top_n_largest_txns[:5]) + 1):
        keys.add(f"largest_txn_{i}_name")
        keys.add(f"largest_txn_{i}_amount")

    # Filter to slug-shaped keys only - guards against odd characters
    # leaking from category strings.
    return sorted(k for k in keys if re.fullmatch(r"[a-z0-9_]+", k))


# ---------------------------------------------------------------------------
# Contract validation
# ---------------------------------------------------------------------------


def find_violations(narration: Narration, allowed_refs: set[str]) -> list[str]:
    """Return human-readable violation strings; empty list means clean.

    Walks ``headline`` plus every section's ``title`` and ``narrative``.
    Reports two kinds of issue:

    - ``digit_in_field:<path>`` if a digit appears anywhere in the text.
    - ``unknown_stat_ref:<key>`` if a ``{{...}}`` token is malformed or
      references a key outside ``allowed_refs``.
    """
    violations: list[str] = []
    fields: list[tuple[str, str]] = [("headline", narration.headline)]
    for i, section in enumerate(narration.sections):
        fields.append((f"sections[{i}].title", section.title))
        fields.append((f"sections[{i}].narrative", section.narrative))

    for path, text in fields:
        if _DIGIT_RE.search(text):
            violations.append(f"digit_in_field:{path}")
        # Catch malformed braces first.
        for raw_token in _ANY_BRACE_RE.findall(text):
            if not re.fullmatch(r"[a-z0-9_]+", raw_token):
                violations.append(f"unknown_stat_ref:{raw_token}")
        # Then well-formed-but-unknown keys.
        for key in _STAT_REF_RE.findall(text):
            if key not in allowed_refs:
                violations.append(f"unknown_stat_ref:{key}")

    return violations


# ---------------------------------------------------------------------------
# Bundle trimming
# ---------------------------------------------------------------------------


def _summarise_bundle_for_prompt(bundle: MonthlyStatsBundle) -> dict[str, Any]:
    """Trim the bundle to the fields the narrator actually needs.

    Drops noisy arrays (full trailing-12m breakdown, all merchants) so the
    prompt fits comfortably under ~2k input tokens.
    """
    cm = bundle.current_month
    pm = bundle.previous_month
    return {
        "month": bundle.month,
        "currency": bundle.currency,
        "current_total": cm.net_spend,
        "previous_total": pm.net_spend,
        "trailing_3m_total": bundle.trailing_3m.net_spend,
        "trailing_12m_total": bundle.trailing_12m.net_spend,
        "txn_count": cm.txn_count,
        "previous_txn_count": pm.txn_count,
        "net_cashflow": bundle.net_cashflow,
        "investment_total_current": bundle.investment_total_current,
        "investment_total_prev_month": bundle.investment_total_prev_month,
        "investment_delta_pct": bundle.investment_delta_pct,
        "by_category_top": [c.model_dump() for c in cm.by_category[:8]],
        "by_type": cm.by_type,
        "top_merchants": cm.top_merchants[:5],
        "category_deltas_vs_prev_top": [
            d.model_dump() for d in bundle.category_deltas_vs_prev[:8]
        ],
        "category_deltas_vs_3m_avg_top": [
            d.model_dump() for d in bundle.category_deltas_vs_3m_avg[:8]
        ],
        "budget_utilisation": [u.model_dump() for u in bundle.budget_utilisation],
        "goals": [g.model_dump() for g in bundle.goals],
    }


# ---------------------------------------------------------------------------
# Public narrate function
# ---------------------------------------------------------------------------


async def narrate_briefing(
    bundle: MonthlyStatsBundle,
    *,
    llm: GroqLLMClient,
    max_retries: int = 1,
) -> Narration | None:
    """Call the LLM, validate the contract, retry once on violation.

    Returns ``Narration`` on success, ``None`` if the LLM is unreachable
    (so the route can render the bundle alone). Raises:

    - ``LLMParseError`` if the model returns invalid JSON twice.
    - ``NarrationContractError`` if the model emits digits or unknown
      stat-refs after exhausting the retry budget.
    """
    allowed_refs = extract_allowed_stat_refs(bundle)
    allowed_set = set(allowed_refs)

    user_payload = {
        "bundle": _summarise_bundle_for_prompt(bundle),
        "allowed_stat_refs": allowed_refs,
    }
    user_message = json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))

    messages: list[dict[str, str]] = [
        {"role": "system", "content": INSIGHTS_BRIEFING_SYSTEM},
        {"role": "user", "content": user_message},
    ]

    attempts = max(1, max_retries + 1)
    last_raw = ""
    last_violations: list[str] = []

    for attempt in range(attempts):
        try:
            raw = await llm.chat_json(
                system=INSIGHTS_BRIEFING_SYSTEM,
                user=messages[-1]["content"]
                if attempt == 0
                else _follow_up_user_message(last_raw, last_violations),
            )
        except LLMTransportError as exc:
            log.warning(
                "narrate_briefing: LLM unreachable (status=%s): %s",
                exc.status,
                exc,
            )
            return None

        last_raw = raw
        try:
            obj = json.loads(raw)
        except json.JSONDecodeError as exc:
            if attempt + 1 >= attempts:
                raise LLMParseError(
                    f"narrator returned invalid JSON: {exc}",
                    raw=raw,
                    transcript="",
                ) from exc
            last_violations = [f"invalid_json:{exc}"]
            continue

        try:
            narration = Narration.model_validate(obj)
        except ValidationError as exc:
            if attempt + 1 >= attempts:
                raise LLMParseError(
                    f"narrator output failed schema validation: {exc}",
                    raw=raw,
                    transcript="",
                ) from exc
            last_violations = [f"schema_error:{exc}"]
            continue

        violations = find_violations(narration, allowed_set)
        if not violations:
            return narration

        last_violations = violations
        if attempt + 1 >= attempts:
            raise NarrationContractError(
                "narrator output violated digit-free / stat-ref contract",
                violations=violations,
                raw=raw,
            )
        log.info(
            "narrate_briefing: retrying after contract violations: %s",
            violations,
        )

    # Defensive - the loop always returns or raises.
    raise NarrationContractError(
        "narrator failed without producing a result",
        violations=last_violations,
        raw=last_raw,
    )


def _follow_up_user_message(prev_raw: str, violations: list[str]) -> str:
    """Build the repair-retry user message.

    The previous raw output is included so the model can see what it wrote;
    the violation list is explicit so the fix is unambiguous.
    """
    return (
        "Your previous output violated the contract. Violations: "
        + json.dumps(violations, ensure_ascii=False)
        + ". Previous output: "
        + prev_raw
        + ". Return ONLY corrected JSON matching the schema. "
        + "NEVER use digits. Only use stat-ref keys from the allow-list "
        + "you were given in the original message."
    )


__all__ = [
    "Narration",
    "NarrationContractError",
    "NarrationSection",
    "Tone",
    "extract_allowed_stat_refs",
    "find_violations",
    "narrate_briefing",
]
