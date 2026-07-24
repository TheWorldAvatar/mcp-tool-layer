"""
OntoSpecies corpus cache: all species + searchable names (labels, IUPAC, SMILES, …).

Paginated warm from fragile Blazegraph; local substring + fuzzy search offline.
"""

from __future__ import annotations

import re
import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.corpus_fetch import retry_corpus_fetch
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS, sparql_timeout
from mini_marie.marie.chemistry.query_builder import _execute, _prefix_block
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import format_tsv

CORPUS_ID = "ontospecies_species"
DEFAULT_BATCH_SIZE = 50
HTTP_IRI_PREFIX = re.compile(r"^https?://", re.I)
HTML_TAG = re.compile(r"<[^>]+>")


def _clean_name(value: str) -> str:
    text = HTML_TAG.sub("", value).strip()
    return text


def _is_searchable_literal(value: str) -> bool:
    if not value or not value.strip():
        return False
    if HTTP_IRI_PREFIX.match(value.strip()):
        return False
    return True


def species_count_query() -> str:
    return (
        _prefix_block("ontospecies")
        + """
SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE {
  ?s a os:Species .
}
"""
    )


def _esc_iri(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


_SPECIES_NAME_UNIONS = """
  {
    ?subject rdfs:label ?nameValue .
    BIND("label" AS ?nameType)
  } UNION {
    ?subject os:hasMolecularFormula ?n .
    ?n os:value ?nameValue .
    BIND("formula" AS ?nameType)
  } UNION {
    ?subject os:hasSMILES ?n .
    ?n os:value ?nameValue .
    BIND("smiles" AS ?nameType)
  } UNION {
    ?subject os:hasInChI ?n .
    ?n os:value ?nameValue .
    BIND("inchi" AS ?nameType)
  } UNION {
    ?subject os:hasInChIKey ?n .
    ?n os:value ?nameValue .
    BIND("inchikey" AS ?nameType)
  } UNION {
    ?subject os:hasIUPACName ?iup .
    ?iup os:value ?nameValue .
    BIND("iupac" AS ?nameType)
  } UNION {
    ?subject os:hasCID ?n .
    ?n os:value ?nameValue .
    BIND("cid" AS ?nameType)
  }
"""


def species_iri_at_index_query(*, index: int) -> str:
    idx = max(0, int(index))
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?subject WHERE {{
  ?subject a os:Species .
}}
ORDER BY ?subject
LIMIT 1 OFFSET {idx}
"""
    )


def species_iri_page_query(*, limit: int, after_subject: str = "") -> str:
    lim = max(1, int(limit))
    cursor_filter = ""
    if after_subject.strip():
        cursor_filter = f'  FILTER(STR(?subject) > "{_esc_iri(after_subject.strip())}")\n'
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?subject WHERE {{
  ?subject a os:Species .
{cursor_filter}}}
ORDER BY ?subject
LIMIT {lim}
"""
    )


def species_names_for_iris_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontospecies")
        + f"""
SELECT ?subject ?nameType ?nameValue WHERE {{
  VALUES ?subject {{ {values} }}
"""
        + _SPECIES_NAME_UNIONS
        + """
  FILTER(isLiteral(?nameValue))
}
"""
    )


def bootstrap_cursor_from_offset(offset: int) -> str:
    """Return species IRI at 0-based index (last fully indexed row)."""
    if offset < 0:
        return ""
    rows = _execute("ontospecies", species_iri_at_index_query(index=offset))
    if not rows:
        return ""
    return str(rows[0].get("subject", "")).strip()


