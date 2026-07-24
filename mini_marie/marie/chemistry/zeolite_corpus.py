"""OntoZeolite corpus: material names, properties, framework index, reference map."""

from __future__ import annotations

import sqlite3
import time
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

MATERIALS_CORPUS_ID = "ontozeolite_materials"
DEFAULT_BATCH_SIZE = 25
RESUME_HINT = "python -m mini_marie.marie.chemistry.warm_zeolite_corpus --max-batches 0 --delay 3"

ZEOLITE_PROPS = (
    "hasLatticeSystem",
    "hasGuestSpecies",
    "hasGuestFormula",
    "hasOccupiableArea",
    "hasUnitCellVolume",
    "hasChemicalFormula",
    "isReferenceZeolite",
)


def _esc_iri(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def material_page_query(*, limit: int, after_subject: str = "") -> str:
    lim = max(1, int(limit))
    cursor_filter = ""
    if after_subject.strip():
        cursor_filter = f'  FILTER(STR(?material) > "{_esc_iri(after_subject.strip())}")\n'
    return (
        _prefix_block("ontozeolite")
        + f"""
SELECT ?material WHERE {{
  ?material a oz:ZeoliticMaterial .
{cursor_filter}}}
ORDER BY ?material
LIMIT {lim}
"""
    )


def material_at_index_query(*, index: int) -> str:
    return (
        _prefix_block("ontozeolite")
        + f"""
SELECT ?material WHERE {{
  ?material a oz:ZeoliticMaterial .
}}
ORDER BY ?material
LIMIT 1 OFFSET {max(0, int(index))}
"""
    )


def bootstrap_material_cursor(offset: int) -> str:
    if offset < 0:
        return ""
    rows = _execute("ontozeolite", material_at_index_query(index=offset))
    if not rows:
        return ""
    return str(rows[0].get("material", "")).strip()


def material_details_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontozeolite")
        + f"""
SELECT ?material ?label ?frameworkCode ?formula
       ?hasLatticeSystem ?hasGuestSpecies ?hasOccupiableArea ?hasUnitCellVolume
       ?hasChemicalFormula ?isReferenceZeolite WHERE {{
  VALUES ?material {{ {values} }}
  ?material a oz:ZeoliticMaterial .
  OPTIONAL {{ ?material rdfs:label ?label }}
  OPTIONAL {{
    ?fw oz:hasZeoliticMaterial ?material .
    ?fw oz:hasFrameworkCode ?frameworkCode .
  }}
  OPTIONAL {{ ?material oz:hasChemicalFormula ?formula }}
  OPTIONAL {{ ?material oz:hasChemicalFormula ?hasChemicalFormula }}
  OPTIONAL {{ ?material oz:hasLatticeSystem ?hasLatticeSystem }}
  OPTIONAL {{ ?material oz:hasGuestSpecies ?hasGuestSpecies }}
  OPTIONAL {{ ?material oz:hasGuestFormula ?hasGuestFormula }}
  OPTIONAL {{ ?material oz:hasOccupiableArea ?hasOccupiableArea }}
  OPTIONAL {{ ?material oz:hasUnitCellVolume ?hasUnitCellVolume }}
  OPTIONAL {{ ?material oz:isReferenceZeolite ?isReferenceZeolite }}
}}
"""
    )


def count_materials_remote() -> int:
    q = (
        _prefix_block("ontozeolite")
        + """
SELECT (COUNT(DISTINCT ?m) AS ?n) WHERE { ?m a oz:ZeoliticMaterial . }
"""
    )
    rows = _execute("ontozeolite", q)
    try:
        return int(str(rows[0].get("n", "0")))
    except (IndexError, ValueError):
        return 0


def guest_formulas_for_iris_query(iri_list: Sequence[str]) -> str:
    values = " ".join(f"<{iri}>" for iri in iri_list if iri.strip())
    if not values:
        return ""
    return (
        _prefix_block("ontozeolite")
        + f"""
SELECT ?material ?guestFormula WHERE {{
  VALUES ?material {{ {values} }}
  ?material oz:hasGuestFormula ?guestFormula .
}}
"""
    )


