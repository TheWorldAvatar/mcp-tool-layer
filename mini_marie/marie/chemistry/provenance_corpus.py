"""OntoProvenance persons + publication reference corpus."""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.corpus_common import ensure_warm_state, mark_paused, warm_state_row
from mini_marie.marie.chemistry.corpus_fetch import retry_corpus_fetch
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import sparql_timeout
from mini_marie.marie.chemistry.query_builder import _execute, _prefix_block
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import format_tsv

PERSONS_CORPUS_ID = "ontoprovenance_persons"
PUBS_CORPUS_ID = "ontoprovenance_publications"


def persons_query() -> str:
    return (
        _prefix_block("ontoprovenance")
        + """
SELECT ?person ?name WHERE {
  ?person a op:Person .
  ?person ?p ?name .
  FILTER(isLiteral(?name))
}
"""
    )


def publications_query() -> str:
    return (
        _prefix_block("ontoprovenance")
        + """
SELECT ?ref ?label ?prop WHERE {
  ?ref ?prop ?label .
  FILTER(isLiteral(?label))
  FILTER(CONTAINS(LCASE(STR(?prop)), "name") || CONTAINS(LCASE(STR(?prop)), "title") || CONTAINS(LCASE(STR(?ref)), "reference") || CONTAINS(LCASE(STR(?ref)), "publication") || CONTAINS(LCASE(STR(?ref)), "doi"))
}
"""
    )


def species_references_query() -> str:
    return (
        _prefix_block("ontospecies")
        + """
SELECT ?ref ?label WHERE {
  ?ref rdfs:label ?label .
  FILTER(CONTAINS(STR(?ref), "/Reference_"))
  FILTER(isLiteral(?label))
}
"""
    )


class ProvenanceCorpusStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = str(path or db_path())
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS corpus_provenance_persons (
                person_iri TEXT NOT NULL,
                name_value TEXT NOT NULL,
                name_value_lc TEXT NOT NULL,
                UNIQUE(person_iri, name_value)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_prov_person_lc ON corpus_provenance_persons(name_value_lc);

            CREATE TABLE IF NOT EXISTS corpus_provenance_refs (
                ref_iri TEXT NOT NULL,
                property_local TEXT,
                label_value TEXT NOT NULL,
                label_value_lc TEXT NOT NULL,
                UNIQUE(ref_iri, property_local, label_value)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_prov_ref_lc ON corpus_provenance_refs(label_value_lc);

            CREATE TABLE IF NOT EXISTS corpus_species_references (
                ref_iri TEXT PRIMARY KEY,
                label_value TEXT NOT NULL,
                label_value_lc TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_species_ref_lc ON corpus_species_references(label_value_lc);
            """
        )

    def warm_all(self) -> Dict[str, Any]:
        started = time.perf_counter()
        person_rows = retry_corpus_fetch("provenance persons", lambda: _execute("ontoprovenance", persons_query()))
        pub_rows = retry_corpus_fetch(
            "provenance publications",
            lambda: _execute("ontoprovenance", publications_query()),
        )
        ref_rows = retry_corpus_fetch(
            "species references",
            lambda: _execute("ontospecies", species_references_query()),
        )
        p_ins = 0
        for row in person_rows:
            person = str(row.get("person", "")).strip()
            name = str(row.get("name", "")).strip()
            if not person or not name:
                continue
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_provenance_persons
                (person_iri, name_value, name_value_lc)
                VALUES (?, ?, ?)
                """,
                (person, name, name.lower()),
            )
            p_ins += cur.rowcount
        r_ins = 0
        for row in pub_rows:
            ref = str(row.get("ref", "")).strip()
            label = str(row.get("label", "")).strip()
            prop = str(row.get("prop", "")).split("#")[-1]
            if not ref or not label:
                continue
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_provenance_refs
                (ref_iri, property_local, label_value, label_value_lc)
                VALUES (?, ?, ?, ?)
                """,
                (ref, prop, label, label.lower()),
            )
            r_ins += cur.rowcount
        s_ins = 0
        for row in ref_rows:
            ref = str(row.get("ref", "")).strip()
            label = str(row.get("label", "")).strip()
            if not ref or not label:
                continue
            cur = self._conn.execute(
                """
                INSERT OR REPLACE INTO corpus_species_references
                (ref_iri, label_value, label_value_lc)
                VALUES (?, ?, ?)
                """,
                (ref, label, label.lower()),
            )
            s_ins += cur.rowcount
        now = time.time()
        for corpus_id, total, rows_added in (
            (PERSONS_CORPUS_ID, len(person_rows), p_ins),
            (PUBS_CORPUS_ID, len(pub_rows), r_ins),
        ):
            ensure_warm_state(self._conn, corpus_id=corpus_id, total=max(total, 1), batch_size=1)
            self._conn.execute(
                """
                UPDATE corpus_warm_state SET offset_next=1, species_indexed=?, name_rows_indexed=?,
                  batches_done=1, status='complete', updated_at=?, error=''
                WHERE corpus_id=?
                """,
                (total, rows_added, now, corpus_id),
            )
        self._conn.commit()
        return {
            "person_rows_fetched": len(person_rows),
            "person_rows_inserted": p_ins,
            "publication_rows_fetched": len(pub_rows),
            "publication_rows_inserted": r_ins,
            "species_reference_rows_fetched": len(ref_rows),
            "species_reference_rows_inserted": s_ins,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }

    def lookup_ref_label(self, ref_fragment: str, *, limit: int = 20) -> List[Dict[str, Any]]:
        frag = ref_fragment.strip().lower()
        if not frag:
            return []
        like = f"%{frag}%"
        rows = [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT ref_iri, label_value FROM corpus_species_references
                WHERE ref_iri LIKE ? OR label_value_lc LIKE ?
                LIMIT ?
                """,
                (like, like, max(1, int(limit))),
            )
        ]
        if rows:
            return rows
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT ref_iri, label_value FROM corpus_provenance_refs
                WHERE ref_iri LIKE ? OR label_value_lc LIKE ?
                LIMIT ?
                """,
                (like, like, max(1, int(limit))),
            )
        ]

    def lookup_ref_label_tsv(self, ref_fragment: str, **kwargs: Any) -> str:
        rows = self.lookup_ref_label(ref_fragment, **kwargs)
        return format_tsv(rows) if rows else "No results"

    def join_pka_provenance_tsv(self, *, limit: int = 50) -> str:
        rows = [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT p.species_iri, p.pka_value, p.provenance, COALESCE(r.label_value, '') AS ref_label
                FROM corpus_species_pka p
                LEFT JOIN corpus_species_references r ON p.provenance = r.ref_iri
                WHERE p.provenance != ''
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
        ]
        return format_tsv(rows) if rows else "No results"

    def search_persons(self, query: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT person_iri, name_value FROM corpus_provenance_persons
                WHERE name_value_lc LIKE ? LIMIT ?
                """,
                (f"%{q}%", max(1, int(limit))),
            )
        ]

    def search_refs(self, query: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT ref_iri, property_local, label_value FROM corpus_provenance_refs
                WHERE label_value_lc LIKE ? OR ref_iri LIKE ? LIMIT ?
                """,
                (f"%{q}%", f"%{query.strip()}%", max(1, int(limit))),
            )
        ]

    def search_persons_tsv(self, query: str, **kwargs: Any) -> str:
        rows = self.search_persons(query, **kwargs)
        return format_tsv(rows) if rows else "No results"

    def stats(self) -> Dict[str, Any]:
        return {
            "person_rows": int(self._conn.execute("SELECT COUNT(*) FROM corpus_provenance_persons").fetchone()[0]),
            "publication_rows": int(self._conn.execute("SELECT COUNT(*) FROM corpus_provenance_refs").fetchone()[0]),
            "warm_state_persons": warm_state_row(self._conn, PERSONS_CORPUS_ID),
            "warm_state_publications": warm_state_row(self._conn, PUBS_CORPUS_ID),
            "endpoint": endpoint("ontoprovenance"),
            "sparql_timeout_s": sparql_timeout("ontoprovenance"),
        }


def warm_provenance_corpus(*, skip_health_check: bool = False) -> Dict[str, Any]:
    store = ProvenanceCorpusStore()
    try:
        if not skip_health_check:
            health = namespace_health_ok("ontoprovenance")
            if not health.get("ok"):
                mark_paused(store._conn, PERSONS_CORPUS_ID, f"health check failed: {health}")
                return {"errors": [health], "paused": True}
        batch = store.warm_all()
        return {"batch": batch, "stats": store.stats(), "errors": [], "paused": False}
    except Exception as exc:
        mark_paused(store._conn, PERSONS_CORPUS_ID, str(exc))
        return {"errors": [{"error": str(exc)}], "paused": True}
    finally:
        store.close()
