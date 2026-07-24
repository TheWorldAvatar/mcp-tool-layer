"""One-off repair: recreate corrupted corpus_species_pka table."""

from __future__ import annotations

import sqlite3

from mini_marie.marie.chemistry.chemistry_cache import db_path


def main() -> None:
    conn = sqlite3.connect(db_path())
    conn.execute("DROP TABLE IF EXISTS corpus_species_pka")
    conn.executescript(
        """
        CREATE TABLE corpus_species_pka (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            species_iri TEXT NOT NULL,
            pka_iri TEXT,
            pka_value TEXT NOT NULL,
            temperature TEXT,
            ionic_strength TEXT,
            method TEXT,
            reliability TEXT,
            acidity_label TEXT,
            provenance TEXT,
            UNIQUE(species_iri, pka_iri, pka_value, temperature, method)
        );
        CREATE INDEX IF NOT EXISTS idx_corpus_pka_species ON corpus_species_pka(species_iri);
        CREATE INDEX IF NOT EXISTS idx_corpus_pka_reliability ON corpus_species_pka(reliability);
        CREATE INDEX IF NOT EXISTS idx_corpus_pka_method ON corpus_species_pka(method);
        DELETE FROM corpus_warm_state WHERE corpus_id = 'ontospecies_pka';
        """
    )
    conn.commit()
    check = conn.execute("PRAGMA integrity_check").fetchone()[0]
    print({"integrity": check[:120], "pka_rows": 0})
    conn.close()


if __name__ == "__main__":
    main()
