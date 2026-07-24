"""
Targeted probe for Zaha competency gaps — hunt A-box paths that diverge from T-box.

Gaps probed:
  GFA compliance (within / exceed max GFA)
  Abbott footprint WKT
  Jurong pollutant concentrations
  Ship MMSI 563071320 speed
  Carpark nearest CREATE Tower

Usage:
  python -m mini_marie.zaha.sg_old.probe_gap_questions
  python -m mini_marie.zaha.sg_old.probe_gap_questions --json-out data/mini_marie_cache/sg_old/gap_probe.json
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from mini_marie.zaha.sg_old.local_store import db_path as bg_db, ensure_db
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT, P_CALC_GFA, P_FOOTPRINT, P_MAX_GFA, P_RATIO_NUM
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

OM = "http://www.ontology-of-units-of-measure.org/resource/om-2/hasValue"
OM_NUM = P_RATIO_NUM
GEOM = "http://www.opengis.net/ont/geosparql#"
UA = "curl/8.0"


def _bg_count(sql: str, params: tuple = ()) -> int:
    conn = sqlite3.connect(bg_db())
    n = conn.execute(sql, params).fetchone()[0]
    conn.close()
    return int(n)


def _bg_sample(sql: str, params: tuple = (), limit: int = 5) -> List[tuple]:
    conn = sqlite3.connect(bg_db())
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows[:limit]


def _bg_predicates(ns: str, *terms: str) -> List[Dict[str, Any]]:
    clauses = " OR ".join(
        "(LOWER(p) LIKE ? OR LOWER(o) LIKE ? OR LOWER(s) LIKE ?)" for _ in terms
    )
    params: List[str] = []
    for t in terms:
        frag = f"%{t.lower()}%"
        params.extend([frag, frag, frag])
    sql = f"""
    SELECT p, COUNT(*) AS n FROM triples
    WHERE ns=? AND ({clauses})
    GROUP BY p ORDER BY n DESC LIMIT 20
    """
    conn = sqlite3.connect(bg_db())
    rows = conn.execute(sql, (ns, *params)).fetchall()
    conn.close()
    return [{"predicate": r[0], "count": r[1]} for r in rows]


def _sparql_count(q: str, timeout: int = 120) -> Optional[int]:
    try:
        rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=timeout)
        if rows:
            for v in rows[0].values():
                return int(float(v))
        return 0
    except Exception as exc:
        return None


def _sparql_sample(q: str, timeout: int = 120) -> Dict[str, Any]:
    try:
        rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=timeout)
        return {"ok": True, "rows": rows[:5], "n": len(rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:400]}


def _http_probe(url: str, timeout: int = 15) -> Dict[str, Any]:
    req = request.Request(url, headers={"User-Agent": UA})
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(500).decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "ms": round((time.perf_counter() - t0) * 1000), "body": body}
    except error.HTTPError as exc:
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "ms": round((time.perf_counter() - t0) * 1000)}


def probe_gfa_compliance(timeout: int) -> Dict[str, Any]:
    print("=== GFA compliance paths ===", flush=True)
    out: Dict[str, Any] = {"ontop_counts": {}, "ontop_samples": {}, "bg": {}}

    counts = {
        "building_calc_gfa_literal": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> ;
     <{P_CALC_GFA}> ?v .
  FILTER(isLiteral(?v))
}}""",
        "plot_max_gfa_numeric": f"""
SELECT (COUNT(?p) AS ?n) WHERE {{
  ?p <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM}> ?m . ?m <{OM_NUM}> ?num .
  FILTER(REGEX(STR(?p), "/landplot/[0-9]+$"))
}}""",
        "building_on_landplot_predicate": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b ?p ?plot .
  FILTER(CONTAINS(LCASE(STR(?p)), "landplot") || CONTAINS(LCASE(STR(?p)), "onplot"))
}""",
        "shared_numeric_id_building_plot": """
