"""
OntoSpecies competency join layer — derived SQL tables from warmed corpus facets.

No SPARQL: rebuild from corpus_species*, corpus_species_pka, corpus_species_references, etc.
Shared spine: species_iri + identifiers (formula, SMILES, InChI) reused across MQ joins.

Marie MQ coverage (join-ready offline):
  MQ1  physprops wide (H-bond donor/acceptor)
  MQ2  uses enriched
  MQ3  formula index (separate module) + identifiers
  MQ4–7, MQ12–19  pka enriched (+ provenance ref label)
  MQ11 species profile (pka/use/prop counts)
  MQ17 cross-ref via pka_enriched.provenance + ref_label
"""

from __future__ import annotations

import sqlite3
import time
from typing import Any, Dict, List, Optional

from mini_marie.marie.chemistry.chemistry_cache import db_path
from mini_marie.marie.chemistry.sparql import format_tsv

CORPUS_ID = "ontospecies_joins"

_IDENTIFIER_TYPES = ("formula", "smiles", "inchi", "inchikey", "iupac")
_PHYSPROP_PIVOT = (
    ("hasHydrogenBondDonorCount", "hbond_donors"),
    ("hasHydrogenBondAcceptorCount", "hbond_acceptors"),
    ("hasTopologicalPolarSurfaceArea", "tpsa"),
    ("hasExactMass", "exact_mass"),
)


