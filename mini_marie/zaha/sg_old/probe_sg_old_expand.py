"""
Keep probing sg-old — discover namespaces, HTTP agents, A-box paths beyond T-box.

Each run appends findings; safe to re-run after cache warms.

Usage:
  python -m mini_marie.zaha.sg_old.probe_sg_old_expand
  python -m mini_marie.zaha.sg_old.probe_sg_old_expand --section http
  python -m mini_marie.zaha.sg_old.probe_sg_old_expand --section kb --section ontop
"""

from __future__ import annotations

import argparse
import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib import error, parse, request

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.local_store import db_path as bg_db, ensure_db
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"

# Candidate Blazegraph namespaces (from stack configs + ontology prefixes)
BG_NAMESPACE_CANDIDATES = [
    "carpark", "kb", "plot", "company", "dispersion", "ontocompany", "ontobuiltenv",
    "ontoplot", "ontozoning", "ontodispersion", "ontotimeseries", "ontocarpark",
    "rdf", "spatial", "geo", "building", "landplot", "sg", "singapore",
]

# HTTP paths seen in Zaha / TWA stack configs
HTTP_PATHS = [
    "/ontop/sparql/",
    "/ontop/ui/sparql",
    "/blazegraph/",
    "/blazegraph/namespace/kb/sparql",
    "/dispersion-interactor/GetPollutantConcentrations",
    "/feature-info-agent/get",
    "/timeseries-agent/query",
    "/stack-data-uploader/",
    "/access-agent/",
    "/visualisation/",
    "/landplot-agent/",
    "/carpark-agent/",
    "/JPSAccessAgent/",
    "/DerivationAgent/",
    "/MetainformationAgent/",
]

DISPERSION_POST_BODIES = [
    {},
    {"location": "Jurong Island"},
    {"location": "Jurong Island, Singapore"},
    {"latitude": 1.29, "longitude": 103.71},
    {"pollutant": "NO2"},
    {"species": "NO2", "lat": 1.29, "lon": 103.71},
]

FEATURE_INFO_QUERIES = [
    {},
    {"uri": "https://www.theworldavatar.com/kg/Building/53ecd194-3dab-4e12-9369-cbb86007882a"},
    {"iri": "http://cui.unige.ch/citygml/2.0/geometry/42226638"},
    {"geometry": "http://cui.unige.ch/citygml/2.0/geometry/42226638"},
    {"query": "CREATE Tower"},
]


