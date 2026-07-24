"""OntoKin rate / thermo / transport model corpus for MQ26–MQ30."""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Sequence

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.corpus_common import (
    advance_warm_state,
    mark_paused,
    migrate_cursor_column,
    warm_state_row,
)
from mini_marie.marie.chemistry.corpus_fetch import retry_corpus_fetch
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS, sparql_timeout
from mini_marie.marie.chemistry.query_builder import _execute, _prefix_block
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import format_tsv

CORPUS_ID = "ontokin_rate_models"
DEFAULT_BATCH_SIZE = 50
RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_ontokin_rate_corpus --max-batches 0 --delay 3"


def _esc_iri(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def reaction_iri_page_query(*, limit: int, after_subject: str = "") -> str:
    lim = max(1, int(limit))
    cursor_filter = ""
    if after_subject.strip():
        cursor_filter = f'  FILTER(STR(?reaction) > "{_esc_iri(after_subject.strip())}")\n'
    return (
        _prefix_block("ontokin")
        + f"""
SELECT ?reaction WHERE {{
  ?reaction a ok:GasPhaseReaction .
  ?reaction ok:hasKineticModel ?kmodel .
{cursor_filter}}}
ORDER BY ?reaction
LIMIT {lim}
"""
    )


def rate_models_for_reactions_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontokin")
        + f"""
SELECT ?reaction ?equation ?mechanism ?model_kind ?submodel ?A ?n ?Ea WHERE {{
  VALUES ?reaction {{ {values} }}
  OPTIONAL {{ ?reaction ok:hasEquation ?eq . ?eq ok:value ?equation . }}
  ?reaction ok:hasKineticModel ?kmodel .
  ?kmodel ok:definedIn ?mechanism .
  {{
    ?kmodel ok:hasArrheniusModel ?submodel .
    BIND("arrhenius" AS ?model_kind)
  }} UNION {{
    ?kmodel ok:hasArrheniusLowModel ?submodel .
    BIND("arrhenius_low" AS ?model_kind)
  }} UNION {{
    ?kmodel ok:hasArrheniusHighModel ?submodel .
    BIND("arrhenius_high" AS ?model_kind)
  }}
  ?submodel ok:hasArrheniusFactor ?aN . ?aN ok:value ?A .
  OPTIONAL {{ ?submodel ok:hasTemperatureExponent ?nN . ?nN ok:value ?n }}
  OPTIONAL {{ ?submodel ok:hasActivationEnergy ?eN . ?eN ok:value ?Ea }}
}}
"""
    )


def count_reactions_with_kinetic_remote() -> int:
    q = (
        _prefix_block("ontokin")
        + """
SELECT (COUNT(DISTINCT ?r) AS ?n) WHERE {
  ?r a ok:GasPhaseReaction .
  ?r ok:hasKineticModel ?m .
}
"""
    )
    rows = _execute("ontokin", q)
    try:
        return int(str(rows[0].get("n", "0")))
    except (IndexError, ValueError):
        return 0


