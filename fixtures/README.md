# Fixtures

Small sample files for the import wizard and for manual smoke-testing.

| File | Purpose |
|------|---------|
| `sample_expenses.csv` | Five-row CSV exercising `₹`-stripped amounts, Indian grouping, enum coercion, UPI → `paid` heuristic, and the Investment row that must land in `expenses.csv` per §4.1. |

Unit tests generate their own in-memory `.xlsx` via `pandas` + `openpyxl` (see `tests/unit/test_import_sniff.py`) so we don't commit binary blobs.