def _parse_species_name_rows(raw_rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    for row in raw_rows:
        subject = str(row.get("subject", "")).strip()
        name_type = str(row.get("nameType", "")).strip()
        raw = _clean_name(str(row.get("nameValue", "")))
        if not subject or not name_type or not _is_searchable_literal(raw):
            continue
        out.append(
            {
                "species_iri": subject,
                "name_type": name_type,
                "name_value": raw,
                "name_value_lc": raw.lower(),
            }
        )
    return out


def fetch_species_name_rows(
    *,
    limit: int,
    after_subject: str = "",
) -> tuple[List[Dict[str, Any]], List[str]]:
    """Cursor-paginated fetch: page species IRIs, then VALUES lookup for names."""
    iri_rows = _execute(
        "ontospecies",
        species_iri_page_query(limit=limit, after_subject=after_subject),
    )
    page_iris = [str(r.get("subject", "")).strip() for r in iri_rows]
    page_iris = [iri for iri in page_iris if iri]
    if not page_iris:
        return [], []
    name_rows = _execute("ontospecies", species_names_for_iris_query(page_iris))
    return _parse_species_name_rows(name_rows), page_iris


def fetch_species_name_rows_with_retry(
    *,
    limit: int,
    after_subject: str = "",
) -> tuple[List[Dict[str, Any]], List[str]]:
    return retry_corpus_fetch(
        "species names",
        lambda: fetch_species_name_rows(limit=limit, after_subject=after_subject),
    )


def count_species_remote() -> int:
    rows = _execute("ontospecies", species_count_query())
    if not rows:
        return 0
    try:
        return int(str(rows[0].get("n", "0")))
    except ValueError:
        return 0


@dataclass
class WarmBatchResult:
    offset: int
    batch_size: int
    species_in_batch: int
    name_rows: int
    elapsed_ms: int
    done: bool
    cursor_subject: str = ""


RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_species_corpus --max-batches 0 --delay 3"


class SpeciesCorpusStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = str(path or db_path())
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
            self._conn.execute("PRAGMA synchronous=NORMAL")
        except sqlite3.OperationalError:
            pass
        self._init_schema()
        self._migrate_schema()

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS corpus_species (
                species_iri TEXT PRIMARY KEY,
                primary_label TEXT,
                indexed_at REAL NOT NULL
            );
            CREATE TABLE IF NOT EXISTS corpus_species_names (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                species_iri TEXT NOT NULL,
                name_type TEXT NOT NULL,
                name_value TEXT NOT NULL,
                name_value_lc TEXT NOT NULL,
                UNIQUE(species_iri, name_type, name_value)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_names_lc
                ON corpus_species_names(name_value_lc);
            CREATE INDEX IF NOT EXISTS idx_corpus_names_species
                ON corpus_species_names(species_iri);

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
                error TEXT
            );
            """
        )
        self._conn.commit()

    def _migrate_schema(self) -> None:
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
        if row is None:
            return {
                "corpus_id": CORPUS_ID,
                "total_species": None,
                "offset_next": 0,
                "cursor_subject": "",
                "species_indexed": 0,
                "name_rows_indexed": 0,
                "batches_done": 0,
                "status": "idle",
            }
        return dict(row)

    def _ensure_state(self, *, total_species: int, batch_size: int) -> None:
        now = time.time()
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
            (CORPUS_ID, total_species, batch_size, now),
        )
        self._conn.commit()

    def mark_paused(self, error: str) -> None:
        self._conn.execute(
            """
            UPDATE corpus_warm_state SET
              status = 'paused',
              error = ?,
              updated_at = ?
            WHERE corpus_id = ?
            """,
            (error[:500], time.time(), CORPUS_ID),
        )
        self._conn.commit()

    def upsert_name_rows(self, rows: Sequence[Dict[str, Any]]) -> int:
        if not rows:
            return 0
        now = time.time()
        species_labels: Dict[str, str] = {}
        inserted = 0
        for row in rows:
            iri = row["species_iri"]
            if row["name_type"] == "label" and iri not in species_labels:
                species_labels[iri] = row["name_value"]
        for iri, label in species_labels.items():
            self._conn.execute(
                """
                INSERT INTO corpus_species (species_iri, primary_label, indexed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(species_iri) DO UPDATE SET
                  primary_label=COALESCE(excluded.primary_label, corpus_species.primary_label),
                  indexed_at=excluded.indexed_at
                """,
                (iri, label, now),
            )
        for row in rows:
            iri = row["species_iri"]
            if iri not in species_labels:
                self._conn.execute(
                    """
                    INSERT OR IGNORE INTO corpus_species (species_iri, primary_label, indexed_at)
                    VALUES (?, ?, ?)
                    """,
                    (iri, None, now),
                )
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_species_names
                (species_iri, name_type, name_value, name_value_lc)
                VALUES (?, ?, ?, ?)
                """,
                (iri, row["name_type"], row["name_value"], row["name_value_lc"]),
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
        name_rows_added: int,
        total_species: int,
        done: bool,
        cursor_subject: str = "",
        error: str = "",
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
              updated_at = ?,
              error = ?
            WHERE corpus_id = ?
            """,
            (
                offset + species_added,
                cursor_subject,
                batch_size,
                species_added,
                name_rows_added,
                batches,
                total_species,
                "complete" if done else "running",
                time.time(),
                error[:500],
                CORPUS_ID,
            ),
        )
        self._conn.commit()

    def _resolve_cursor(self, state: Dict[str, Any], offset: int) -> str:
        cursor = str(state.get("cursor_subject") or "").strip()
        if cursor or offset <= 0:
            return cursor
        return bootstrap_cursor_from_offset(offset - 1)

    def warm_batch(
        self,
        *,
        offset: Optional[int] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        total_species: Optional[int] = None,
    ) -> WarmBatchResult:
        state = self.warm_state()
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(total_species or state.get("total_species") or count_species_remote())
        if state.get("status") != "running" or state.get("total_species") is None:
            self._ensure_state(total_species=total, batch_size=batch_size)

        cursor = self._resolve_cursor(state, off)
        started = time.perf_counter()
        rows, page_iris = fetch_species_name_rows_with_retry(
            limit=batch_size,
            after_subject=cursor,
        )
        species_iris = set(page_iris) or {r["species_iri"] for r in rows}
        new_cursor = page_iris[-1] if page_iris else cursor
        inserted = self.upsert_name_rows(rows)
        done = not page_iris or off + len(page_iris) >= total
        self.advance_state(
            offset=off,
            batch_size=batch_size,
            species_added=len(species_iris),
            name_rows_added=inserted,
            total_species=total,
            done=done,
            cursor_subject=new_cursor,
        )
        return WarmBatchResult(
            offset=off,
            batch_size=batch_size,
            species_in_batch=len(species_iris),
            name_rows=inserted,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            done=done,
            cursor_subject=new_cursor,
        )

    def stats(self) -> Dict[str, Any]:
        species_n = self._conn.execute("SELECT COUNT(*) FROM corpus_species").fetchone()[0]
        names_n = self._conn.execute("SELECT COUNT(*) FROM corpus_species_names").fetchone()[0]
        by_type = [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT name_type, COUNT(*) AS n
                FROM corpus_species_names GROUP BY name_type ORDER BY n DESC
                """
            )
        ]
        return {
            "db_path": self.path,
            "corpus_id": CORPUS_ID,
            "species_rows": int(species_n),
            "name_rows": int(names_n),
            "by_name_type": by_type,
            "warm_state": self.warm_state(),
            "endpoint": endpoint("ontospecies"),
            "sparql_timeout_s": sparql_timeout("ontospecies"),
        }

    def search_names(
        self,
        query: str,
        *,
        limit: int = 20,
        fuzzy: bool = False,
        min_score: int = 70,
    ) -> List[Dict[str, Any]]:
        q = query.strip()
        if not q:
            return []
        ql = q.lower()
        cap = max(limit * 10, 100)

        cur = self._conn.execute(
            """
            SELECT n.species_iri, n.name_type, n.name_value, s.primary_label
            FROM corpus_species_names n
            LEFT JOIN corpus_species s ON s.species_iri = n.species_iri
            WHERE n.name_value_lc LIKE ?
            ORDER BY LENGTH(n.name_value), n.name_value
            LIMIT ?
            """,
            (f"%{ql}%", cap),
        )
        candidates = [dict(r) for r in cur]

        scored: List[Dict[str, Any]] = []
        if fuzzy:
            try:
                from fuzzywuzzy import fuzz
            except ImportError:
                fuzzy = False

        for c in candidates:
            if fuzzy:
                score = fuzz.WRatio(q, c["name_value"])
                if score < min_score and ql not in c["name_value"].lower():
                    continue
                scored.append({**c, "match_score": int(score)})
            elif ql in c["name_value"].lower():
                scored.append({**c, "match_score": 100})

        if not scored and not fuzzy:
            scored = [{**c, "match_score": 80} for c in candidates]

        best: Dict[str, Dict[str, Any]] = {}
        for c in scored:
            iri = c["species_iri"]
            prev = best.get(iri)
            if prev is None or int(c["match_score"]) > int(prev["match_score"]):
                best[iri] = c
        ranked = sorted(
            best.values(),
            key=lambda x: (-int(x.get("match_score", 0)), len(str(x.get("name_value", "")))),
        )
        return ranked[:limit]

    def search_names_tsv(
        self,
        query: str,
        *,
        limit: int = 20,
        fuzzy: bool = False,
        min_score: int = 70,
    ) -> str:
        rows = self.search_names(query, limit=limit, fuzzy=fuzzy, min_score=min_score)
        if not rows:
            return "No results"
        out = []
        for r in rows:
            out.append(
                {
                    "subject": r["species_iri"],
                    "label": r.get("primary_label") or r.get("name_value", ""),
                    "matched_name": r.get("name_value", ""),
                    "name_type": r.get("name_type", ""),
                    "match_score": r.get("match_score", ""),
                }
            )
        return format_tsv(out)


