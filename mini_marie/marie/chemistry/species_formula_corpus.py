"""Derived formula index from warmed species names corpus (no SPARQL warm)."""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.sparql import format_tsv

CORPUS_ID = "ontospecies_formula"


class SpeciesFormulaStore:
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
            CREATE TABLE IF NOT EXISTS corpus_species_formula (
                species_iri TEXT NOT NULL,
                formula TEXT NOT NULL,
                formula_lc TEXT NOT NULL,
                UNIQUE(species_iri, formula)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_formula_lc ON corpus_species_formula(formula_lc);
            CREATE INDEX IF NOT EXISTS idx_corpus_formula_exact ON corpus_species_formula(formula);
            """
        )
        self._conn.commit()

    def build_from_names(self) -> int:
        """Populate from corpus_species_names where name_type='formula'."""
        names_n = self._conn.execute(
            "SELECT COUNT(*) FROM corpus_species_names WHERE name_type='formula'"
        ).fetchone()[0]
        if names_n == 0:
            return 0
        self._conn.execute("DELETE FROM corpus_species_formula")
        cur = self._conn.execute(
            """
            INSERT OR IGNORE INTO corpus_species_formula (species_iri, formula, formula_lc)
            SELECT species_iri, name_value, name_value_lc
            FROM corpus_species_names
            WHERE name_type = 'formula' AND name_value != ''
            """
        )
        self._conn.commit()
        return int(cur.rowcount)

    def lookup_formula(
        self,
        formula: str,
        *,
        match: str = "equals",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        q = formula.strip()
        if not q:
            return []
        if match == "equals":
            rows = self._conn.execute(
                """
                SELECT f.species_iri, f.formula, s.primary_label
                FROM corpus_species_formula f
                LEFT JOIN corpus_species s ON s.species_iri = f.species_iri
                WHERE f.formula = ? OR f.formula_lc = ?
                LIMIT ?
                """,
                (q, q.lower(), max(1, int(limit))),
            )
        else:
            ql = q.lower()
            rows = self._conn.execute(
                """
                SELECT f.species_iri, f.formula, s.primary_label
                FROM corpus_species_formula f
                LEFT JOIN corpus_species s ON s.species_iri = f.species_iri
                WHERE f.formula_lc LIKE ?
                LIMIT ?
                """,
                (f"%{ql}%", max(1, int(limit))),
            )
        return [dict(r) for r in rows]

    def lookup_formula_tsv(self, formula: str, *, match: str = "equals", limit: int = 50) -> str:
        rows = self.lookup_formula(formula, match=match, limit=limit)
        if not rows:
            return "No results"
        out = [
            {"subject": r["species_iri"], "label": r.get("primary_label") or "", "formula": r["formula"]}
            for r in rows
        ]
        return format_tsv(out)

    def stats(self) -> Dict[str, Any]:
        n = self._conn.execute("SELECT COUNT(*) FROM corpus_species_formula").fetchone()[0]
        species_n = self._conn.execute(
            "SELECT COUNT(DISTINCT species_iri) FROM corpus_species_formula"
        ).fetchone()[0]
        return {
            "corpus_id": CORPUS_ID,
            "formula_rows": int(n),
            "species_with_formula": int(species_n),
            "derived_from": "corpus_species_names.name_type=formula",
        }


def build_formula_index() -> Dict[str, Any]:
    store = SpeciesFormulaStore()
    try:
        inserted = store.build_from_names()
        return {"inserted": inserted, "stats": store.stats()}
    finally:
        store.close()