def fetch_guest_formulas(iri_list: Sequence[str]) -> Dict[str, str]:
    if not iri_list:
        return {}
    raw = _execute("ontozeolite", guest_formulas_for_iris_query(iri_list))
    out: Dict[str, str] = {}
    for row in raw:
        material = str(row.get("material", "")).strip()
        guest = str(row.get("guestFormula", "")).strip()
        if material and guest:
            out[material] = guest
    return out


def fetch_material_batch(*, limit: int, after_subject: str = "") -> tuple[List[Dict[str, Any]], List[str]]:
    iri_rows = _execute("ontozeolite", material_page_query(limit=limit, after_subject=after_subject))
    page_iris = [str(r.get("material", "")).strip() for r in iri_rows if str(r.get("material", "")).strip()]
    if not page_iris:
        return [], []
    raw = _execute("ontozeolite", material_details_query(page_iris))
    guests = fetch_guest_formulas(page_iris)
    for row in raw:
        material = str(row.get("material", "")).strip()
        if material in guests:
            row["hasGuestFormula"] = guests[material]
    return raw, page_iris


class ZeoliteCorpusStore:
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
            CREATE TABLE IF NOT EXISTS corpus_zeolite_materials (
                material_iri TEXT PRIMARY KEY,
                label TEXT,
                formula TEXT,
                framework_code TEXT,
                indexed_at REAL NOT NULL
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_zeo_label ON corpus_zeolite_materials(label);
            CREATE INDEX IF NOT EXISTS idx_corpus_zeo_fw ON corpus_zeolite_materials(framework_code);

            CREATE TABLE IF NOT EXISTS corpus_zeolite_properties (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                material_iri TEXT NOT NULL,
                property_local TEXT NOT NULL,
                property_value TEXT NOT NULL,
                UNIQUE(material_iri, property_local, property_value)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_zeo_prop ON corpus_zeolite_properties(property_local);

            CREATE TABLE IF NOT EXISTS corpus_zeolite_by_framework (
                framework_code TEXT NOT NULL,
                material_iri TEXT NOT NULL,
                label TEXT,
                formula TEXT,
                UNIQUE(framework_code, material_iri)
            );
            CREATE INDEX IF NOT EXISTS idx_corpus_zeo_fw_mat ON corpus_zeolite_by_framework(framework_code);

            CREATE TABLE IF NOT EXISTS corpus_zeolite_reference (
                framework_code TEXT PRIMARY KEY,
                material_iri TEXT NOT NULL,
                label TEXT
            );
            """
        )
        migrate_cursor_column(self._conn)

    def warm_state(self) -> Dict[str, Any]:
        return warm_state_row(self._conn, MATERIALS_CORPUS_ID)

    def _upsert_material_row(self, row: Dict[str, Any], now: float) -> int:
        material = str(row.get("material", "")).strip()
        if not material:
            return 0
        label = str(row.get("label", "") or "").strip()
        formula = str(row.get("formula", "") or row.get("hasChemicalFormula", "") or "").strip()
        fw = str(row.get("frameworkCode", "") or "").strip().upper()
        self._conn.execute(
            """
            INSERT INTO corpus_zeolite_materials (material_iri, label, formula, framework_code, indexed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(material_iri) DO UPDATE SET
              label=COALESCE(excluded.label, corpus_zeolite_materials.label),
              formula=COALESCE(excluded.formula, corpus_zeolite_materials.formula),
              framework_code=COALESCE(excluded.framework_code, corpus_zeolite_materials.framework_code),
              indexed_at=excluded.indexed_at
            """,
            (material, label or None, formula or None, fw or None, now),
        )
        if fw:
            self._conn.execute(
                """
                INSERT OR REPLACE INTO corpus_zeolite_by_framework
                (framework_code, material_iri, label, formula)
                VALUES (?, ?, ?, ?)
                """,
                (fw, material, label, formula),
            )
        props_added = 0
        for prop in ZEOLITE_PROPS:
            val = row.get(prop) or row.get(f"{prop}Val")
            if val is None or str(val).strip() == "":
                continue
            text = str(val).strip()
            cur = self._conn.execute(
                """
                INSERT OR IGNORE INTO corpus_zeolite_properties
                (material_iri, property_local, property_value)
                VALUES (?, ?, ?)
                """,
                (material, prop, text),
            )
            props_added += cur.rowcount
            if prop == "isReferenceZeolite" and fw and text.lower() in ("true", "1"):
                self._conn.execute(
                    """
                    INSERT OR REPLACE INTO corpus_zeolite_reference (framework_code, material_iri, label)
                    VALUES (?, ?, ?)
                    """,
                    (fw, material, label),
                )
        return 1 + props_added

    def warm_batch(
        self,
        *,
        offset: Optional[int] = None,
        batch_size: int = DEFAULT_BATCH_SIZE,
        total: Optional[int] = None,
    ) -> Dict[str, Any]:
        state = self.warm_state()
        off = int(offset if offset is not None else state.get("offset_next") or 0)
        total_n = int(total or state.get("total_species") or count_materials_remote())
        if state.get("status") != "running" or state.get("total_species") is None:
            ensure_warm_state(self._conn, corpus_id=MATERIALS_CORPUS_ID, total=total_n, batch_size=batch_size)

        cursor = resolve_cursor(
            self._conn, corpus_id=MATERIALS_CORPUS_ID, offset=off, bootstrap_fn=bootstrap_material_cursor
        )
        started = time.perf_counter()
        rows, page_iris = retry_corpus_fetch(
            "zeolite materials",
            lambda: fetch_material_batch(limit=batch_size, after_subject=cursor),
        )
        now = time.time()
        inserted = 0
        for row in rows:
            inserted += self._upsert_material_row(row, now)
        self._conn.commit()
        new_cursor = page_iris[-1] if page_iris else cursor
        done = not page_iris or off + len(page_iris) >= total_n
        advance_warm_state(
            self._conn,
            corpus_id=MATERIALS_CORPUS_ID,
            offset=off,
            species_added=len(page_iris),
            rows_added=inserted,
            batch_size=batch_size,
            total=total_n,
            done=done,
            cursor_subject=new_cursor,
        )
        return {
            "offset": off,
            "materials_in_batch": len(page_iris),
            "rows_upserted": inserted,
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
            "done": done,
            "cursor_subject": new_cursor,
        }

    def search_materials(self, query: str, *, limit: int = 50) -> List[Dict[str, Any]]:
        q = query.strip().lower()
        if not q:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT material_iri, label, formula, framework_code
                FROM corpus_zeolite_materials
                WHERE LOWER(label) LIKE ? OR LOWER(formula) LIKE ?
                   OR LOWER(material_iri) LIKE ? OR UPPER(framework_code) LIKE ?
                LIMIT ?
                """,
                (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q.upper()}%", max(1, int(limit))),
            )
        ]

    def materials_by_framework(self, framework_code: str, *, limit: int = 200) -> List[Dict[str, Any]]:
        code = framework_code.strip().upper()
        if not code:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                """
                SELECT material_iri, label, formula, framework_code
                FROM corpus_zeolite_by_framework
                WHERE framework_code = ?
                LIMIT ?
                """,
                (code, max(1, int(limit))),
            )
        ]

    def reference_zeolite(self, framework_code: str) -> List[Dict[str, Any]]:
        code = framework_code.strip().upper()
        if not code:
            return []
        return [
            dict(r)
            for r in self._conn.execute(
                "SELECT framework_code, material_iri, label FROM corpus_zeolite_reference WHERE framework_code = ?",
                (code,),
            )
        ]

    def query_property(
        self,
        *,
        material_label: str = "",
        framework_code: str = "",
        property_local: str = "",
        value_filter: str = "",
        limit: int = 100,
    ) -> List[Dict[str, Any]]:
        if framework_code.strip() and property_local == "isReferenceZeolite":
            return self.reference_zeolite(framework_code)
        if framework_code.strip() and not property_local:
            return self.materials_by_framework(framework_code, limit=limit)

        clauses = ["1=1"]
        params: List[Any] = []
        if material_label.strip():
            clauses.append(
                "(m.label LIKE ? OR m.formula LIKE ? OR m.material_iri LIKE ?)"
            )
            frag = f"%{material_label.strip()}%"
            params.extend([frag, frag, frag])
        if framework_code.strip():
            clauses.append("m.framework_code = ?")
            params.append(framework_code.strip().upper())
        if property_local.strip():
            clauses.append("p.property_local = ?")
            params.append(property_local.strip())
        if value_filter.strip():
            clauses.append("p.property_value LIKE ?")
            params.append(f"%{value_filter.strip()}%")
        params.append(max(1, int(limit)))
        sql = f"""
            SELECT m.material_iri, m.label, m.framework_code, p.property_local, p.property_value
            FROM corpus_zeolite_materials m
            LEFT JOIN corpus_zeolite_properties p ON p.material_iri = m.material_iri
            WHERE {' AND '.join(clauses)}
            LIMIT ?
        """
        return [dict(r) for r in self._conn.execute(sql, params)]

    def query_property_tsv(self, **kwargs: Any) -> str:
        rows = self.query_property(**kwargs)
        return format_tsv(rows) if rows else "No results"

    def materials_numeric_rows(self, *, limit: int = 5000) -> List[Dict[str, Any]]:
        """Pivot numeric zeolite properties for filter_rows / MQ41 pipelines."""
        numeric_props = (
            "hasOccupiableArea",
            "hasUnitCellVolume",
            "hasLatticeSystem",
        )
        case_lines = []
        for prop in numeric_props:
            key = prop.replace("has", "").replace("UnitCell", "unit_cell_")
            key = "".join(["_" + c.lower() if c.isupper() else c for c in key]).lstrip("_")
            if key.startswith("occupiable"):
                key = "occupiable_area"
            elif key.startswith("unit_cell"):
                key = "unit_cell_volume"
            elif key.startswith("lattice"):
                key = "lattice_system"
            case_lines.append(
                f"MAX(CASE WHEN p.property_local = '{prop}' THEN p.property_value END) AS {key}"
            )
        sql = f"""
            SELECT m.material_iri, m.label, m.framework_code, m.formula,
                   {', '.join(case_lines)}
            FROM corpus_zeolite_materials m
            LEFT JOIN corpus_zeolite_properties p ON p.material_iri = m.material_iri
            GROUP BY m.material_iri
            LIMIT ?
        """
        rows = [dict(r) for r in self._conn.execute(sql, (max(1, int(limit)),))]
        for row in rows:
            for field in ("occupiable_area", "unit_cell_volume"):
                val = row.get(field)
                if val is not None and str(val).strip() != "":
                    try:
                        row[field] = float(str(val).strip())
                    except ValueError:
                        pass
        return rows

    def materials_numeric_rows_tsv(self, *, limit: int = 5000) -> str:
        rows = self.materials_numeric_rows(limit=limit)
        return format_tsv(rows) if rows else "No results"

    def stats(self) -> Dict[str, Any]:
        return {
            "material_rows": int(self._conn.execute("SELECT COUNT(*) FROM corpus_zeolite_materials").fetchone()[0]),
            "property_rows": int(self._conn.execute("SELECT COUNT(*) FROM corpus_zeolite_properties").fetchone()[0]),
            "framework_index_rows": int(
                self._conn.execute("SELECT COUNT(*) FROM corpus_zeolite_by_framework").fetchone()[0]
            ),
            "reference_rows": int(self._conn.execute("SELECT COUNT(*) FROM corpus_zeolite_reference").fetchone()[0]),
            "warm_state": self.warm_state(),
            "endpoint": endpoint("ontozeolite"),
            "sparql_timeout_s": sparql_timeout("ontozeolite"),
        }

    def backfill_properties_batch(self, *, after_iri: str = "", batch_size: int = DEFAULT_BATCH_SIZE) -> Dict[str, Any]:
        started = time.perf_counter()
        rows = self._conn.execute(
            """
            SELECT material_iri FROM corpus_zeolite_materials
            WHERE material_iri > ? ORDER BY material_iri LIMIT ?
            """,
            (after_iri, max(1, int(batch_size))),
        ).fetchall()
        iris = [str(r[0]) for r in rows]
        if not iris:
            return {"materials": 0, "props_added": 0, "done": True, "cursor": after_iri}
        raw = _execute("ontozeolite", material_details_query(iris))
        guests = fetch_guest_formulas(iris)
        for row in raw:
            material = str(row.get("material", "")).strip()
            if material in guests:
                row["hasGuestFormula"] = guests[material]
        now = time.time()
        props_added = 0
        for row in raw:
            props_added += self._upsert_material_row(row, now)
        self._conn.commit()
        return {
            "materials": len(iris),
            "props_added": props_added,
            "done": False,
            "cursor": iris[-1],
            "elapsed_ms": round((time.perf_counter() - started) * 1000),
        }


