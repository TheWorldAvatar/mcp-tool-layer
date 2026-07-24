"""OntoSpecies physical properties corpus (H-bond counts, mass, TPSA, …)."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.corpus_common import (
    advance_warm_state,
    ensure_warm_state,
    mark_paused,
    migrate_cursor_column,
    resolve_cursor,
    warm_state_row,
)
from mini_marie.marie.chemistry.corpus_fetch import retry_corpus_fetch
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS, sparql_timeout
from mini_marie.marie.chemistry.query_builder import _execute, _prefix_block
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import format_tsv
from mini_marie.marie.chemistry.species_corpus import bootstrap_cursor_from_offset, species_iri_page_query

CORPUS_ID = "ontospecies_physprops"
DEFAULT_BATCH_SIZE = 50
RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_species_physprops_corpus --max-batches 0 --delay 3"

PHYSPROP_UNIONS = """
  {
    ?species os:hasHydrogenBondDonorCount ?n .
    ?n os:value ?propValue .
    BIND("hasHydrogenBondDonorCount" AS ?propLocal)
  } UNION {
    ?species os:hasHydrogenBondAcceptorCount ?n .
    ?n os:value ?propValue .
    BIND("hasHydrogenBondAcceptorCount" AS ?propLocal)
  } UNION {
    ?species os:hasExactMass ?n .
    ?n os:value ?propValue .
    BIND("hasExactMass" AS ?propLocal)
  } UNION {
    ?species os:hasMolecularWeight ?n .
    ?n os:value ?propValue .
    BIND("hasMolecularWeight" AS ?propLocal)
  } UNION {
    ?species os:hasTopologicalPolarSurfaceArea ?n .
    ?n os:value ?propValue .
    BIND("hasTopologicalPolarSurfaceArea" AS ?propLocal)
  }
"""


def physprops_for_iris_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?species ?propLocal ?propValue WHERE {{
  VALUES ?species {{ {values} }}
"""
        + PHYSPROP_UNIONS
        + "\n  FILTER(isLiteral(?propValue))\n}\n"
    )


