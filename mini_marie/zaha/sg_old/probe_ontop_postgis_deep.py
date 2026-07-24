"""
Second look: does sg-old Ontop expose sg-postgis timeseries/dispersion data?

Ontop CAN map PostGIS — we already get buildings/GFA from PostGIS via Ontop.
This probe discovers whether timeseries/ship/concentration tables are in the same OBDA.

Usage:
  python -m mini_marie.zaha.sg_old.probe_ontop_postgis_deep
"""

from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.local_store import db_path, ensure_db
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

OUT = mini_marie_cache_root() / "sg_old" / "probe_ontop_postgis_deep.json"

SHIP = "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320"
SPEED_M = "https://www.theworldavatar.com/kg/ontodispersion/Ship563071320SpeedMeasure"
SHIP_TS = "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"
CO_TS = "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_ee9a9039-0da0-4caa-92b7-57f6c207a05c"
OM_NUM = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue"
OM_VAL = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasValue"
HAS_TS = "https://www.theworldavatar.com/kg/ontotimeseries/hasTimeSeries"


def q(query: str, timeout: int = 90) -> Dict[str, Any]:
    try:
        rows = execute_sparql_get(query, ONTOP_ENDPOINT, timeout=timeout)
        return {"ok": True, "rows": rows, "n": len(rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:500]}


def count(query: str) -> Optional[int]:
    r = q(query)
    if r.get("ok") and r["rows"]:
        for k in ("n", "c", "count"):
            if k in r["rows"][0]:
                return int(float(r["rows"][0][k]))
    return None


def kb_timeseries_hints() -> Dict[str, Any]:
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: Dict[str, Any] = {}
    out["hasRDB"] = conn.execute(
        "SELECT DISTINCT o FROM triples WHERE ns='kb' AND p LIKE '%hasRDB%'"
    ).fetchall()
    out["timeseries_time_unit"] = conn.execute(
        """
        SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%hasTimeUnit%' LIMIT 5
        """
    ).fetchall()
    out["timeseries_hasTime"] = conn.execute(
        """
        SELECT s, o FROM triples WHERE ns='kb' AND p LIKE '%hasTime%' AND s LIKE '%Timeseries_%' LIMIT 5
        """
    ).fetchall()
    out["table_column_preds"] = conn.execute(
        """
        SELECT DISTINCT p FROM triples WHERE ns='kb'
        AND (p LIKE '%hasTable%' OR p LIKE '%hasColumn%' OR p LIKE '%tableName%' OR p LIKE '%hasQuery%')
        """
    ).fetchall()
    out["measure_subject_patterns"] = conn.execute(
        """
        SELECT DISTINCT
          CASE
            WHEN s LIKE '%Ship%Measure%' THEN 'ship_measure'
            WHEN s LIKE '%measure_%' THEN 'ontoems_measure'
            WHEN s LIKE '%gfameasure%' THEN 'gfameasure'
            ELSE 'other'
          END AS pat,
          COUNT(*) AS n
        FROM triples WHERE ns='kb' AND p LIKE '%type%'
        GROUP BY pat
        """
    ).fetchall()
    conn.close()
    return out


