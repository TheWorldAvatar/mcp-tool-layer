"""CLI: write materialized view catalog JSON."""

from __future__ import annotations

from pathlib import Path

from mini_marie.materialized_catalog import write_catalog


def main() -> None:
    out = Path(__file__).resolve().parent / "materialized_catalog.json"
    write_catalog(out, domain="mof")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
