"""Rebuild corpus_species from corpus_species_names after DB recovery."""

from __future__ import annotations

import sqlite3
import time

from mini_marie.marie.chemistry.chemistry_cache import db_path


def main() -> None:
    conn = sqlite3.connect(db_path())
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS corpus_species (
            species_iri TEXT PRIMARY KEY,
            primary_label TEXT,
            indexed_at REAL NOT NULL
        );
        DELETE FROM corpus_species;
        """
    )
    now = time.time()
    conn.execute(
        """
        INSERT INTO corpus_species (species_iri, primary_label, indexed_at)
        SELECT DISTINCT species_iri, NULL, ?
        FROM corpus_species_names
        """,
        (now,),
    )
    conn.execute(
        """
        UPDATE corpus_species SET primary_label = (
          SELECT name_value FROM corpus_species_names n
          WHERE n.species_iri = corpus_species.species_iri AND n.name_type = 'label'
          ORDER BY LENGTH(name_value) LIMIT 1
        )
        """
    )
    n = conn.execute("SELECT COUNT(*) FROM corpus_species").fetchone()[0]
    conn.execute(
        """
        INSERT INTO corpus_warm_state
        (corpus_id, total_species, offset_next, batch_size, species_indexed,
         name_rows_indexed, batches_done, status, updated_at, cursor_subject)
        VALUES ('ontospecies_species', ?, ?, 50, ?, ?, 733, 'complete', ?, '')
        ON CONFLICT(corpus_id) DO UPDATE SET
          total_species=excluded.total_species,
          offset_next=excluded.offset_next,
          species_indexed=excluded.species_indexed,
          name_rows_indexed=excluded.name_rows_indexed,
          status='complete',
          updated_at=excluded.updated_at
        """,
        (n, n, n, conn.execute("SELECT COUNT(*) FROM corpus_species_names").fetchone()[0], now),
    )
    conn.commit()
    print({"species_rows": n, "integrity": conn.execute("PRAGMA integrity_check").fetchone()[0]})
    conn.close()


if __name__ == "__main__":
    main()