def fetch_physprops_rows(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    iri_rows = _execute("ontospecies", species_iri_page_query(limit=limit, after_subject=after_subject))
    page_iris = [str(r.get("subject", "")).strip() for r in iri_rows if str(r.get("subject", "")).strip()]
    if not page_iris:
        return [], []
    raw = _execute("ontospecies", physprops_for_iris_query(page_iris))
    out: List[Dict[str, Any]] = []
    for row in raw:
        species = str(row.get("species", "")).strip()
        prop = str(row.get("propLocal", "")).strip()
        val = str(row.get("propValue", "")).strip()
        if not species or not prop or not val:
            continue
        out.append({"species_iri": species, "property_local": prop, "property_value": val})
    return out, page_iris


def fetch_physprops_rows_with_retry(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    return retry_corpus_fetch(
        "species physprops",
        lambda: fetch_physprops_rows(limit=limit, after_subject=after_subject),
    )


@dataclass
class PhyspropsWarmBatchResult:
    offset: int
    batch_size: int
    species_in_batch: int
    prop_rows: int
    elapsed_ms: int
    done: bool
    cursor_subject: str = ""


class SpeciesPhyspropsCorpusStore:
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
            CREATE TABLE IF NOT EXISTS corpus_species_properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                species_iri TEXT NOT NULL,
                property_local TEXT NOT NULL,
                property_value TEXT NOT NULL,
                UNIQUE(species_iri, property_local, property_value)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_props_species ON corpus_species_properties(species_iri);
            CREATE INDEX IF NOT EXISTS idx_corpus_props_name ON corpus_species_properties(property_local);
            """
        )
        migrate_cursor_column(self._conn)

    def warm_state(self) -> Dict[str, Any]:
        return warm_state_row(self._conn, CORPUS_ID)

    def upsert_rows(self, rows: Sequence[Dict[str, Any]]) -> int:
        inserted = 0
        for row in rows:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_species_properties
                (species_iri, property_local, property_value)
                VALUES (?, ?, ?)
                """,
                (row["species_iri"], row["property_local"], row["property_value"]),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def warm_batch(
        self,
        *,
        offset: Optional[int] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        total_species: Optional[int] = None,
    ) -> PhyspropsWarmBatchResult:
        state = self.warm_state()
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(total_species or state.get("total_species") or 36631)
        if state.get("status") != "running" or state.get("total_species") is None:
            ensure_warm_state(self._conn, corpus_id=CORPUS_ID, total=total, batch_size=batch_size)

        cursor = resolve_cursor(
            self._conn,
            corpus_id=CORPUS_ID,
            offset=off,
            bootstrap_fn=bootstrap_cursor_from_offset,
        )
        started = time.perf_counter()
        rows, page_iris = fetch_physprops_rows_with_retry(limit=batch_size, after_subject=cursor)
        new_cursor = page_iris[-1] if page_iris else cursor
        inserted = self.upsert_rows(rows)
        done = not page_iris or off + len(page_iris) >= total
        advance_warm_state(
            self._conn,
            corpus_id=CORPUS_ID,
            offset=off,
            species_added=len(page_iris),
            rows_added=inserted,
            batch_size=batch_size,
            total=total,
            done=done,
            cursor_subject=new_cursor,
        )
        return PhyspropsWarmBatchResult(
            offset=off,
            batch_size=batch_size,
            species_in_batch=len(page_iris),
            prop_rows=inserted,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            done=done,
            cursor_subject=new_cursor,
        )

    def query_properties(
        self,
        *,
        species_iri: str = "",
        property_local: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if species_iri.strip():
            clauses.append("species_iri = ?")
            params.append(species_iri.strip())
        if property_local.strip():
            clauses.append("property_local = ?")
            params.append(property_local.strip())
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT species_iri, property_local, property_value
            FROM corpus_species_properties
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def query_properties_tsv(self, **kwargs: Any) -> str:
        rows = self.query_properties(**kwargs)
        return format_tsv(rows) if rows else "No results"

    def stats(self) -> Dict[str, Any]:
        n = self._conn.execute("SELECT COUNT(*) FROM corpus_species_properties").fetchone()[0]
        by_prop = [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT property_local, COUNT(*) AS n
                FROM corpus_species_properties GROUP BY property_local ORDER BY n DESC
                """
            )
        ]
        return {
            "corpus_id": CORPUS_ID,
            "property_rows": int(n),
            "by_property": by_prop,
            "warm_state": self.warm_state(),
            "endpoint": endpoint("ontospecies"),
            "sparql_timeout_s": sparql_timeout("ontospecies"),
        }


def warm_physprops_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    show_progress: bool = True,
    skip_health_check: bool = False,
) -> Dict[str, Any]:
    store = SpeciesPhyspropsCorpusStore()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    total = 36631
    try:
        ensure_warm_state(store._conn, corpus_id=CORPUS_ID, total=total, batch_size=batch_size)
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        use_bar = show_progress and tqdm is not None and max_batches > 1
        batch_range = range(max_batches)
        iterator = tqdm(batch_range, desc="physprops warm", unit="batch") if use_bar else batch_range
        for i in iterator:
            state = store.warm_state()
            if state.get("status") == "complete":
                break
            off = int(state.get("offset_next") or 0)
            if off >= total:
                break
            if not skip_health_check:
                health = namespace_health_ok("ontospecies")
                if not health.get("ok"):
                    mark_paused(store._conn, CORPUS_ID, f"health check failed: {health}")
                    errors.append({"phase": "health", "offset": off, **health})
                    break
            try:
                batch = store.warm_batch(offset=off, batch_size=batch_size, total_species=total)
            except Exception as exc:
                mark_paused(store._conn, CORPUS_ID, str(exc))
                errors.append({"phase": "fetch", "offset": off, "error": str(exc)})
                break
            results.append(batch.__dict__)
            if batch.done:
                break
            if i + 1 < max_batches and delay_seconds > 0:
                time.sleep(delay_seconds)
        stats = store.stats()
        paused = bool(errors) or stats.get("warm_state", {}).get("status") == "paused"
        return {
            "batches": results,
            "stats": stats,
            "errors": errors,
            "paused": paused,
            "resume_hint": RESUME_HINT if paused else "",
        }
    finally:
        store.close()