def _http(
    url: str,
    *,
    method: str = "GET",
    data: Optional[bytes] = None,
    headers: Optional[Dict[str, str]] = None,
    timeout: float = 20,
) -> Dict[str, Any]:
    hdrs = {"User-Agent": UA, **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(2000).decode("utf-8", errors="replace")
            return {
                "ok": True,
                "status": resp.status,
                "ms": round((time.perf_counter() - t0) * 1000),
                "content_type": resp.headers.get("Content-Type"),
                "body_preview": body[:500],
            }
    except error.HTTPError as exc:
        body = exc.read(500).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body_preview": body[:300]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "ms": round((time.perf_counter() - t0) * 1000)}


def _sparql_ask(endpoint: str, timeout: int = 15) -> Dict[str, Any]:
    url = endpoint + ("&" if "?" in endpoint else "?") + parse.urlencode({"query": "ASK { }"})
    return _http(url, timeout=timeout)


def probe_blazegraph_namespaces(timeout: int) -> Dict[str, Any]:
    print("=== Blazegraph namespace discovery ===", flush=True)
    out: Dict[str, Any] = {}
    for ns in BG_NAMESPACE_CANDIDATES:
        ep = f"{HOST}/blazegraph/namespace/{ns}/sparql"
        r = _sparql_ask(ep, timeout=timeout)
        out[ns] = r
        tag = "OK" if r.get("ok") else r.get("http") or r.get("error", "?")[:30]
        print(f"  {ns}: {tag}", flush=True)
    return out


def probe_http_surface(timeout: int) -> Dict[str, Any]:
    print("=== HTTP path surface ===", flush=True)
    out: Dict[str, Any] = {"get_paths": {}, "dispersion_post": {}, "feature_info": {}}
    for path in HTTP_PATHS:
        url = HOST + path
        out["get_paths"][path] = _http(url, timeout=timeout)
        print(f"  GET {path}: {out['get_paths'][path].get('status') or out['get_paths'][path].get('http') or 'err'}", flush=True)

    base = f"{HOST}/dispersion-interactor/GetPollutantConcentrations"
    for i, body in enumerate(DISPERSION_POST_BODIES):
        payload = json.dumps(body).encode()
        out["dispersion_post"][str(i)] = _http(
            base,
            method="POST",
            data=payload,
            headers={"Content-Type": "application/json"},
            timeout=timeout,
        )

    fi_base = f"{HOST}/feature-info-agent/get"
    for i, params in enumerate(FEATURE_INFO_QUERIES):
        if params:
            url = fi_base + "?" + parse.urlencode({k: str(v) for k, v in params.items()})
        else:
            url = fi_base
        out["feature_info"][str(i)] = _http(url, timeout=timeout)
    return out


def probe_geometry_iris() -> Dict[str, Any]:
    print("=== External geometry IRI fetch ===", flush=True)
    iris = [
        "http://cui.unige.ch/citygml/2.0/geometry/42226638",
        "http://cui.unige.ch/citygml/2.0/geometry/1780121",
    ]
    out = {}
    for iri in iris:
        for url in [iri, iri + ".wkt", iri + "?format=wkt", iri + "/wkt"]:
            out[url] = _http(url, timeout=15)
        print(f"  {iri}: {out[iri].get('status') or out[iri].get('http') or 'err'}", flush=True)
    return out


def probe_kb_deep() -> Dict[str, Any]:
    print("=== kb SQLite deep chains ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(bg_db())
    out: Dict[str, Any] = {"counts": {}, "samples": {}}

    count_queries = {
        "timeseries_entities": "SELECT COUNT(DISTINCT s) FROM triples WHERE ns='kb' AND s LIKE '%ontotimeseries/Timeseries%'",
        "hasRDB_links": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND p LIKE '%hasRDB%'",
        "location_property_nodes": "SELECT COUNT(DISTINCT s) FROM triples WHERE ns='kb' AND s LIKE '%Location%'",
        "lat_property_nodes": "SELECT COUNT(DISTINCT s) FROM triples WHERE ns='kb' AND s LIKE '%Lat%'",
        "speed_property_nodes": "SELECT COUNT(DISTINCT s) FROM triples WHERE ns='kb' AND s LIKE '%Speed%'",
        "grid_or_cell": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(s) LIKE '%grid%' OR LOWER(o) LIKE '%grid%' OR LOWER(s) LIKE '%cell%')",
        "pollution_mentions": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(o) LIKE '%pollut%' OR LOWER(p) LIKE '%pollut%')",
        "simulation_mentions": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(s) LIKE '%simul%' OR LOWER(o) LIKE '%simul%')",
        "island_mentions": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND LOWER(o) LIKE '%island%'",
        "jurong_mentions": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(s) LIKE '%jurong%' OR LOWER(o) LIKE '%jurong%')",
        "create_mentions": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND (LOWER(o) LIKE '%create%' OR LOWER(s) LIKE '%create%')",
        "numerical_value_triples": "SELECT COUNT(*) FROM triples WHERE ns='kb' AND p LIKE '%hasNumericalValue%'",
    }
    for k, q in count_queries.items():
        out["counts"][k] = conn.execute(q).fetchone()[0]
        print(f"  {k}: {out['counts'][k]}", flush=True)

    samples = {
        "timeseries_rdb_urls": """
            SELECT DISTINCT o FROM triples WHERE ns='kb' AND p LIKE '%hasRDB%' LIMIT 15
        """,
        "location_measure_chain": """
            SELECT s,p,o FROM triples
            WHERE ns='kb' AND s LIKE '%Ship563071320Location%'
            LIMIT 20
        """,
        "lat_measure_chain": """
            SELECT s,p,o FROM triples
            WHERE ns='kb' AND s LIKE '%Ship563071320LatMeasure%'
            LIMIT 10
        """,
        "grid_samples": """
            SELECT s,p,o FROM triples WHERE ns='kb'
            AND (LOWER(s) LIKE '%grid%' OR LOWER(o) LIKE '%grid%') LIMIT 10
        """,
        "pollutant_id_samples": """
            SELECT s,p,o FROM triples WHERE ns='kb' AND p LIKE '%hasPollutantID%' LIMIT 8
        """,
        "derivation_samples": """
            SELECT s,p,o FROM triples WHERE ns='kb' AND (s LIKE '%Derivation%' OR p LIKE '%derivation%') LIMIT 8
        """,
        "island_literals": """
            SELECT s,p,o FROM triples WHERE ns='kb' AND LOWER(o) LIKE '%island%' LIMIT 10
        """,
    }
    for k, q in samples.items():
        out["samples"][k] = conn.execute(q).fetchall()
    conn.close()
    return out