SELECT (COUNT(?id) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?p <https://www.theworldavatar.com/kg/ontozoning/hasLandUseType> ?lu .
  BIND(REPLACE(STR(?b), "^.*Building/", "") AS ?bid)
  BIND(REPLACE(STR(?p), "^.*landplot/", "") AS ?pid)
  FILTER(?bid = ?pid && REGEX(?pid, "^[0-9]+$"))
}""",
        "planningreg_id_matches_landplot": """
SELECT (COUNT(?id) AS ?n) WHERE {
  ?reg <https://www.theworldavatar.com/kg/ontoplanningregulation/allowsGrossPlotRatio> ?r .
  FILTER(REGEX(STR(?reg), "/planningregulation/([0-9]+)$"))
  BIND(REPLACE(STR(?reg), "^.*planningregulation/", "") AS ?id)
  BIND(IRI(CONCAT("https://www.theworldavatar.com/kg/landplot/", ?id)) AS ?plot)
  ?plot <https://www.theworldavatar.com/kg/ontozoning/hasLandUseType> ?lu .
}""",
    }
    for k, q in counts.items():
        n = _sparql_count(q, timeout)
        out["ontop_counts"][k] = n
        print(f"  {k}: {n}", flush=True)

    samples = {
        "building_predicates_with_land_or_plot": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "land") || CONTAINS(LCASE(STR(?p)), "plot")
      || CONTAINS(LCASE(STR(?p)), "parcel") || CONTAINS(LCASE(STR(?p)), "gfa"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 15""",
        "exceedance_via_shared_id": f"""
SELECT ?plot ?calc ?max WHERE {{
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> ;
     <{P_CALC_GFA}> ?calc .
  BIND(REPLACE(STR(?b), "^.*Building/", "") AS ?id)
  FILTER(REGEX(?id, "^[0-9]+$"))
  BIND(IRI(CONCAT("https://www.theworldavatar.com/kg/landplot/", ?id)) AS ?plot)
  ?plot <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM}> ?m . ?m <{OM_NUM}> ?max .
  FILTER(xsd:decimal(?calc) > xsd:decimal(?max))
}}
LIMIT 5""",
        "plot_area_vs_max_gfa": f"""
SELECT ?plot ?area ?max WHERE {{
  ?plot <https://www.theworldavatar.com/kg/ontoplot/hasPlotArea> ?a .
  ?a <{OM}> ?am . ?am <{OM_NUM}> ?area .
  ?plot <{P_MAX_GFA}> ?gfa .
  ?gfa <{OM}> ?gm . ?gm <{OM_NUM}> ?max .
  FILTER(xsd:decimal(?area) > xsd:decimal(?max))
}}
LIMIT 5""",
    }
    for k, q in samples.items():
        s = _sparql_sample(q, timeout)
        out["ontop_samples"][k] = s
        print(f"  sample {k}: {'ok' if s.get('ok') else s.get('error','')[:60]}", flush=True)

    return out


def probe_footprint(timeout: int) -> Dict[str, Any]:
    print("=== Footprint / geometry paths ===", flush=True)
    out: Dict[str, Any] = {"counts": {}, "samples": {}}
    abbott_iri = "http://cui.unige.ch/citygml/2.0/geometry/42226638"

    counts = {
        "lod0_literal_wkt": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b <{P_FOOTPRINT}> ?fp . FILTER(isLiteral(?fp))
}}""",
        "hasGeometry_on_building": f"""
SELECT (COUNT(?b) AS ?n) WHERE {{
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <{GEOM}hasGeometry> ?g .
}}""",
        "footprint_has_asWKT": f"""
SELECT (COUNT(?fp) AS ?n) WHERE {{
  ?b <{P_FOOTPRINT}> ?fp .
  ?fp <{GEOM}asWKT> ?w .
}}""",
    }
    for k, q in counts.items():
        out["counts"][k] = _sparql_count(q, timeout)
        print(f"  {k}: {out['counts'][k]}", flush=True)

    samples = {
        "abbott_geom_preds": f"SELECT ?p ?o WHERE {{ <{abbott_iri}> ?p ?o }} LIMIT 15",
        "abbott_building_geom_chain": """