class SpeciesJoinStore:
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
            CREATE TABLE IF NOT EXISTS corpus_join_meta (
                join_set TEXT PRIMARY KEY,
                built_at REAL NOT NULL,
                row_count INTEGER NOT NULL DEFAULT 0,
                source_tables TEXT NOT NULL DEFAULT ''
            );

            CREATE TABLE IF NOT EXISTS corpus_species_identifiers (
                species_iri TEXT PRIMARY KEY,
                primary_label TEXT,
                formula TEXT,
                smiles TEXT,
                inchi TEXT,
                inchikey TEXT,
                iupac TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_species_ident_formula
                ON corpus_species_identifiers(formula);
            CREATE INDEX IF NOT EXISTS idx_species_ident_smiles
                ON corpus_species_identifiers(smiles);
            CREATE INDEX IF NOT EXISTS idx_species_ident_inchi
                ON corpus_species_identifiers(inchi);

            CREATE TABLE IF NOT EXISTS corpus_species_pka_enriched (
                pka_row_id INTEGER PRIMARY KEY,
                species_iri TEXT NOT NULL,
                primary_label TEXT,
                formula TEXT,
                smiles TEXT,
                inchi TEXT,
                pka_iri TEXT,
                pka_value TEXT,
                temperature TEXT,
                ionic_strength TEXT,
                method TEXT,
                reliability TEXT,
                acidity_label TEXT,
                provenance TEXT,
                ref_label TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_pka_enriched_species
                ON corpus_species_pka_enriched(species_iri);
            CREATE INDEX IF NOT EXISTS idx_pka_enriched_reliability
                ON corpus_species_pka_enriched(reliability);
            CREATE INDEX IF NOT EXISTS idx_pka_enriched_method
                ON corpus_species_pka_enriched(method);

            CREATE TABLE IF NOT EXISTS corpus_species_uses_enriched (
                use_row_id INTEGER PRIMARY KEY,
                species_iri TEXT NOT NULL,
                use_value TEXT NOT NULL,
                primary_label TEXT,
                formula TEXT,
                smiles TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_uses_enriched_lc
                ON corpus_species_uses_enriched(use_value);
            CREATE INDEX IF NOT EXISTS idx_uses_enriched_species
                ON corpus_species_uses_enriched(species_iri);

            CREATE TABLE IF NOT EXISTS corpus_species_physprops_wide (
                species_iri TEXT PRIMARY KEY,
                primary_label TEXT,
                formula TEXT,
                smiles TEXT,
                hbond_donors TEXT,
                hbond_acceptors TEXT,
                tpsa TEXT,
                exact_mass TEXT
            );

            CREATE TABLE IF NOT EXISTS corpus_species_profile (
                species_iri TEXT PRIMARY KEY,
                primary_label TEXT,
                formula TEXT,
                pka_count INTEGER NOT NULL DEFAULT 0,
                use_count INTEGER NOT NULL DEFAULT 0,
                prop_count INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_species_profile_pka
                ON corpus_species_profile(pka_count DESC);
            """
        )
        self._conn.commit()

    def _table_exists(self, name: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            (name,),
        ).fetchone()
        return row is not None

    def _touch_meta(self, join_set: str, row_count: int, sources: str) -> None:
        self._conn.execute(
            """
            INSERT INTO corpus_join_meta (join_set, built_at, row_count, source_tables)
            VALUES (?, ?, ?, ?)
            ON CONFLICT(join_set) DO UPDATE SET
              built_at=excluded.built_at,
              row_count=excluded.row_count,
              source_tables=excluded.source_tables
            """,
            (join_set, time.time(), int(row_count), sources),
        )

    def build_identifiers(self) -> int:
        if not self._table_exists("corpus_species_names"):
            return 0
        self._conn.execute("DELETE FROM corpus_species_identifiers")
        pivot_cols = ",\n".join(
            f"MAX(CASE WHEN n.name_type = '{t}' THEN n.name_value END) AS {t}"
            for t in _IDENTIFIER_TYPES
        )
        cur = self._conn.execute(
            f"""
            INSERT INTO corpus_species_identifiers
                (species_iri, primary_label, formula, smiles, inchi, inchikey, iupac)
            SELECT
                s.species_iri,
                s.primary_label,
                {pivot_cols}
            FROM corpus_species s
            LEFT JOIN corpus_species_names n ON n.species_iri = s.species_iri
            GROUP BY s.species_iri, s.primary_label
            """
        )
        self._conn.commit()
        count = int(cur.rowcount)
        self._touch_meta(
            "identifiers",
            count,
            "corpus_species,corpus_species_names",
        )
        return count

    def build_pka_enriched(self) -> int:
        if not self._table_exists("corpus_species_pka"):
            return 0
        self._conn.execute("DELETE FROM corpus_species_pka_enriched")
        refs_join = ""
        if self._table_exists("corpus_species_references"):
            refs_join = "LEFT JOIN corpus_species_references r ON p.provenance = r.ref_iri"
            ref_sel = "COALESCE(r.label_value, '')"
        else:
            ref_sel = "''"
        cur = self._conn.execute(
            f"""
            INSERT INTO corpus_species_pka_enriched (
                pka_row_id, species_iri, primary_label, formula, smiles, inchi,
                pka_iri, pka_value, temperature, ionic_strength, method,
                reliability, acidity_label, provenance, ref_label
            )
            SELECT
                p.id,
                p.species_iri,
                COALESCE(s.primary_label, id.primary_label, ''),
                COALESCE(id.formula, ''),
                COALESCE(id.smiles, ''),
                COALESCE(id.inchi, ''),
                COALESCE(p.pka_iri, ''),
                p.pka_value,
                COALESCE(p.temperature, ''),
                COALESCE(p.ionic_strength, ''),
                COALESCE(p.method, ''),
                COALESCE(p.reliability, ''),
                COALESCE(p.acidity_label, ''),
                COALESCE(p.provenance, ''),
                {ref_sel}
            FROM corpus_species_pka p
            LEFT JOIN corpus_species s ON s.species_iri = p.species_iri
            LEFT JOIN corpus_species_identifiers id ON id.species_iri = p.species_iri
            {refs_join}
            """
        )
        self._conn.commit()
        count = int(cur.rowcount)
        self._touch_meta(
            "pka_enriched",
            count,
            "corpus_species_pka,corpus_species_identifiers,corpus_species_references",
        )
        return count

    def build_uses_enriched(self) -> int:
        if not self._table_exists("corpus_species_uses"):
            return 0
        self._conn.execute("DELETE FROM corpus_species_uses_enriched")
        cur = self._conn.execute(
            """
            INSERT INTO corpus_species_uses_enriched (
                use_row_id, species_iri, use_value, primary_label, formula, smiles
            )
            SELECT
                u.id,
                u.species_iri,
                u.use_value,
                COALESCE(id.primary_label, s.primary_label, ''),
                COALESCE(id.formula, ''),
                COALESCE(id.smiles, '')
            FROM corpus_species_uses u
            LEFT JOIN corpus_species_identifiers id ON id.species_iri = u.species_iri
            LEFT JOIN corpus_species s ON s.species_iri = u.species_iri
            """
        )
        self._conn.commit()
        count = int(cur.rowcount)
        self._touch_meta("uses_enriched", count, "corpus_species_uses,corpus_species_identifiers")
        return count

    def build_physprops_wide(self) -> int:
        if not self._table_exists("corpus_species_properties"):
            return 0
        self._conn.execute("DELETE FROM corpus_species_physprops_wide")
        pivot = ",\n".join(
            f"MAX(CASE WHEN pp.property_local = '{src}' THEN pp.property_value END) AS {dst}"
            for src, dst in _PHYSPROP_PIVOT
        )
        cur = self._conn.execute(
            f"""
            INSERT INTO corpus_species_physprops_wide (
                species_iri, primary_label, formula, smiles,
                hbond_donors, hbond_acceptors, tpsa, exact_mass
            )
            SELECT
                pp.species_iri,
                COALESCE(id.primary_label, s.primary_label, ''),
                COALESCE(id.formula, ''),
                COALESCE(id.smiles, ''),
                {pivot}
            FROM corpus_species_properties pp
            LEFT JOIN corpus_species_identifiers id ON id.species_iri = pp.species_iri
            LEFT JOIN corpus_species s ON s.species_iri = pp.species_iri
            GROUP BY pp.species_iri
            """
        )
        self._conn.commit()
        count = int(cur.rowcount)
        self._touch_meta(
            "physprops_wide",
            count,
            "corpus_species_properties,corpus_species_identifiers",
        )
        return count

    def build_profile(self) -> int:
        if not self._table_exists("corpus_species"):
            return 0
        self._conn.execute("DELETE FROM corpus_species_profile")
        cur = self._conn.execute(
            """
            INSERT INTO corpus_species_profile (
                species_iri, primary_label, formula, pka_count, use_count, prop_count
            )
            SELECT
                s.species_iri,
                COALESCE(id.primary_label, s.primary_label, ''),
                COALESCE(id.formula, ''),
                COALESCE((SELECT COUNT(*) FROM corpus_species_pka p WHERE p.species_iri = s.species_iri), 0),
                COALESCE((SELECT COUNT(*) FROM corpus_species_uses u WHERE u.species_iri = s.species_iri), 0),
                COALESCE((SELECT COUNT(*) FROM corpus_species_properties pp WHERE pp.species_iri = s.species_iri), 0)
            FROM corpus_species s
            LEFT JOIN corpus_species_identifiers id ON id.species_iri = s.species_iri
            """
        )
        self._conn.commit()
        count = int(cur.rowcount)
        self._touch_meta(
            "profile",
            count,
            "corpus_species,corpus_species_identifiers,+counts",
        )
        return count

    def build_all(self) -> Dict[str, int]:
        """Rebuild join tables in dependency order (local SQL only)."""
        out: Dict[str, int] = {}
        out["identifiers"] = self.build_identifiers()
        out["pka_enriched"] = self.build_pka_enriched()
        out["uses_enriched"] = self.build_uses_enriched()
        out["physprops_wide"] = self.build_physprops_wide()
        out["profile"] = self.build_profile()
        return out

    def is_built(self) -> bool:
        row = self._conn.execute(
            "SELECT COUNT(*) FROM corpus_join_meta WHERE join_set = 'pka_enriched'"
        ).fetchone()
        return bool(row and row[0])

    def stats(self) -> Dict[str, Any]:
        meta = [
            dict(r)
            for r in self._conn.execute(
                "SELECT join_set, row_count, source_tables, built_at FROM corpus_join_meta ORDER BY join_set"
            )
        ]
        counts: Dict[str, int] = {}
        for table in (
            "corpus_species_identifiers",
            "corpus_species_pka_enriched",
            "corpus_species_uses_enriched",
            "corpus_species_physprops_wide",
            "corpus_species_profile",
        ):
            if self._table_exists(table):
                counts[table] = int(
                    self._conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                )
        metadata_coverage: Dict[str, int] = {}
        if self._table_exists("corpus_species_pka_enriched"):
            for col in (
                "temperature",
                "ionic_strength",
                "method",
                "reliability",
                "acidity_label",
                "provenance",
                "ref_label",
            ):
                metadata_coverage[col] = int(
                    self._conn.execute(
                        f"SELECT COUNT(*) FROM corpus_species_pka_enriched "
                        f"WHERE {col} IS NOT NULL AND TRIM({col}) != ''"
                    ).fetchone()[0]
                )
        return {
            "corpus_id": CORPUS_ID,
            "meta": meta,
            "row_counts": counts,
            "metadata_coverage": metadata_coverage,
        }

    def query_pka_enriched(
        self,
        *,
        reliability_fragment: str = "",
        method_fragment: str = "",
        species_fragment: str = "",
        ref_label_fragment: str = "",
        acidity_label_fragment: str = "",
        limit: int = 50,
    ) -> List[Dict[str, Any]]:
        clauses = ["1=1"]
        params: List[Any] = []
        if reliability_fragment.strip():
            clauses.append("reliability LIKE ?")
            params.append(f"%{reliability_fragment.strip()}%")
        if method_fragment.strip():
            clauses.append("method LIKE ?")
            params.append(f"%{method_fragment.strip()}%")
        if acidity_label_fragment.strip():
            clauses.append("acidity_label LIKE ?")
            params.append(f"%{acidity_label_fragment.strip()}%")
        if ref_label_fragment.strip():
            clauses.append("ref_label LIKE ?")
            params.append(f"%{ref_label_fragment.strip()}%")
        if species_fragment.strip():
            q = species_fragment.strip()
            id_parts = [
                "primary_label LIKE ?",
                "formula LIKE ?",
                "smiles LIKE ?",
                "inchi LIKE ?",
            ]
            params.extend([f"%{q}%"] * 4)
            if self._table_exists("corpus_species_names"):
                id_parts.append(
                    "species_iri IN ("
                    "SELECT species_iri FROM corpus_species_names WHERE name_value LIKE ?)"
                )
                params.append(f"%{q}%")
            clauses.append("(" + " OR ".join(id_parts) + ")")
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT * FROM corpus_species_pka_enriched
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def query_pka_enriched_tsv(self, **kwargs: Any) -> str:
        rows = self.query_pka_enriched(**kwargs)
        if not rows:
            return "No results"
        out = [
            {
                "species": r["species_iri"],
                "label": r.get("primary_label") or "",
                "pka": r.get("pka_value") or "",
                "reliability": r.get("reliability") or "",
                "method": r.get("method") or "",
                "provenance": r.get("ref_label") or r.get("provenance") or "",
            }
            for r in rows
        ]
        return format_tsv(out)

    def lookup_physprops_by_smiles(self, smiles: str, *, limit: int = 10) -> List[Dict[str, Any]]:
        q = smiles.strip()
        if not q:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT * FROM corpus_species_physprops_wide
                WHERE smiles = ?
                LIMIT ?
                """,
                (q, max(1, int(limit))),
            )
        ]

    def lookup_uses_enriched(self, use_fragment: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        q = use_fragment.strip().lower()
        if not q:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT species_iri, use_value, primary_label, formula, smiles
                FROM corpus_species_uses_enriched
                WHERE use_value LIKE ?
                LIMIT ?
                """,
                (f"%{q}%", max(1, int(limit))),
            )
        ]

    def top_species_by_pka_count(self, *, limit: int = 10) -> List[Dict[str, Any]]:
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT species_iri, primary_label, formula, pka_count, use_count, prop_count
                FROM corpus_species_profile
                WHERE pka_count > 0
                ORDER BY pka_count DESC, species_iri
                LIMIT ?
                """,
                (max(1, int(limit)),),
            )
        ]


def build_species_join_tables() -> Dict[str, Any]:
    store = SpeciesJoinStore()
    try:
        counts = store.build_all()
        return {"built": counts, "stats": store.stats()}
    finally:
        store.close()