def _ontop_count(q: str, timeout: int) -> Any:
    try:
        rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=timeout)
        if rows:
            return list(rows[0].values())[0]
        return 0
    except Exception as exc:
        return f"ERR:{str(exc)[:80]}"


def probe_ontop_deep(timeout: int) -> Dict[str, Any]:
    print("=== Ontop deep A-box hunts ===", flush=True)
    out: Dict[str, Any] = {"counts": {}, "samples": {}}

    counts = {
        "buildings_with_address_label": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <http://www.theworldavatar.com/kg/ontocompany/hasAddress> ?a .
  ?a <http://www.w3.org/2000/01/rdf-schema#label> ?l .
}""",
        "buildings_label_rdfs": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <http://www.w3.org/2000/01/rdf-schema#label> ?l .
}""",
        "carpark_in_ontop": """
SELECT (COUNT(?c) AS ?n) WHERE {
  ?c a <https://www.theworldavatar.com/kg/ontocarpark/Carpark> .
}""",
        "pred_hasGeometry": """
SELECT (COUNT(?x) AS ?n) WHERE { ?x <http://www.opengis.net/ont/geosparql#hasGeometry> ?g }""",
        "pred_sfWithin": """
SELECT (COUNT(?x) AS ?n) WHERE {
  ?x ?p ?y .
  FILTER(CONTAINS(LCASE(STR(?p)), "within") || CONTAINS(LCASE(STR(?p)), "intersect"))
}""",
        "building_name_create": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "create") || CONTAINS(LCASE(STR(?l)), "nus") || CONTAINS(LCASE(STR(?l)), "tower"))
}""",
    }
    for k, q in counts.items():
        out["counts"][k] = _ontop_count(q, timeout)
        print(f"  {k}: {out['counts'][k]}", flush=True)

    samples = {
        "create_tower_candidates": """
SELECT ?b ?l WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "create") || CONTAINS(LCASE(STR(?l)), "nus") || CONTAINS(LCASE(STR(?l)), "university"))
} LIMIT 15""",
        "distinct_gfa_predicates": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "gfa"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 15""",
        "distinct_geometry_predicates": """
SELECT DISTINCT ?p (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "geom") || CONTAINS(LCASE(STR(?p)), "wkt")
      || CONTAINS(LCASE(STR(?p)), "foot") || CONTAINS(LCASE(STR(?p)), "coord"))
} GROUP BY ?p ORDER BY DESC(?n) LIMIT 15""",
        "abbott_all_facility_labels": """
SELECT ?l WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "abbott"))
}""",
    }
    for k, q in samples.items():
        try:
            rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=timeout)
            out["samples"][k] = {"ok": True, "rows": rows[:10]}
        except Exception as exc:
            out["samples"][k] = {"ok": False, "error": str(exc)[:300]}
        print(f"  sample {k}: {'ok' if out['samples'][k].get('ok') else 'err'}", flush=True)
    return out