def backfill_zeolite_properties(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 0,
    delay_seconds: float = WARM_DELAY_SECONDS,
) -> Dict[str, Any]:
    store = ZeoliteCorpusStore()
    batches: List[Dict[str, Any]] = []
    try:
        max_n = max_batches if max_batches > 0 else 10_000
        cursor = ""
        for i in range(max_n):
            batch = store.backfill_properties_batch(after_iri=cursor, batch_size=batch_size)
            batches.append(batch)
            cursor = str(batch.get("cursor") or cursor)
            if batch.get("materials", 0) == 0:
                break
            if i + 1 < max_n and delay_seconds > 0:
                time.sleep(delay_seconds)
        prop_counts = {
            r[0]: r[1]
            for r in store._conn.execute(
                "SELECT property_local, COUNT(*) FROM corpus_zeolite_properties GROUP BY property_local"
            )
        }
        return {"batches": len(batches), "property_counts": prop_counts, "stats": store.stats()}
    finally:
        store.close()


def warm_zeolite_corpus(
    *,
    batch_size: int = DEFAULT_BATCH_SIZE,
    max_batches: int = 1,
    delay_seconds: float = WARM_DELAY_SECONDS,
    skip_health_check: bool = False,
    show_progress: bool = True,
) -> Dict[str, Any]:
    store = ZeoliteCorpusStore()
    results: List[Dict[str, Any]] = []
    errors: List[Dict[str, Any]] = []
    try:
        total = count_materials_remote()
        ensure_warm_state(store._conn, corpus_id=MATERIALS_CORPUS_ID, total=total, batch_size=batch_size)
        try:
            from tqdm import tqdm
        except ImportError:
            tqdm = None
        use_bar = show_progress and tqdm is not None and max_batches > 1
        batch_range = range(max_batches)
        iterator = tqdm(batch_range, desc="zeolite corpus", unit="batch") if use_bar else batch_range
        for i in iterator:
            state = store.warm_state()
            if state.get("status") == "complete":
                break
            if not skip_health_check:
                health = namespace_health_ok("ontozeolite")
                if not health.get("ok"):
                    mark_paused(store._conn, MATERIALS_CORPUS_ID, f"health check failed: {health}")
                    errors.append({"phase": "health", **health})
                    break
            try:
                batch = store.warm_batch(batch_size=batch_size, total=total)
            except Exception as exc:
                mark_paused(store._conn, MATERIALS_CORPUS_ID, str(exc))
                errors.append({"phase": "fetch", "error": str(exc)})
                break
            results.append(batch)
            if batch.get("done"):
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
