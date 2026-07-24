"""OntoKin mechanism resolve + reaction graph corpus."""

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

MECH_CORPUS_ID = "ontokin_mechanisms"
GRAPH_CORPUS_ID = "ontokin_reaction_graph"
DEFAULT_BATCH_SIZE = 20
RESUME_MECH = "python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet mechanisms --max-batches 0"
RESUME_GRAPH = "python -m mini_marie.marie.chemistry.warm_ontokin_corpus --facet graph --max-batches 0"


def _esc_iri(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def mechanism_page_query(*, limit: int, after_subject: str = "") -> str:
    lim = max(1, int(limit))
    cursor_filter = ""
    if after_subject.strip():
        cursor_filter = f'  FILTER(STR(?mechanism) > "{_esc_iri(after_subject.strip())}")\n'
    return (
        _prefix_block("ontokin")
        + f"""
SELECT ?mechanism ?label WHERE {{
  ?mechanism a ok:ReactionMechanism .
  OPTIONAL {{ ?mechanism rdfs:label ?label }}
{cursor_filter}}}
ORDER BY ?mechanism
LIMIT {lim}
"""
    )


def mechanism_at_index_query(*, index: int) -> str:
    return (
        _prefix_block("ontokin")
        + f"""
SELECT ?mechanism WHERE {{
  ?mechanism a ok:ReactionMechanism .
}}
ORDER BY ?mechanism
LIMIT 1 OFFSET {max(0, int(index))}
"""
    )


def bootstrap_mechanism_cursor(offset: int) -> str:
    if offset < 0:
        return ""
    rows = _execute("ontokin", mechanism_at_index_query(index=offset))
    if not rows:
        return ""
    return str(rows[0].get("mechanism", "")).strip()


def reactions_for_mechanisms_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontokin")
        + f"""
SELECT ?mechanism ?mechanismLabel ?reaction ?equation WHERE {{
  VALUES ?mechanism {{ {values} }}
  ?mechanism a ok:ReactionMechanism .
  ?mechanism ok:hasReaction ?reaction .
  OPTIONAL {{ ?mechanism rdfs:label ?mechanismLabel }}
  OPTIONAL {{ ?reaction ok:hasEquation ?equation }}
}}
"""
    )


def count_mechanisms_remote() -> int:
    q = (
        _prefix_block("ontokin")
        + """
SELECT (COUNT(DISTINCT ?m) AS ?n) WHERE { ?m a ok:ReactionMechanism . }
"""
    )
    rows = _execute("ontokin", q)
    try:
        return int(str(rows[0].get("n", "0")))
    except (IndexError, ValueError):
        return 0


