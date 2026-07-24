"""OntoCompChem calculation results corpus."""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.corpus_common import ensure_warm_state, mark_paused, warm_state_row
from mini_marie.marie.chemistry.corpus_fetch import retry_corpus_fetch
from mini_marie.marie.chemistry.corpus_health import namespace_health_ok
from mini_marie.marie.chemistry.limits import WARM_DELAY_SECONDS, sparql_timeout
from mini_marie.marie.chemistry.query_builder import COMPCHEM_RESULT_TYPES, _execute, _prefix_block
from mini_marie.marie.chemistry.registry import endpoint
from mini_marie.marie.chemistry.sparql import format_tsv

CORPUS_ID = "ontocompchem_results"
RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_compchem_corpus"


def full_results_query() -> str:
    type_filter = ", ".join(f"occ:{t}" for t in sorted(set(COMPCHEM_RESULT_TYPES.values())))
    return (
        _prefix_block("ontocompchem")
        + f"""
SELECT ?result ?resultType ?value ?unit ?rotCount WHERE {{
  ?result a ?resultType .
  FILTER(?resultType IN ({type_filter}))
  ?result occ:value ?value .
  OPTIONAL {{ ?result occ:unit ?unit }}
  OPTIONAL {{ ?result occ:hasRotationalConstantsCount ?rotCount }}
}}
"""
    )


class CompChemCorpusStore:
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
            CREATE TABLE IF NOT EXISTS corpus_qm_results (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                result_iri TEXT NOT NULL UNIQUE,
                result_type TEXT,
                species_fragment TEXT,
                method_fragment TEXT,
                basis_fragment TEXT,
                value TEXT,
                unit TEXT,
                rot_count TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_qm_species ON corpus_qm_results(species_fragment);
            CREATE INDEX IF NOT EXISTS idx_corpus_qm_type ON corpus_qm_results(result_type);
            """
        )

    @staticmethod
    def _fragments(result_iri: str) -> tuple[str, str, str]:
        text = result_iri.lower()
        species = ""
        method = ""
        basis = ""
        if "species_" in text:
            species = text.split("species_")[-1].split("/")[0].split("#")[0][:36]
        for token in ("ub3lyp", "rb3lyp", "b3lyp", "cc-pvtz", "cc-pvqz", "cc-pvdz"):
            if token in text:
                if token.startswith("cc-"):
                    basis = token.upper()
                else:
                    method = token.upper()
        return species, method, basis

    def warm_all(self) -> Dict[str, Any]:
        ensure_warm_state(self._conn, corpus_id=CORPUS_ID, total=1, batch_size=1)
        started = time.perf_counter()
        rows = retry_corpus_fetch("compchem results", lambda: _execute("ontocompchem", full_results_query()))
        inserted = 0
        for row in rows:
            result_iri = str(row.get("result", "")).strip()
            if not result_iri:
                continue
            species_f, method_f, basis_f = self._fragments(result_iri)
            rtype = str(row.get("resultType", "")).split("#")[-1]
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_qm_results
                (result_iri, result_type, species_fragment, method_fragment, basis_fragment, value, unit, rot_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    result_iri,
                    rtype,
                    species_f,
                    method_f,
                    basis_f,
                    str(row.get("value", "")),
                    str(row.get("unit", "") or ""),
                    str(row.get("rotCount", "") or ""),
                ),
            )
            inserted += cur.rowcount
        self._conn.execute(
            """
            UPDATE corpus_warm_state SET
              offset_next=1, species_indexed=?, name_rows_indexed=?, batches_done=1,
              status='complete', updated_at=?, error=''
            WHERE corpus_id=?
            """,
            (len(rows), inserted, time.time(), CORPUS_ID),
        )
        self._conn.commit()
        return {
            "rows_fetched": len(rows),
            "rows_inserted": inserted,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "done": True,
        }

    def query_results(
        self,
        species_label: str,
        *,
        result_kinds: Optional[List[str]] = None,
        method_fragment: str = "",
        basis_fragment: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if species_label.strip():
            clauses.append("(species_fragment LIKE ? OR result_iri LIKE ?)")
            frag = f"%{species_label.strip().lower()}%"
            params.extend([frag, frag])
        kinds = [k.lower() for k in (result_kinds or [])]
        if kinds:
            type_names = []
            for k in kinds:
                t = COMPCHEM_RESULT_TYPES.get(k)
                if t:
                    type_names.append(t)
            if type_names:
                placeholders = ", ".join("?" for _ in type_names)
                clauses.append(f"result_type IN ({placeholders})")
                params.extend(type_names)
        if method_fragment.strip():
            clauses.append("(method_fragment LIKE ? OR result_iri LIKE ?)")
            mf = f"%{method_fragment.strip().lower()}%"
            params.extend([mf, mf])
        if basis_fragment.strip():
            clauses.append("(basis_fragment LIKE ? OR result_iri LIKE ?)")
            bf = f"%{basis_fragment.strip().lower()}%"
            params.extend([bf, bf])
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT result_iri, result_type, value, unit, rot_count, species_fragment, method_fragment, basis_fragment
            FROM corpus_qm_results
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def query_results_tsv(self, species_label: str, **kwargs: Any) -> str:
        rows = self.query_results(species_label, **kwargs)
        if not rows:
            return "No results"
        out = [
            {
                "result": r["result_iri"],
                "resultType": r.get("result_type", ""),
                "value": r.get("value", ""),
                "unit": r.get("unit", ""),
                "rotCount": r.get("rot_count", ""),
            }
            for r in rows
        ]
        return format_tsv(out)

    def stats(self) -> Dict[str, Any]:
        n = self._conn.execute("SELECT COUNT(*) FROM corpus_qm_results").fetchone()[0]
        return {
            "corpus_id": CORPUS_ID,
            "qm_result_rows": int(n),
            "warm_state": warm_state_row(self._conn, CORPUS_ID),
            "endpoint": endpoint("ontocompchem"),
            "sparql_timeout_s": sparql_timeout("ontocompchem"),
        }


def warm_compchem_corpus(*, skip_health_check: bool = False) -> Dict[str, Any]:
    store = CompChemCorpusStore()
    errors: List[Dict[str, Any]] = []
    try:
        if not skip_health_check:
            health = namespace_health_ok("ontocompchem")
            if not health.get("ok"):
                mark_paused(store._conn, CORPUS_ID, f"health check failed: {health}")
                return {"errors": [health], "paused": True, "resume_hint": RESUME_HINT}
        batch = store.warm_all()
        return {"batches": [batch], "stats": store.stats(), "errors": [], "paused": False}
    except Exception as exc:
        mark_paused(store._conn, CORPUS_ID, str(exc))
        errors.append({"error": str(exc)})
        return {"errors": errors, "paused": True, "resume_hint": RESUME_HINT}
    finally:
        store.close()
