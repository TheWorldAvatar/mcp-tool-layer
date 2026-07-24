"""Report chemistry_cache.sqlite size and row counts (A3 backlog)."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from mini_marie.marie.chemistry.chemistry_cache import db_path

CORPUS_TABLES = [
    "corpus_species",
    "corpus_species_names",
    "corpus_species_formula",
    "corpus_species_pka",
    "corpus_species_uses",
    "corpus_species_properties",
    "corpus_species_references",
    "corpus_mechanisms",
    "corpus_reaction_edges",
    "corpus_reaction_models",
    "corpus_qm_results",
    "corpus_zeolite_materials",
    "corpus_zeolite_properties",
    "corpus_zeolite_by_framework",
    "corpus_zeolite_reference",
    "corpus_provenance_persons",
    "corpus_provenance_refs",
    "atomic_calls",
    "atomic_rows",
]


def main() -> None:
    path = Path(db_path())
    conn = sqlite3.connect(path)
    tables = {
        r[0]: conn.execute(f"SELECT COUNT(*) FROM {r[0]}").fetchone()[0]
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name"
        )
    }
    conn.close()
    report = {
        "db_path": str(path),
        "size_mb": round(path.stat().st_size / (1024 * 1024), 2),
        "corpus_tables": {t: tables.get(t, 0) for t in CORPUS_TABLES if t in tables},
        "all_tables": tables,
    }
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
