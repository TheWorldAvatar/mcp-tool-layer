"""
OntoSpecies pKa measurement corpus — traverse pattern (not name search).

Materializes hasDissociationConstants + metadata for offline MQ4–MQ19 analytics.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.corpus_fetch import retry_corpus_fetch
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS, sparql_timeout
from mini_marie.marie.chemistry.query_builder import PKA_METADATA_PROPERTIES, _execute, _prefix_block
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import format_tsv

CORPUS_ID = "ontospecies_pka"
DEFAULT_BATCH_SIZE = 50
RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_species_pka_corpus --max-batches 0 --delay 3"


def _esc_iri(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def pka_species_iri_at_index_query(*, index: int) -> str:
    idx = max(0, int(index))
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?species WHERE {{
  ?species a os:Species .
  ?species os:hasDissociationConstants ?pka .
}}
ORDER BY ?species
LIMIT 1 OFFSET {idx}
"""
    )


def pka_species_iri_page_query(*, limit: int, after_subject: str = "") -> str:
    lim = max(1, int(limit))
    cursor_filter = ""
    if after_subject.strip():
        cursor_filter = f'  FILTER(STR(?species) > "{_esc_iri(after_subject.strip())}")\n'
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?species WHERE {{
  ?species a os:Species .
  ?species os:hasDissociationConstants ?pka .
{cursor_filter}}}
ORDER BY ?species
LIMIT {lim}
"""
    )


def pka_rows_for_species_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    optional_lines = []
    bind_map = {
        "hasTemperature": "temp",
        "hasIonicStrength": "ionic_strength",
        "hasMeasurementMethod": "method",
        "hasReliabilityAssessment": "reliability",
        "hasAcidityLabel": "acidity_label",
    }
    for prop in PKA_METADATA_PROPERTIES:
        if prop == "hasProvenance":
            optional_lines.append(
                "  OPTIONAL { ?pka os:hasProvenance ?provNode . BIND(STR(?provNode) AS ?provenance) }"
            )
            continue
        var = bind_map.get(prop)
        if not var:
            continue
        optional_lines.append(
            f"  OPTIONAL {{ ?pka os:{prop} ?{var}Node . ?{var}Node os:value ?{var} . }}"
        )
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?species ?pka ?pka_value ?temp ?ionic_strength ?method ?reliability ?acidity_label ?provenance WHERE {{
  VALUES ?species {{ {values} }}
  ?species os:hasDissociationConstants ?pka .
  ?pka os:value ?pka_value .
"""
        + "\n".join(optional_lines)
        + "\n}\n"
    )


def bootstrap_pka_cursor_from_offset(offset: int) -> str:
    if offset < 0:
        return ""
    rows = _execute("ontospecies", pka_species_iri_at_index_query(index=offset))
    if not rows:
        return ""
    return str(rows[0].get("species", "")).strip()