def fetch_rate_model_rows(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    iri_rows = _execute("ontokin", reaction_iri_page_query(limit=limit, after_subject=after_subject))
    page_iris = [str(r.get("reaction", "")).strip() for r in iri_rows if str(r.get("reaction", "")).strip()]
    if not page_iris:
        return [], []
    raw = _execute("ontokin", rate_models_for_reactions_query(page_iris))
    out: List[Dict[str, Any]] = []
    for row in raw:
        reaction = str(row.get("reaction", "")).strip()
        submodel = str(row.get("submodel", "")).strip()
        if not reaction or not submodel:
            continue
        out.append(
            {
                "reaction_iri": reaction,
                "equation": str(row.get("equation", "") or "").strip(),
                "mechanism_iri": str(row.get("mechanism", "") or "").strip(),
                "model_kind": str(row.get("model_kind", "") or "").strip(),
                "submodel_iri": submodel,
                "arrhenius_a": str(row.get("A", "") or "").strip(),
                "arrhenius_n": str(row.get("n", "") or "").strip(),
                "arrhenius_ea": str(row.get("Ea", "") or "").strip(),
            }
        )
    return out, page_iris


def fetch_rate_model_rows_with_retry(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    return retry_corpus_fetch(
        "ontokin rate models",
        lambda: fetch_rate_model_rows(limit=limit, after_subject=after_subject),
    )


@dataclass
class RateWarmBatchResult:
    offset: int
    batch_size: int
    reactions_in_batch: int
    model_rows: int
    elapsed_ms: int
    done: bool
    cursor_subject: str = ""


class OntokinRateCorpusStore:
    def __init__(self, path: Optional[str] = None) -> None:
        self.path = str(path or db_path())
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._init_schema()
        migrate_cursor_column(self._conn)

    def close(self) -> None:
        self._conn.close()

    def _init_schema(self) -> None:
        self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS corpus_reaction_models (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                reaction_iri TEXT NOT NULL,
                equation TEXT,
                equation_lc TEXT,
                mechanism_iri TEXT,
                model_kind TEXT NOT NULL,
                submodel_iri TEXT NOT NULL,
                arrhenius_a TEXT,
                arrhenius_n TEXT,
                arrhenius_ea TEXT,
                UNIQUE(reaction_iri, submodel_iri, model_kind)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_rate_reaction ON corpus_reaction_models(reaction_iri);
            CREATE INDEX IF NOT EXISTS idx_corpus_rate_mechanism ON corpus_reaction_models(mechanism_iri);
            CREATE INDEX IF NOT EXISTS idx_corpus_rate_equation ON corpus_reaction_models(equation_lc);
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

    def _lookup_equation(self, reaction_iri: str) -> str:
        row = self._conn.execute(
            """
            SELECT equation FROM corpus_reaction_edges
            WHERE reaction_iri = ? AND equation != ''
            LIMIT 1
            """,
            (reaction_iri,),
        ).fetchone()
        return str(row[0]) if row else ""

    def backfill_equations_from_graph(self) -> int:
        updated = 0
        for reaction_iri, in self._conn.execute(
            "SELECT DISTINCT reaction_iri FROM corpus_reaction_models WHERE equation = '' OR equation IS NULL"
        ):
            eq = self._lookup_equation(reaction_iri)
            if not eq:
                continue
            cur = self._conn.execute(
                """
                UPDATE corpus_reaction_models
                SET equation = ?, equation_lc = ?
                WHERE reaction_iri = ?
                """,
                (eq, eq.lower(), reaction_iri),
            )
            updated += cur.rowcount
        self._conn.commit()
        return updated

    def upsert_rows(self, rows: Sequence[Dict[str, Any]]) -> int:
        inserted = 0
        for row in rows:
            eq = row.get("equation") or ""
            if not eq:
                eq = self._lookup_equation(row["reaction_iri"])
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_reaction_models
                (reaction_iri, equation, equation_lc, mechanism_iri, model_kind, submodel_iri,
                 arrhenius_a, arrhenius_n, arrhenius_ea)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    row["reaction_iri"],
                    eq,
                    eq.lower(),
                    row.get("mechanism_iri") or "",
                    row["model_kind"],
                    row["submodel_iri"],
                    row.get("arrhenius_a") or "",
                    row.get("arrhenius_n") or "",
                    row.get("arrhenius_ea") or "",
                ),
            )
            inserted += cur.rowcount
        self._conn.commit()
        return inserted

    def warm_batch(
        self,
        *,
        offset: Optional[int] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        total_reactions: Optional[int] = None,
    ) -> RateWarmBatchResult:
        state = self.warm_state()
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total = int(total_reactions or state.get("total_species") or count_reactions_with_kinetic_remote())
        if state.get("status") not in ("running", "complete") or not state.get("total_species"):
            self._conn.execute(
                """
                INSERT INTO corpus_warm_state
                (corpus_id, total_species, offset_next, batch_size, species_indexed,
                 name_rows_indexed, batches_done, status, updated_at)
                VALUES (?, ?, ?, ?, 0, 0, 0, 'running', ?)
                ON CONFLICT(corpus_id) DO UPDATE SET
                  total_species=excluded.total_species,
                  batch_size=excluded.batch_size,
                  status='running',
                  updated_at=excluded.updated_at
                """,
                (CORPUS_ID, total, off, batch_size, time.time()),
            )
            self._conn.commit()
        elif state.get("status") == "complete" and int(state.get("species_indexed") or 0) < total:
            self._conn.execute(
                "UPDATE corpus_warm_state SET status='running', updated_at=? WHERE corpus_id=?",
                (time.time(), CORPUS_ID),
            )
            self._conn.commit()

        cursor = str(state.get("cursor_subject") or "").strip()
        started = time.perf_counter()
        rows, page_iris = fetch_rate_model_rows_with_retry(limit=batch_size, after_subject=cursor)
        new_cursor = page_iris[-1] if page_iris else cursor
        inserted = self.upsert_rows(rows)
        new_offset = off + len(page_iris)
        done = new_offset >= total
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
        return RateWarmBatchResult(
            offset=off,
            batch_size=batch_size,
            reactions_in_batch=len(page_iris),
            model_rows=inserted,
            elapsed_ms=round((time.perf_counter() - started) * 1000),
            done=done,
            cursor_subject=new_cursor,
        )

    def search_rate_models(
        self,
        *,
        equation_fragment: str = "",
        mechanism_iri_fragment: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if equation_fragment.strip():
            like = f"%{equation_fragment.strip().lower()}%"
            clauses.append("(m.equation_lc LIKE ? OR LOWER(m.reaction_iri) LIKE ?)")
            params.extend([like, like])
        if mechanism_iri_fragment.strip():
            clauses.append("m.mechanism_iri LIKE ?")
            params.append(f"%{mechanism_iri_fragment.strip()}%")
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT m.reaction_iri, m.equation, m.mechanism_iri, m.model_kind,
                   m.arrhenius_a, m.arrhenius_n, m.arrhenius_ea
            FROM corpus_reaction_models m
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def compare_rate_constants_tsv(self, equation_fragment: str, *, limit: int = 50) -> str:
        rows = self.search_rate_models(equation_fragment=equation_fragment, limit=limit)
        if not rows:
            return "No results"
        return format_tsv(rows)

    def stats(self) -> Dict[str, Any]:
        model_n = self._conn.execute("SELECT COUNT(*) FROM corpus_reaction_models").fetchone()[0]
        rxn_n = self._conn.execute(
            "SELECT COUNT(DISTINCT reaction_iri) FROM corpus_reaction_models"
        ).fetchone()[0]
        return {
            "corpus_id": CORPUS_ID,
            "model_rows": int(model_n),
            "reactions_with_models": int(rxn_n),
            "warm_state": self.warm_state(),
            "endpoint": endpoint("ontokin"),
            "sparql_timeout_s": sparql_timeout("ontokin"),
        }


def warm_rate_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    skip_health_check: bool = False,
    show_progress: bool = True,
) -> Dict[str, Any]:
    store = OntokinRateCorpusStore()
    try:
        if not skip_health_check:
            health = namespace_health_ok("ontokin")
            if not health.get("ok"):
                mark_paused(store._conn, CORPUS_ID, str(health.get("error") or "health check failed"))
                return {"status": "paused", "health": health, "resume_hint": RESUME_HINT}
        total = count_reactions_with_kinetic_remote()
        batches: List[Dict[str, Any]] = []
        for i in range(max(1, max_batches)):
            batch = store.warm_batch(batch_size=batch_size, total_reactions=total)
            batches.append(
                {
                    "batch": i + 1,
                    "reactions": batch.reactions_in_batch,
                    "model_rows": batch.model_rows,
                    "done": batch.done,
                    "elapsed_ms": batch.elapsed_ms,
                }
            )
            if show_progress:
                print(
                    f"  batch {i + 1}: reactions={batch.reactions_in_batch} "
                    f"models={batch.model_rows} done={batch.done}",
                    flush=True,
                )
            if batch.done:
                break
            if i + 1 < max_batches and delay_seconds > 0:
                time.sleep(delay_seconds)
        stats = store.stats()
        return {"status": "complete" if batches and batches[-1]["done"] else "running", "batches": batches, **stats}
    finally:
        store.close()