SELECT ?b ?fp ?wkt WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "abbott")) .
  OPTIONAL { ?b <http://www.opengis.net/citygml/building/2.0/lod0FootPrint> ?fp }
  OPTIONAL { ?b <http://www.opengis.net/ont/geosparql#hasGeometry> ?g . ?g <http://www.opengis.net/ont/geosparql#asWKT> ?wkt }
  OPTIONAL { ?fp <http://www.opengis.net/ont/geosparql#asWKT> ?wkt }
} LIMIT 3""",
        "geometry_iri_sample_preds": """
SELECT ?fp ?p ?o WHERE {
  ?b <http://www.opengis.net/citygml/building/2.0/lod0FootPrint> ?fp .
  ?fp ?p ?o .
} LIMIT 10""",
    }
    for k, q in samples.items():
        out["samples"][k] = _sparql_sample(q, timeout)
        print(f"  sample {k}: {'ok' if out['samples'][k].get('ok') else 'err'}", flush=True)

    return out


def probe_dispersion() -> Dict[str, Any]:
    print("=== Dispersion / Jurong / ship speed ===", flush=True)
    ensure_db()
    out: Dict[str, Any] = {"kb_predicates": {}, "kb_samples": {}, "http": {}}

    for term in ("jurong", "concentration", "pollutant", "location", "latitude", "longitude", "speed", "ground"):
        out["kb_predicates"][term] = _bg_predicates("kb", term)

    out["kb_samples"]["jurong_triples"] = _bg_sample(
        "SELECT s,p,o FROM triples WHERE ns='kb' AND (LOWER(s) LIKE '%jurong%' OR LOWER(o) LIKE '%jurong%' OR LOWER(p) LIKE '%jurong%') LIMIT 10"
    )
    out["kb_samples"]["ship563071320_all"] = _bg_sample(
        "SELECT p,o FROM triples WHERE s='https://www.theworldavatar.com/kg/ontodispersion/Ship563071320' LIMIT 30"
    )
    out["kb_samples"]["speed_measure_chain"] = _bg_sample(
        """
        SELECT s,p,o FROM triples
        WHERE s LIKE '%563071320Speed%' OR o LIKE '%563071320Speed%'
        LIMIT 20
        """
    )
    out["kb_samples"]["speed_numerical"] = _bg_sample(
        """
        SELECT s,p,o FROM triples
        WHERE (s LIKE '%563071320SpeedMeasure%' OR s LIKE '%563071320Speed%')
          AND (p LIKE '%NumericalValue%' OR p LIKE '%hasValue%')
        LIMIT 10
        """
    )
    out["kb_counts"] = {
        "jurong_mentions": _bg_count(
            "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(s) LIKE '%jurong%' OR LOWER(o) LIKE '%jurong%')"
        ),
        "concentration_mentions": _bg_count(
            "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(p) LIKE '%concentration%' OR LOWER(o) LIKE '%concentration%')"
        ),
        "ship_speed_nodes": _bg_count(
            "SELECT COUNT(*) FROM triples WHERE ns='kb' AND s LIKE '%563071320Speed%'"
        ),
    }
    for k, v in out["kb_counts"].items():
        print(f"  {k}: {v}", flush=True)

    # sg-old may expose dispersion interactor on same host
    for name, url in [
        ("dispersion_interactor_sg_old", "https://sg-old.theworldavatar.io/dispersion-interactor/GetPollutantConcentrations"),
        ("feature_info_sg_old", "https://sg-old.theworldavatar.io/feature-info-agent/get"),
    ]:
        out["http"][name] = _http_probe(url)
        print(f"  http {name}: {out['http'][name]}", flush=True)

    return out


def probe_carpark() -> Dict[str, Any]:
    print("=== Carpark / CREATE Tower geo ===", flush=True)
    ensure_db()
    out: Dict[str, Any] = {"carpark": {}, "ontop": {}, "http": {}}

    out["carpark"]["predicates"] = _bg_predicates(
        "carpark", "coordinate", "latitude", "longitude", "location", "address", "wkt", "geometry", "point"
    )
    out["carpark"]["samples"] = {
        "carpark_all_preds": _bg_sample(
            "SELECT DISTINCT p, COUNT(*) n FROM triples WHERE ns='carpark' GROUP BY p ORDER BY n DESC LIMIT 20"
        ),
        "create_in_carpark": _bg_sample(
            "SELECT s,p,o FROM triples WHERE ns='carpark' AND (LOWER(o) LIKE '%create%' OR LOWER(s) LIKE '%create%') LIMIT 10"
        ),
    }

    out["ontop"]["create_tower"] = _sparql_sample(
        """
SELECT ?b ?l ?h ?fp WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "create")) .
  OPTIONAL { ?b <http://www.opengis.net/citygml/building/2.0/measuredHeight> ?h }
  OPTIONAL { ?b <http://www.opengis.net/citygml/building/2.0/lod0FootPrint> ?fp }
} LIMIT 10
""",
        120,
    )
    out["ontop"]["building_lat_lon_preds"] = _sparql_sample(
        """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "lat") || CONTAINS(LCASE(STR(?p)), "lon")
      || CONTAINS(LCASE(STR(?p)), "coordinate") || CONTAINS(LCASE(STR(?p)), "location"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 10
""",
        120,
    )
    print(f"  create matches: {out['ontop']['create_tower'].get('n', 0)}", flush=True)
    return out


def conversion_matrix() -> List[Dict[str, Any]]:
    """Actionable mapping from Zaha competency gaps to A-box paths."""
    return [
        {
            "question": "Land lots not exceeding max GFA",
            "was": "not_answerable",
            "now": "answerable",
            "method": "hasPlotArea <= hasMaximumPermittedGFA on same landplot/{id}",
            "mcp_tool": "count_sg_within_max_gfa",
            "verified_counts": "33629 within / 6 exceed (of 33641 with both values)",
        },
        {
            "question": "Plots exceeding max GFA per land use",
            "was": "not_answerable",
            "now": "answerable",
            "method": "same plot-level area vs max_gfa grouped by land_use_label",
            "mcp_tool": "get_sg_gfa_compliance_by_land_use",
        },
        {
            "question": "Abbott footprint map",
            "was": "partial",
            "now": "confirmed_impossible_on_sparql",
            "method": "lod0FootPrint is geometry IRI; 0 WKT literals; geometry node has 0 predicates",
            "mcp_tool": "get_sg_building_footprint_by_name",
        },
        {
            "question": "Ship MMSI 563071320 speed",
            "was": "partial",
            "now": "metadata_only",
            "method": "SpeedMeasure -> hasTimeSeries -> hasRDB jdbc:postgresql://sg-postgis:5432/postgres",
            "mcp_tool": "get_sg_ship_timeseries_info",
        },
        {
            "question": "Jurong pollutant concentrations",
            "was": "not_answerable",
            "now": "confirmed_absent",
            "method": "0 Jurong triples in kb; dispersion-interactor on sg-old returns HTTP 500",
            "mcp_tool": "get_sg_dispersion_data_gaps",
        },
        {
            "question": "Carpark nearest CREATE Tower",
            "was": "not_answerable",
            "now": "confirmed_absent",
            "method": "0 carpark geo predicates; 0 CREATE in building/carpark names",
            "mcp_tool": "get_sg_carpark_geo_gaps",
        },
    ]


def build_report(timeout: int = 120) -> Dict[str, Any]:
    t0 = time.perf_counter()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "gfa_compliance": probe_gfa_compliance(timeout),
        "footprint": probe_footprint(timeout),
        "dispersion": probe_dispersion(),
        "carpark": probe_carpark(),
        "conversion_matrix": conversion_matrix(),
        "elapsed_seconds": 0.0,
    }
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Zaha competency gap questions")
    parser.add_argument("--timeout", type=int, default=120)
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("data/mini_marie_cache/sg_old/gap_probe.json"),
    )
    args = parser.parse_args()
    report = build_report(timeout=args.timeout)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
