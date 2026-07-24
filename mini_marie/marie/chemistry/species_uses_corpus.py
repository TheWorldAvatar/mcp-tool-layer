"""OntoSpecies uses corpus — hasUse literals per species."""

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
from mini_marie.marie.chemistry.species_corpus import bootstrap_cursor_from_offset, count_species_remote, species_iri_page_query

CORPUS_ID = "ontospecies_uses"
DEFAULT_BATCH_SIZE = 50
RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_species_uses_corpus --max-batches 0 --delay 3"


def uses_for_iris_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?species ?useValue WHERE {{
  VALUES ?species {{ {values} }}
  ?species os:hasUse ?useNode .
  ?useNode rdfs:label ?useValue .
  FILTER(isLiteral(?useValue))
}}
"""
    )


def fetch_uses_rows(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    iri_rows = _execute("ontospecies", species_iri_page_query(limit=limit, after_subject=after_subject))
    page_iris = [str(r.get("subject", "")).strip() for r in iri_rows if str(r.get("subject", "")).strip()]
    if not page_iris:
        return [], []
    raw = _execute("ontospecies", uses_for_iris_query(page_iris))
    out: List[Dict[str, Any]] = []
    for row in raw:
        species = str(row.get("species", "")).strip()
        use_val = str(row.get("useValue", "")).strip()
        if not species or not use_val:
            continue
        out.append(
            {
                "species_iri": species,
                "use_value": use_val,
                "use_value_lc": use_val.lower(),
            }
        )
    return out, page_iris


def fetch_uses_rows_with_retry(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    return retry_corpus_fetch(
        "species uses",
        lambda: fetch_uses_rows(limit=limit, after_subject=after_subject),
    )


def count_species_with_uses_remote() -> int:
    q = (
        _prefix_block("ontospecies")
        + """
SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {
  ?s a os:Species .
  ?s os:hasUse ?u .
}
"""
    )
    rows = _execute("ontospecies", q)
    try:
        return int(str(rows[0].get("n", "0")))
    except (IndexError, ValueError):
        return 36631


@dataclass
class UsesWarmBatchResult:
    offset: int
    batch_size: int
    species_in_batch: int
    use_rows: int
    elapsed_ms: int
    done: bool
    cursor_subject: str = ""


class SpeciesUsesCorpusStore:
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
            CREATE TABLE IF NOT EXISTS corpus_species_uses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                species_iri TEXT NOT NULL,
                use_value TEXT NOT NULL,
                use_value_lc TEXT NOT NULL,
                UNIQUE(species_iri, use_value)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_uses_lc ON corpus_species_uses(use_value_lc);
            CREATE INDEX IF NOT EXISTS idx_corpus_uses_species ON corpus_species_uses(species_iri);
            CREATE TABLE IF NOT EXISTS corpus_warm_state (
                corpus_id TEXT PRIMARY KEY,
                total_species INTEGER,
                offset_next INTEGER NOT NULL DEFAULT 0,
                batch_size INTEGER NOT NULL DEFAULT 50,
                species_indexed INTEGER NOT NULL DEFAULT 0,
                name_rows_indexed INTEGER NOT NULL DEFAULT 0,
                batches_done INTEGER NOT NULL DEFAULT 0,
                status TEXT NOT NULL DEFAULT 'idle',
                updated_at REAL NOT NULL,
                error TEXT,
                cursor_subject TEXT NOT NULL DEFAULT ''
            );
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
                INSERT OR IGNORE INTO corpus_species_uses
                (species_iri, use_value, use_value_lc)
                VALUES (?, ?, ?)
                """,
                (row["species_iri"], row["use_value"], row["use_value_lc"]),
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
    ) -> UsesWarmBatchResult:
        state = self.warm_state()
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(total_species or state.get("total_species") or count_species_remote())
        if state.get("status") != "running" or state.get("total_species") is None:
            ensure_warm_state(self._conn, corpus_id=CORPUS_ID, total=total, batch_size=batch_size)

        cursor = resolve_cursor(
            self._conn,
            corpus_id=CORPUS_ID,
            offset=off,
            bootstrap_fn=bootstrap_cursor_from_offset,
        )
        started = time.perf_counter()
        rows, page_iris = fetch_uses_rows_with_retry(limit=batch_size, after_subject=cursor)
        species_with_uses = {r["species_iri"] for r in rows}
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
        return UsesWarmBatchResult(
            offset=off,
            batch_size=batch_size,
            species_in_batch=len(species_with_uses) or len(page_iris),
            use_rows=inserted,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            done=done,
            cursor_subject=new_cursor,
        )

    def search_uses(
        self,
        query: str,
        *,
        limit: int = 50,
        species_iri: str = "",
    ) -> List[Dict[str, Any]]:
        q = query.strip()
        clauses = ["1=1"]
        params: List[Any] = []
        if species_iri.strip():
            clauses.append("species_iri = ?")
            params.append(species_iri.strip())
        if q:
            clauses.append("use_value_lc LIKE ?")
            params.append(f"%{q.lower()}%")
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT species_iri, use_value
            FROM corpus_species_uses
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def search_uses_tsv(self, query: str, **kwargs: Any) -> str:
        rows = self.search_uses(query, **kwargs)
        return format_tsv(rows) if rows else "No results"

    def stats(self) -> Dict[str, Any]:
        n = self._conn.execute("SELECT COUNT(*) FROM corpus_species_uses").fetchone()[0]
        species_n = self._conn.execute(
            "SELECT COUNT(DISTINCT species_iri) FROM corpus_species_uses"
        ).fetchone()[0]
        return {
            "corpus_id": CORPUS_ID,
            "use_rows": int(n),
            "species_with_uses": int(species_n),
            "warm_state": self.warm_state(),
            "endpoint": endpoint("ontospecies"),
            "sparql_timeout_s": sparql_timeout("ontospecies"),
        }


def warm_uses_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    show_progress: bool = True,
    skip_health_check: bool = False,
) -> Dict[str, Any]:
    store = SpeciesUsesCorpusStore()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        total = count_species_remote()
        ensure_warm_state(store._conn, corpus_id=CORPUS_ID, total=total, batch_size=batch_size)
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        use_bar = show_progress and tqdm is not None and max_batches > 1
        batch_range = range(max_batches)
        iterator = tqdm(batch_range, desc="uses corpus warm", unit="batch") if use_bar else batch_range
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