def probe_carpark_text_mining() -> Dict[str, Any]:
    print("=== Carpark text / ID mining ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(bg_db())
    out = {
        "agency_literals": conn.execute(
            """
            SELECT DISTINCT o FROM triples
            WHERE ns='carpark' AND p LIKE '%hasAgency%'
            LIMIT 20
            """
        ).fetchall(),
        "id_literals": conn.execute(
            "SELECT DISTINCT o FROM triples WHERE ns='carpark' AND p LIKE '%hasID%' LIMIT 20"
        ).fetchall(),
        "label_with_road_or_street": conn.execute(
            """
            SELECT s,o FROM triples WHERE ns='carpark' AND p LIKE '%label%'
            AND (LOWER(o) LIKE '%road%' OR LOWER(o) LIKE '%street%' OR LOWER(o) LIKE '%avenue%')
            LIMIT 15
            """
        ).fetchall(),
    }
    conn.close()
    print(f"  agencies: {len(out['agency_literals'])}, labels with road: {len(out['label_with_road_or_street'])}", flush=True)
    return out


def next_probe_hints(report: Dict[str, Any]) -> List[str]:
    hints: List[str] = []
    kb = report.get("kb_deep", {}).get("counts", {})
    if kb.get("numerical_value_triples", 0) > 0:
        hints.append("kb has hasNumericalValue triples — mine emission/measure nodes for Jurong-adjacent data")
    if kb.get("grid_or_cell", 0) > 0:
        hints.append("kb mentions grid/cell — probe derivation→grid→concentration chain")
    http = report.get("http_surface", {}).get("get_paths", {})
    for path, r in http.items():
        if r.get("ok") and r.get("status") == 200:
            hints.append(f"HTTP 200 on {path} — document API contract")
    ontop = report.get("ontop_deep", {}).get("counts", {})
    if ontop.get("buildings_with_address_label") not in (0, "0", None):
        hints.append("Ontop hasAddress labels exist — warm address names")
    return hints


def build_report(sections: List[str], timeout: int) -> Dict[str, Any]:
    t0 = time.perf_counter()
    all_sections = {"bg", "http", "geometry", "kb", "ontop", "carpark"}
    want = set(sections) if sections else all_sections
    report: Dict[str, Any] = {"host": HOST, "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}

    if "bg" in want:
        report["blazegraph_namespaces"] = probe_blazegraph_namespaces(timeout)
    if "http" in want:
        report["http_surface"] = probe_http_surface(timeout)
    if "geometry" in want:
        report["geometry_iris"] = probe_geometry_iris()
    if "kb" in want:
        report["kb_deep"] = probe_kb_deep()
    if "ontop" in want:
        report["ontop_deep"] = probe_ontop_deep(timeout)
    if "carpark" in want:
        report["carpark_text"] = probe_carpark_text_mining()

    report["next_probe_hints"] = next_probe_hints(report)
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Expand sg-old probing")
    parser.add_argument("--timeout", type=int, default=25)
    parser.add_argument("--section", action="append", default=[], help="bg|http|geometry|kb|ontop|carpark")
    parser.add_argument(
        "--json-out",
        type=Path,
        default=Path("data/mini_marie_cache/sg_old/expand_probe.json"),
    )
    args = parser.parse_args()

    report = build_report(args.section, args.timeout)
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {args.json_out} ({report['elapsed_seconds']}s)")
    if report.get("next_probe_hints"):
        print("Next hints:")
        for h in report["next_probe_hints"]:
            print(f"  - {h}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
