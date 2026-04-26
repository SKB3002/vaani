# FinEye

Personal finance tracker with voice-driven expense capture, pandas/CSV ledger, and AI categorization.

## Run

```bash
pip install -e ".[dev]"
python -m scripts.bootstrap_cli
python -m uvicorn app.main:app --reload
```

Open http://localhost:8000 and http://localhost:8000/docs.

## Seed demo data

```bash
python -m scripts.seed
```

## Tests

```bash
python -m pytest -x
python -m ruff check .
python -m mypy app/
```

> If your Python `Scripts/` dir is on `PATH`, you can drop the `python -m` prefix
> (`uvicorn …`, `pytest …`). On Windows, Scripts usually lives next to
> `python.exe` — add it to your user PATH once and these commands work bare.

> Portfolio README with hero screenshot + GIFs is generated in a later milestone.