def fetch_mechanism_page(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    raw = _execute("ontokin", mechanism_page_query(limit=limit, after_subject=after_subject))
    page_iris = [str(r.get("mechanism", "")).strip() for r in raw if str(r.get("mechanism", "")).strip()]
    rows: List[Dict[str, Any]] = []
    for r in raw:
        mech = str(r.get("mechanism", "")).strip()
        if not mech:
            continue
        rows.append({"mechanism_iri": mech, "label": str(r.get("label", "") or "").strip()})
    return rows, page_iris


def fetch_reaction_graph_page(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    _, page_iris = fetch_mechanism_page(limit=limit, after_subject=after_subject)
    if not page_iris:
        return [], []
    raw = _execute("ontokin", reactions_for_mechanisms_query(page_iris))
    out: List[Dict[str, Any]] = []
    for row in raw:
        mech = str(row.get("mechanism", "")).strip()
        rxn = str(row.get("reaction", "")).strip()
        if not mech or not rxn:
            continue
        eq = str(row.get("equation", "") or "").strip()
        out.append(
            {
                "mechanism_iri": mech,
                "mechanism_label": str(row.get("mechanismLabel", "") or "").strip(),
                "reaction_iri": rxn,
                "equation": eq,
                "equation_lc": eq.lower(),
            }
        )
    return out, page_iris


class OntokinCorpusStore:
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
            CREATE TABLE IF NOT EXISTS corpus_mechanisms (
                mechanism_iri TEXT PRIMARY KEY,
                label TEXT,
                indexed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_mech_label ON corpus_mechanisms(label);

            CREATE TABLE IF NOT EXISTS corpus_reaction_edges (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                mechanism_iri TEXT NOT NULL,
                mechanism_label TEXT,
                reaction_iri TEXT NOT NULL,
                equation TEXT,
                equation_lc TEXT,
                UNIQUE(mechanism_iri, reaction_iri, equation)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_rxn_mech ON corpus_reaction_edges(mechanism_iri);
            CREATE INDEX IF NOT EXISTS idx_corpus_rxn_eq ON corpus_reaction_edges(equation_lc);
            """
        )
        migrate_cursor_column(self._conn)

    def warm_state(self, corpus_id: str) -> Dict[str, Any]:
        return warm_state_row(self._conn, corpus_id)

    def warm_mechanisms_batch(self, *, offset: Optional[int] = None, batch_size: int = DEFAULT_BATCH_SIZE) -> Dict[str, Any]:
        corpus_id = MECH_CORPUS_ID
        state = self.warm_state(corpus_id)
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(state.get("total_species") or count_mechanisms_remote())
        if state.get("status") != "running" or state.get("total_species") is None:
            ensure_warm_state(self._conn, corpus_id=corpus_id, total=total, batch_size=batch_size)

        cursor = resolve_cursor(
            self._conn, corpus_id=corpus_id, offset=off, bootstrap_fn=bootstrap_mechanism_cursor
        )
        started = time.perf_counter()
        rows, page_iris = retry_corpus_fetch(
            "ontokin mechanisms",
            lambda: fetch_mechanism_page(limit=batch_size, after_subject=cursor),
        )
        now = time.time()
        for row in rows:
            self._conn.execute(
                """
                INSERT INTO corpus_mechanisms (mechanism_iri, label, indexed_at)
                VALUES (?, ?, ?)
                ON CONFLICT(mechanism_iri) DO UPDATE SET label=excluded.label, indexed_at=excluded.indexed_at
                """,
                (row["mechanism_iri"], row.get("label") or None, now),
            )
        self._conn.commit()
        new_cursor = page_iris[-1] if page_iris else cursor
        done = not page_iris or off + len(page_iris) >= total
        advance_warm_state(
            self._conn,
            corpus_id=corpus_id,
            offset=off,
            species_added=len(page_iris),
            rows_added=len(rows),
            batch_size=batch_size,
            total=total,
            done=done,
            cursor_subject=new_cursor,
        )
        return {
            "offset": off,
            "mechanisms": len(page_iris),
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "done": done,
            "cursor_subject": new_cursor,
        }

    def warm_graph_batch(self, *, offset: Optional[int] = None, batch_size: int = DEFAULT_BATCH_SIZE) -> Dict[str, Any]:
        corpus_id = GRAPH_CORPUS_ID
        state = self.warm_state(corpus_id)
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(state.get("total_species") or count_mechanisms_remote())
        if state.get("status") != "running" or state.get("total_species") is None:
            ensure_warm_state(self._conn, corpus_id=corpus_id, total=total, batch_size=batch_size)

        cursor = resolve_cursor(
            self._conn, corpus_id=corpus_id, offset=off, bootstrap_fn=bootstrap_mechanism_cursor
        )
        started = time.perf_counter()
        rows, page_iris = retry_corpus_fetch(
            "ontokin reaction graph",
            lambda: fetch_reaction_graph_page(limit=batch_size, after_subject=cursor),
        )
        inserted = 0
        for row in rows:
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_reaction_edges
                (mechanism_iri, mechanism_label, reaction_iri, equation, equation_lc)
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    row["mechanism_iri"],
                    row.get("mechanism_label") or "",
                    row["reaction_iri"],
                    row.get("equation") or "",
                    row.get("equation_lc") or "",
                ),
            )
            inserted += cur.rowcount
        self._conn.commit()
        new_cursor = page_iris[-1] if page_iris else cursor
        done = not page_iris or off + len(page_iris) >= total
        advance_warm_state(
            self._conn,
            corpus_id=corpus_id,
            offset=off,
            species_added=len(page_iris),
            rows_added=inserted,
            batch_size=batch_size,
            total=total,
            done=done,
            cursor_subject=new_cursor,
        )
        return {
            "offset": off,
            "mechanisms": len(page_iris),
            "edges_inserted": inserted,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "done": done,
            "cursor_subject": new_cursor,
        }

    def search_mechanisms(self, query: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT mechanism_iri, label FROM corpus_mechanisms
                WHERE LOWER(label) LIKE ? OR LOWER(mechanism_iri) LIKE ?
                LIMIT ?
                """,
                (f"%{q}%", f"%{q}%", max(1, int(limit))),
            )
        ]

    def traverse_reactions(
        self,
        *,
        mechanism_label: str = "",
        reaction_fragment: str = "",
        mechanism_iri_fragment: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if mechanism_label.strip():
            clauses.append("LOWER(mechanism_label) LIKE ?")
            params.append(f"%{mechanism_label.strip().lower()}%")
        if mechanism_iri_fragment.strip():
            clauses.append("mechanism_iri LIKE ?")
            params.append(f"%{mechanism_iri_fragment.strip()}%")
        if reaction_fragment.strip():
            clauses.append("(equation_lc LIKE ? OR reaction_iri LIKE ?)")
            frag = f"%{reaction_fragment.strip().lower()}%"
            params.extend([frag, f"%{reaction_fragment.strip()}%"])
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT mechanism_iri, mechanism_label, reaction_iri, equation
            FROM corpus_reaction_edges
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def traverse_reactions_tsv(self, **kwargs: Any) -> str:
        rows = self.traverse_reactions(**kwargs)
        if not rows:
            return "No results"
        out = [
            {
                "mechanism": r["mechanism_iri"],
                "mechanismLabel": r.get("mechanism_label", ""),
                "reaction": r["reaction_iri"],
                "equation": r.get("equation", ""),
            }
            for r in rows
        ]
        return format_tsv(out)

    def stats(self) -> Dict[str, Any]:
        mech_n = self._conn.execute("SELECT COUNT(*) FROM corpus_mechanisms").fetchone()[0]
        edge_n = self._conn.execute("SELECT COUNT(*) FROM corpus_reaction_edges").fetchone()[0]
        return {
            "mechanism_rows": int(mech_n),
            "reaction_edge_rows": int(edge_n),
            "warm_state_mechanisms": self.warm_state(MECH_CORPUS_ID),
            "warm_state_graph": self.warm_state(GRAPH_CORPUS_ID),
            "endpoint": endpoint("ontokin"),
            "sparql_timeout_s": sparql_timeout("ontokin"),
        }


def warm_ontokin_corpus(
    *,
    facet: str = "graph",
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    skip_health_check: bool = False,
    show_progress: bool = True,
) -> Dict[str, Any]:
    store = OntokinCorpusStore()
    corpus_id = MECH_CORPUS_ID if facet == "mechanisms" else GRAPH_CORPUS_ID
    resume = RESUME_MECH if facet == "mechanisms" else RESUME_GRAPH
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        total = count_mechanisms_remote()
        ensure_warm_state(store._conn, corpus_id=corpus_id, total=total, batch_size=batch_size)
        warm_fn = store.warm_mechanisms_batch if facet == "mechanisms" else store.warm_graph_batch
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        use_bar = show_progress and tqdm is not None and max_batches > 1
        batch_range = range(max_batches)
        iterator = tqdm(batch_range, desc=f"ontokin {facet}", unit="batch") if use_bar else batch_range
        for i in iterator:
            state = store.warm_state(corpus_id)
            if state.get("status") == "complete":
                break
            if not skip_health_check:
                health = namespace_health_ok("ontokin")
                if not health.get("ok"):
                    mark_paused(store._conn, corpus_id, f"health check failed: {health}")
                    errors.append({"phase": "health", **health})
                    break
            try:
                batch = warm_fn(batch_size=batch_size)
            except Exception as exc:
                mark_paused(store._conn, corpus_id, str(exc))
                errors.append({"phase": "fetch", "error": str(exc)})
                break
            results.append(batch)
            if batch.get("done"):
                break
            if i + 1 < max_batches and delay_seconds > 0:
                time.sleep(delay_seconds)
        stats = store.stats()
        paused = bool(errors) or store.warm_state(corpus_id).get("status") == "paused"
        return {
            "facet": facet,
            "batches": results,
            "stats": stats,
            "errors": errors,
            "paused": paused,
            "resume_hint": resume if paused else "",
        }
    finally:
        store.close()
