"""CLI wrapper: `python -m scripts.bootstrap_cli`"""
from __future__ import annotations

from app.bootstrap import bootstrap


def main() -> None:
    bootstrap()
    print("FinEye bootstrapped: data/, .wal/, .tmp/ ready.")


if __name__ == "__main__":
    main()