def warm_species_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    offset: Optional[int] = None,
    show_progress: bool = True,
    skip_health_check: bool = False,
) -> Dict[str, Any]:
    store = SpeciesCorpusStore()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        total = count_species_remote()
        store._ensure_state(total_species=total, batch_size=batch_size)
        start_off = offset if offset is not None else int(store.warm_state().get("offset_next") or 0)

        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None

        use_bar = show_progress and tqdm is not None and max_batches > 1
        batch_range = range(max_batches)
        iterator = tqdm(batch_range, desc="Species corpus warm", unit="batch") if use_bar else batch_range

        for i in iterator:
            state = store.warm_state()
            if state.get("status") == "complete" and offset is None:
                break
            off = start_off if i == 0 and offset is not None else int(store.warm_state().get("offset_next") or 0)
            if off >= total:
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

            item = {
                "offset": batch.offset,
                "cursor_subject": batch.cursor_subject,
                "species_in_batch": batch.species_in_batch,
                "name_rows_inserted": batch.name_rows,
                "elapsed_ms": batch.elapsed_ms,
                "done": batch.done,
            }
            results.append(item)
            if use_bar:
                iterator.set_postfix(
                    offset=batch.offset,
                    species=batch.species_in_batch,
                    names=batch.name_rows,
                    refresh=False,
                )
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