def build_report() -> Dict[str, Any]:
    t0 = time.perf_counter()
    report: Dict[str, Any] = {
        "endpoint": ONTOP_ENDPOINT,
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "kb_hints": kb_timeseries_hints(),
        "postgis_via_ontop_confirmed": {},
        "ontology_discovery": {},
        "predicate_discovery": {},
        "iri_lookup": {},
        "pattern_queries": {},
    }

    print("=== PostGIS via Ontop (confirmed working domain) ===", flush=True)
    confirmed = {
        "buildings": "SELECT (COUNT(?b) AS ?n) WHERE { ?b a <http://www.opengis.net/citygml/building/2.0/Building> }",
        "gfameasure_nodes": "SELECT (COUNT(?m) AS ?n) WHERE { ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> }",
        "gfameasure_with_num": f"""
            SELECT (COUNT(?m) AS ?n) WHERE {{
              ?m a <http://www.ontology-of-units-of-measure.org/resource/om-2/Measure> .
              ?m <{OM_NUM}> ?v .
              FILTER(CONTAINS(STR(?m), "gfameasure"))
            }}""",
        "landplot_area_measure": """
            SELECT (COUNT(?m) AS ?n) WHERE {
              ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
              FILTER(CONTAINS(STR(?m), "plotarea") || CONTAINS(STR(?m), "PlotArea"))
            }""",
    }
    for k, query in confirmed.items():
        n = count(query)
        report["postgis_via_ontop_confirmed"][k] = n
        print(f"  {k}: {n}", flush=True)

    print("\n=== Class discovery (timeseries/dispersion/ship) ===", flush=True)
    class_q = """
SELECT ?c (COUNT(?x) AS ?n) WHERE {
  ?x a ?c .
  FILTER(
    CONTAINS(LCASE(STR(?c)), "time") ||
    CONTAINS(LCASE(STR(?c)), "ship") ||
    CONTAINS(LCASE(STR(?c)), "dispersion") ||
    CONTAINS(LCASE(STR(?c)), "concentration") ||
    CONTAINS(LCASE(STR(?c)), "sensor") ||
    CONTAINS(LCASE(STR(?c)), "emission") ||
    CONTAINS(LCASE(STR(?c)), "measure")
  )
} GROUP BY ?c ORDER BY DESC(?n) LIMIT 30
"""
    report["ontology_discovery"]["classes"] = q(class_q)
    for row in (report["ontology_discovery"]["classes"].get("rows") or [])[:12]:
        print(f"  {row.get('c','')[:80]}: {row.get('n')}", flush=True)

    print("\n=== Predicate discovery ===", flush=True)
    pred_q = """
SELECT ?p (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(
    CONTAINS(LCASE(STR(?p)), "timeseries") ||
    CONTAINS(LCASE(STR(?p)), "hasrdb") ||
    CONTAINS(LCASE(STR(?p)), "speed") ||
    CONTAINS(LCASE(STR(?p)), "concentration") ||
    CONTAINS(LCASE(STR(?p)), "dispersion") ||
    CONTAINS(LCASE(STR(?p)), "ship") ||
    CONTAINS(LCASE(STR(?p)), "pollutant") ||
    CONTAINS(LCASE(STR(?p)), "emission")
  )
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 25
"""
    report["predicate_discovery"] = q(pred_q)
    for row in (report["predicate_discovery"].get("rows") or [])[:10]:
        print(f"  {row.get('p','')[:90]}: {row.get('n')}", flush=True)

    print("\n=== Direct IRI lookup on Ontop ===", flush=True)
    iri_queries = {
        "ship_all_preds": f"SELECT ?p ?o WHERE {{ <{SHIP}> ?p ?o }} LIMIT 20",
        "speed_measure_preds": f"SELECT ?p ?o WHERE {{ <{SPEED_M}> ?p ?o }} LIMIT 20",
        "ship_ts_preds": f"SELECT ?p ?o WHERE {{ <{SHIP_TS}> ?p ?o }} LIMIT 20",
        "co_ts_preds": f"SELECT ?p ?o WHERE {{ <{CO_TS}> ?p ?o }} LIMIT 20",
        "ship_ts_ask": f"ASK {{ <{SHIP_TS}> ?p ?o }}",
        "gfameasure_1_preds": """
            SELECT ?p ?o WHERE {
              <https://www.theworldavatar.com/kg/landplot/gfameasure/95815> ?p ?o .
            } LIMIT 10""",
    }
    for name, query in iri_queries.items():
        report["iri_lookup"][name] = q(query)
        n = report["iri_lookup"][name].get("n", 0)
        print(f"  {name}: {n} rows" if report["iri_lookup"][name].get("ok") else f"  {name}: ERR", flush=True)

    print("\n=== Pattern queries (alternate mappings) ===", flush=True)
    patterns = {
        "ship_uri_pattern": """
SELECT ?s ?p ?o WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(STR(?s), "Ship563071320"))
} LIMIT 15""",
        "timeseries_uri_pattern": """
SELECT ?s ?p ?o WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(STR(?s), "Timeseries_c3389874"))
} LIMIT 15""",
        "measure_ship_pattern": """
SELECT ?m ?v WHERE {
  ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
  FILTER(CONTAINS(STR(?m), "Ship") && CONTAINS(STR(?m), "Speed"))
} LIMIT 5""",
        "hasTimeSeries_any": f"""
SELECT ?m ?ts WHERE {{
  ?m <{HAS_TS}> ?ts .
}} LIMIT 10""",
        "timeseries_hasTime": """
SELECT ?ts ?t WHERE {
  ?ts <http://www.w3.org/2006/time#hasTime> ?t .
} LIMIT 5""",
        "concentration_measure_any": """
SELECT ?m ?v WHERE {
  ?m <http://www.ontology-of-units-of-measure.org/resource/om-2/hasNumericalValue> ?v .
  FILTER(CONTAINS(LCASE(STR(?m)), "concentration") || CONTAINS(LCASE(STR(?m)), "co"))
} LIMIT 5""",
        "virtual_sensor_any": """
SELECT ?vs ?p ?o WHERE {
  ?vs a ?t .
  FILTER(CONTAINS(LCASE(STR(?t)), "virtualsensor") || CONTAINS(LCASE(STR(?vs)), "virtualsensor"))
  ?vs ?p ?o .
} LIMIT 10""",
        "dispersion_matrix": """
SELECT ?dm ?p ?o WHERE {
  ?dm a ?t .
  FILTER(CONTAINS(LCASE(STR(?t)), "dispersionmatrix"))
  ?dm ?p ?o .
} LIMIT 10""",
        "carpark_in_ontop": """
SELECT (COUNT(?c) AS ?n) WHERE {
  ?c a <https://www.theworldavatar.com/kg/ontocarpark/Carpark> .
}""",
        "emission_in_ontop": """
SELECT (COUNT(?e) AS ?n) WHERE {
  ?e a ?t .
  FILTER(CONTAINS(LCASE(STR(?t)), "emission"))
}""",
        "subject_uri_timeseries": """
SELECT DISTINCT ?s WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(STR(?s), "ontotimeseries/Timeseries"))
} LIMIT 10""",
        "subject_uri_ship": """
SELECT DISTINCT ?s WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(STR(?s), "ontodispersion/Ship"))
} LIMIT 10""",
        # PostGIS geometry — maybe concentration grid stored as raster/geometry
        "geometry_asWKT_any": """
SELECT ?g ?wkt WHERE {
  ?g <http://www.opengis.net/ont/geosparql/asWKT> ?wkt .
  FILTER(CONTAINS(LCASE(STR(?wkt)), "point") || CONTAINS(LCASE(STR(?wkt)), "polygon"))
} LIMIT 3""",
        "hasGeometry_chain": """
SELECT ?s ?geom ?wkt WHERE {
  ?s <https://www.theworldavatar.com/kg/ontogeosparql/hasGeometry> ?geom .
  ?geom <http://www.opengis.net/ont/geosparql/asWKT> ?wkt .
} LIMIT 3""",
    }
    for name, query in patterns.items():
        report["pattern_queries"][name] = q(query, timeout=120)
        r = report["pattern_queries"][name]
        tag = r.get("n", 0) if r.get("ok") else "ERR"
        print(f"  {name}: {tag}", flush=True)

    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    report["conclusion"] = _conclude(report)
    return report