def _parse_pka_rows(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in raw_rows:
        species = str(row.get("species", "")).strip()
        if not species:
            continue
        out.append(
            {
                "species_iri": species,
                "pka_iri": str(row.get("pka", "")).strip(),
                "pka_value": str(row.get("pka_value", "")).strip(),
                "temperature": str(row.get("temp", "") or "").strip(),
                "ionic_strength": str(row.get("ionic_strength", "") or "").strip(),
                "method": str(row.get("method", "") or "").strip(),
                "reliability": str(row.get("reliability", "") or "").strip(),
                "acidity_label": str(row.get("acidity_label", "") or "").strip(),
                "provenance": str(row.get("provenance", "") or "").strip(),
            }
        )
    return out


def fetch_pka_rows(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    iri_rows = _execute(
        "ontospecies",
        pka_species_iri_page_query(limit=limit, after_subject=after_subject),
    )
    page_iris = [str(r.get("species", "")).strip() for r in iri_rows]
    page_iris = [iri for iri in page_iris if iri]
    if not page_iris:
        return [], []
    rows = _execute("ontospecies", pka_rows_for_species_query(page_iris))
    return _parse_pka_rows(rows), page_iris


def fetch_pka_rows_with_retry(
    *,
    limit: int,
    after_subject: str = "",
) -> tuple[List[Dict[str, Any]], List[str]]:
    return retry_corpus_fetch(
        "species pKa",
        lambda: fetch_pka_rows(limit=limit, after_subject=after_subject),
    )


def count_species_with_pka_remote() -> int:
    q = (
        _prefix_block("ontospecies")
        + """
SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {
  ?s a os:Species .
  ?s os:hasDissociationConstants ?pka .
}
"""
    )
    rows = _execute("ontospecies", q)
    try:
        return int(str(rows[0].get("n", "0")))
    except (IndexError, ValueError):
        return 0


@dataclass
class PkaWarmBatchResult:
    offset: int
    batch_size: int
    species_in_batch: int
    pka_rows: int
    elapsed_ms: int
    done: bool
    cursor_subject: str = ""


class SpeciesPkaCorpusStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = str(path or db_path())
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        self._migrate_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS corpus_species_pka (
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
            """
        )
        self._conn.commit()

    def _migrate_schema(self) -> None:
        tables = {
            row[0]
            for row in self._conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
        if "corpus_warm_state" not in tables:
            return
        cols = {row[1] for row in self._conn.execute("PRAGMA table_info(corpus_warm_state)")}
        if "cursor_subject" not in cols:
            self._conn.execute(
                "ALTER TABLE corpus_warm_state ADD COLUMN cursor_subject TEXT NOT NULL DEFAULT ''"
            )
            self._conn.commit()

    def warm_state(self) -> Dict[str, Any]:
        cur = self._conn.execute(
            "SELECT * FROM corpus_warm_state WHERE corpus_id = ?", (CORPUS_ID,)
        )
        row = cur.fetchone()
        return dict(row) if row else {"corpus_id": CORPUS_ID, "offset_next": 0, "status": "idle"}

    def _ensure_state(self, *, total: int, batch_size: int) -> None:
        self._conn.execute(
            """
            INSERT INTO corpus_warm_state
            (corpus_id, total_species, offset_next, batch_size, species_indexed,
             name_rows_indexed, batches_done, status, updated_at)
            VALUES (?, ?, 0, ?, 0, 0, 0, 'running', ?)
            ON CONFLICT(corpus_id) DO UPDATE SET
              total_species=excluded.total_species,
              batch_size=excluded.batch_size,
              status='running',
              updated_at=excluded.updated_at
            """,
            (CORPUS_ID, total, batch_size, time.time()),
        )
        self._conn.commit()

    def mark_paused(self, error: str) -> None:
        self._conn.execute(
            """
            UPDATE corpus_warm_state SET status='paused', error=?, updated_at=?
            WHERE corpus_id = ?
            """,
            (error[:500], time.time(), CORPUS_ID),
        )
        self._conn.commit()

    def upsert_rows(self, rows: Sequence[Dict[str, Any]]) -> int:
        inserted = 0
        for row in rows:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_species_pka
                (species_iri, pka_iri, pka_value, temperature, ionic_strength,
                 method, reliability, acidity_label, provenance)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["species_iri"],
                    row.get("pka_iri") or "",
                    row["pka_value"],
                    row.get("temperature") or "",
                    row.get("ionic_strength") or "",
                    row.get("method") or "",
                    row.get("reliability") or "",
                    row.get("acidity_label") or "",
                    row.get("provenance") or "",
                ),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def advance_state(
        self,
        *,
        offset: int,
        batch_size: int,
        species_added: int,
        rows_added: int,
        total: int,
        done: bool,
        cursor_subject: str = "",
    ) -> None:
        state = self.warm_state()
        batches = int(state.get("batches_done") or 0) + 1
        self._conn.execute(
            """
            UPDATE corpus_warm_state SET
              offset_next = ?,
              cursor_subject = ?,
              batch_size = ?,
              species_indexed = species_indexed + ?,
              name_rows_indexed = name_rows_indexed + ?,
              batches_done = ?,
              total_species = ?,
              status = ?,
              updated_at = ?
            WHERE corpus_id = ?
            """,
            (
                offset + species_added,
                cursor_subject,
                batch_size,
                species_added,
                rows_added,
                batches,
                total,
                "complete" if done else "running",
                time.time(),
                CORPUS_ID,
            ),
        )
        self._conn.commit()

    def _resolve_cursor(self, state: Dict[str, Any], offset: int) -> str:
        cursor = str(state.get("cursor_subject") or "").strip()
        if cursor or offset <= 0:
            return cursor
        return bootstrap_pka_cursor_from_offset(offset - 1)

    def warm_batch(
        self,
        *,
        offset: Optional[int] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        total_species: Optional[int] = None,
    ) -> PkaWarmBatchResult:
        state = self.warm_state()
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(total_species or state.get("total_species") or count_species_with_pka_remote())
        if state.get("status") != "running" or state.get("total_species") is None:
            self._ensure_state(total=total, batch_size=batch_size)

        cursor = self._resolve_cursor(state, off)
        started = time.perf_counter()
        rows, page_iris = fetch_pka_rows_with_retry(limit=batch_size, after_subject=cursor)
        species_iris = set(page_iris) or {r["species_iri"] for r in rows}
        new_cursor = page_iris[-1] if page_iris else cursor
        inserted = self.upsert_rows(rows)
        new_offset = off + len(species_iris)
        done = new_offset >= total
        if not page_iris and not rows and new_offset < total:
            done = False
        self.advance_state(
            offset=off,
            batch_size=batch_size,
            species_added=len(species_iris),
            rows_added=inserted,
            total=total,
            done=done,
            cursor_subject=new_cursor,
        )
        return PkaWarmBatchResult(
            offset=off,
            batch_size=batch_size,
            species_in_batch=len(species_iris),
            pka_rows=inserted,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            done=done,
            cursor_subject=new_cursor,
        )

    def stats(self) -> Dict[str, Any]:
        n = self._conn.execute("SELECT COUNT(*) FROM corpus_species_pka").fetchone()[0]
        species_n = self._conn.execute(
            "SELECT COUNT(DISTINCT species_iri) FROM corpus_species_pka"
        ).fetchone()[0]
        return {
            "corpus_id": CORPUS_ID,
            "pka_rows": int(n),
            "species_with_pka": int(species_n),
            "warm_state": self.warm_state(),
            "endpoint": endpoint("ontospecies"),
            "sparql_timeout_s": sparql_timeout("ontospecies"),
        }

    def query_pka(
        self,
        *,
        species_iri: str = "",
        reliability: str = "",
        method: str = "",
        acidity_label: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if species_iri.strip():
            clauses.append("species_iri = ?")
            params.append(species_iri.strip())
        if reliability.strip():
            clauses.append("reliability LIKE ?")
            params.append(f"%{reliability.strip()}%")
        if method.strip():
            clauses.append("method LIKE ?")
            params.append(f"%{method.strip()}%")
        if acidity_label.strip():
            clauses.append("acidity_label LIKE ?")
            params.append(f"%{acidity_label.strip()}%")
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT species_iri, pka_value, temperature, ionic_strength, method,
                   reliability, acidity_label, provenance
            FROM corpus_species_pka
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def query_pka_tsv(self, **kwargs: Any) -> str:
        rows = self.query_pka(**kwargs)
        return format_tsv(rows) if rows else "No results"


def refresh_provenance_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 0,
    delay_seconds: float = WARM_DELAY_SECONDS,
) -> Dict[str, Any]:
    """Re-fetch pKa rows to populate provenance IRIs (metadata backfill)."""
    store = SpeciesPkaCorpusStore()
    batches: List[Dict[str, Any]] = []
    try:
        max_n = max_batches if max_batches > 0 else 10_000
        cursor = ""
        for i in range(max_n):
            rows, page_iris = fetch_pka_rows_with_retry(limit=batch_size, after_subject=cursor)
            updated = 0
            for row in rows:
                prov = row.get("provenance") or ""
                if not prov:
                    continue
                cur = store._conn.execute(
                    """
                    UPDATE corpus_species_pka SET provenance = ?
                    WHERE species_iri = ? AND pka_iri = ? AND pka_value = ?
                    """,
                    (prov, row["species_iri"], row.get("pka_iri") or "", row["pka_value"]),
                )
                updated += cur.rowcount
            store._conn.commit()
            cursor = page_iris[-1] if page_iris else cursor
            batches.append({"batch": i + 1, "species": len(page_iris), "updated": updated, "cursor": cursor})
            if not page_iris:
                break
            if i + 1 < max_n and delay_seconds > 0:
                time.sleep(delay_seconds)
        with_prov = store._conn.execute(
            "SELECT COUNT(*) FROM corpus_species_pka WHERE provenance != ''"
        ).fetchone()[0]
        return {"batches": batches, "rows_with_provenance": int(with_prov), "stats": store.stats()}
    finally:
        store.close()


def warm_pka_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    show_progress: bool = True,
    skip_health_check: bool = False,
) -> Dict[str, Any]:
    store = SpeciesPkaCorpusStore()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        total = count_species_with_pka_remote()
        store._ensure_state(total=total, batch_size=batch_size)
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        use_bar = show_progress and tqdm is not None and max_batches > 1
        batch_range = range(max_batches)
        iterator = tqdm(batch_range, desc="pKa corpus warm", unit="batch") if use_bar else batch_range
        for i in iterator:
            state = store.warm_state()
            indexed = int(state.get("species_indexed") or 0)
            if state.get("status") == "complete" and indexed >= total:
                break
            if state.get("status") == "complete" and indexed < total:
                store._conn.execute(
                    "UPDATE corpus_warm_state SET status='running', updated_at=? WHERE corpus_id=?",
                    (time.time(), CORPUS_ID),
                )
                store._conn.commit()
            off = int(state.get("offset_next") or 0)
            if off >= total and indexed >= total:
                break

            if not skip_health_check:
                health = namespace_health_ok("ontospecies")
                if not health.get("ok"):
                    msg = f"health check failed: {health}"
                    store.mark_paused(msg)
                    errors.append({"phase": "health", "offset": off, **health})
                    break

            try:
                batch = store.warm_batch(offset=off, batch_size=batch_size, total_species=total)
            except Exception as exc:
                store.mark_paused(str(exc))
                errors.append(
                    {
                        "phase": "fetch",
                        "offset": off,
                        "cursor_subject": state.get("cursor_subject") or "",
                        "error": str(exc),
                    }
                )
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