def _conclude(report: Dict[str, Any]) -> Dict[str, str]:
    confirmed = report.get("postgis_via_ontop_confirmed", {})
    classes = (report.get("ontology_discovery", {}).get("classes") or {}).get("rows") or []
    has_ship_class = any("ship" in (r.get("c") or "").lower() for r in classes)
    has_ts_class = any("timeseries" in (r.get("c") or "").lower() for r in classes)
    iri_ship = (report.get("iri_lookup", {}).get("ship_all_preds") or {}).get("n", 0)
    iri_ts = (report.get("iri_lookup", {}).get("ship_ts_preds") or {}).get("n", 0)
    has_ts_links = (report.get("pattern_queries", {}).get("hasTimeSeries_any") or {}).get("n", 0)

    if confirmed.get("buildings") and not has_ts_links and iri_ts == 0:
        return {
            "ontop_postgis": "YES for buildings/landplot/gfameasure tables",
            "ontop_timeseries_postgis": "NO — ship/timeseries IRIs and hasTimeSeries not in Ontop OBDA",
            "implication": "sg-postgis is used by multiple table sets; only city/plot OBDA is loaded on public /ontop/sparql/",
            "next_step": "Need separate Ontop mapping or internal sg-ontop OBDA that includes ontotimeseries tables",
        }
    return {"status": "partial — review probe_ontop_postgis_deep.json"}


def main() -> int:
    report = build_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nConclusion: {report.get('conclusion')}")
    print(f"Wrote {OUT} ({report['elapsed_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
