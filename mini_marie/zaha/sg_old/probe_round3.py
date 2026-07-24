"""
Probe round 3: timeseries API, visualisation assets, scope bbox, derivations, geometry.

Usage:
  python -m mini_marie.zaha.sg_old.probe_round3
"""

from __future__ import annotations

import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.local_store import db_path, ensure_db
from mini_marie.zaha.sg_old.ontop_store import ONTOP_ENDPOINT
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

HOST = "https://sg-old.theworldavatar.io"
UA = "curl/8.0"
OUT = mini_marie_cache_root() / "sg_old" / "probe_round3.json"

# Known timeseries from kb cache
SAMPLE_TS = [
    "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664",  # ship speed
    "Timeseries_ee9a9039-0da0-4caa-92b7-57f6c207a05c",  # CO concentration
]

TS_HTTP_PATHS = [
    "/timeseries-agent/query",
    "/timeseries-agent/",
    "/stack-data-uploader/timeseries",
    "/access-agent/timeseries",
    "/blazegraph/namespace/kb/sparql",
    "/ontop/sparql/",
    "/dispersion-interactor/GetDispersionSimulations",
    "/dispersion-interactor/GetPollutantConcentrations",
    "/dispersion-interactor/GetColourBar",
]

TS_QUERY_TEMPLATES = [
    {"timeseries": "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"},
    {"iri": "https://www.theworldavatar.com/kg/ontotimeseries/Timeseries_c3389874-bc39-46fe-861b-63f6e8176664"},
    {"id": "Timeseries_c3389874-bc39-46fe-861b-63f6e8176664", "limit": 5},
]


def _http(url: str, *, method: str = "GET", data: Optional[bytes] = None, headers: Optional[Dict[str, str]] = None, timeout: float = 20) -> Dict[str, Any]:
    hdrs = {"User-Agent": UA, **(headers or {})}
    req = request.Request(url, data=data, headers=hdrs, method=method)
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            body = resp.read(4000).decode("utf-8", errors="replace")
            return {"ok": True, "status": resp.status, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:2000]}
    except error.HTTPError as exc:
        body = exc.read(800).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:500]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:200], "ms": round((time.perf_counter() - t0) * 1000)}


def probe_timeseries_http() -> Dict[str, Any]:
    print("=== Timeseries HTTP ===", flush=True)
    out: Dict[str, Any] = {"get_paths": {}, "post_attempts": {}}
    for path in TS_HTTP_PATHS:
        url = HOST + path
        out["get_paths"][path] = _http(url)
        print(f"  GET {path}: {out['get_paths'][path].get('status') or out['get_paths'][path].get('http')}", flush=True)

    base = HOST + "/timeseries-agent/query"
    for i, body in enumerate(TS_QUERY_TEMPLATES):
        payload = json.dumps(body).encode()
        out["post_attempts"][str(i)] = _http(base, method="POST", data=payload, headers={"Content-Type": "application/json"})
    # GET with query params
    for i, params in enumerate(TS_QUERY_TEMPLATES):
        url = base + "?" + parse.urlencode({k: str(v) for k, v in params.items()})
        out["post_attempts"][f"get_{i}"] = _http(url)
    return out


def probe_visualisation_assets() -> Dict[str, Any]:
    print("=== Visualisation assets ===", flush=True)
    out: Dict[str, Any] = {"pages": {}, "scripts": {}, "urls_found": []}
    for page in ["/visualisation/", "/visualisation/index.html"]:
        r = _http(HOST + page, timeout=30)
        out["pages"][page] = {"status": r.get("status") or r.get("http"), "len": len(r.get("body", ""))}
        html = r.get("body", "")
        scripts = re.findall(r'<script[^>]+src=["\']([^"\']+)["\']', html, re.I)
        links = re.findall(r'href=["\']([^"\']+)["\']', html, re.I)
        for s in scripts:
            if s.startswith("/"):
                s = HOST + s
            elif not s.startswith("http"):
                s = HOST + "/visualisation/" + s.lstrip("./")
            sr = _http(s, timeout=30)
            out["scripts"][s] = {"status": sr.get("status") or sr.get("http"), "bytes": len(sr.get("body", ""))}
            # mine JS for API URLs
            for m in re.findall(r"https?://[a-zA-Z0-9._:/\-?&=%]+", sr.get("body", "")):
                if "theworldavatar" in m or "mapbox" in m or "api" in m.lower():
                    out["urls_found"].append(m)
        print(f"  {page}: scripts={len(scripts)}", flush=True)

    # common static asset names
    for asset in ["main.js", "bundle.js", "app.js", "config.json", "layers.json", "assets/config.json"]:
        url = f"{HOST}/visualisation/{asset}"
        out["scripts"][url] = _http(url, timeout=15)
    out["urls_found"] = sorted(set(out["urls_found"]))[:40]
    return out


def probe_scope_and_derivations() -> Dict[str, Any]:
    print("=== Scope + derivations (kb SQLite) ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(db_path())
    out: Dict[str, Any] = {}

    scopes = [r[0] for r in conn.execute(
        "SELECT DISTINCT s FROM triples WHERE ns='kb' AND o LIKE '%/Scope' AND p LIKE '%type%'"
    )]
    out["scope_full_triples"] = {}
    for s in scopes:
        rows = conn.execute("SELECT p,o FROM triples WHERE ns='kb' AND s=?", (s,)).fetchall()
        out["scope_full_triples"][s] = rows
        print(f"  scope {s.split('/')[-1]}: {len(rows)} triples", flush=True)

    out["derivation_output_chain"] = conn.execute(
        """
        SELECT d.s, d.p, d.o
        FROM triples d
        WHERE d.ns='kb' AND d.s LIKE '%DerivationWithTimeSeries%'
        ORDER BY d.s LIMIT 80
        """
    ).fetchall()

    out["derivation_distinct_preds"] = conn.execute(
        """
        SELECT p, COUNT(*) n FROM triples
        WHERE ns='kb' AND s LIKE '%DerivationWithTimeSeries%'
        GROUP BY p ORDER BY n DESC
        """
    ).fetchall()

    # Any geosparql / wkt / bbox in kb at all
    out["geo_predicate_counts"] = conn.execute(
        """
        SELECT p, COUNT(*) n FROM triples WHERE ns='kb'
        AND (LOWER(p) LIKE '%wkt%' OR LOWER(p) LIKE '%geosparql%' OR LOWER(p) LIKE '%asgeojson%'
             OR LOWER(p) LIKE '%coordinate%' OR LOWER(o) LIKE '%polygon%')
        GROUP BY p ORDER BY n DESC LIMIT 20
        """
    ).fetchall()

    # Link derivation -> dispersion output / concentration
    out["dispersion_output_links"] = conn.execute(
        """
        SELECT s,p,o FROM triples WHERE ns='kb'
        AND (s LIKE '%DispersionOutput%' OR o LIKE '%DispersionOutput%' OR p LIKE '%DispersionOutput%')
        LIMIT 20
        """
    ).fetchall()

    conn.close()
    return out


def probe_ontop_geometry(timeout: int = 180) -> Dict[str, Any]:
    print("=== Ontop geometry (long timeout) ===", flush=True)
    out: Dict[str, Any] = {}
    queries = {
        "hasGeometry_count": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <http://www.opengis.net/ont/geosparql#hasGeometry> ?g .
}""",
        "footprint_asWKT_count": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b <http://www.opengis.net/citygml/building/2.0/lod0FootPrint> ?fp .
  ?fp <http://www.opengis.net/ont/geosparql#asWKT> ?w .
}""",
        "abbott_wkt": """
SELECT ?wkt WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "abbott")) .
  ?b <http://www.opengis.net/ont/geosparql#hasGeometry> ?g .
  ?g <http://www.opengis.net/ont/geosparql#asWKT> ?wkt .
} LIMIT 1""",
        "create_facility_search": """
SELECT ?b ?l WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
  ?b <https://www.theworldavatar.com/kg/ontobim/hasFacility> ?f .
  ?f <http://www.w3.org/2000/01/rdf-schema#label> ?l .
  FILTER(CONTAINS(LCASE(STR(?l)), "create") || CONTAINS(LCASE(STR(?l)), "nus")
      || CONTAINS(LCASE(STR(?l)), "kent") || CONTAINS(LCASE(STR(?l)), "university"))
} LIMIT 20""",
    }
    for name, q in queries.items():
        try:
            rows = execute_sparql_get(q, ONTOP_ENDPOINT, timeout=timeout)
            out[name] = {"ok": True, "rows": rows[:5]}
            print(f"  {name}: ok {rows[:2]}", flush=True)
        except Exception as exc:
            out[name] = {"ok": False, "error": str(exc)[:300]}
            print(f"  {name}: err", flush=True)
    return out


def probe_carpark_address_mining() -> Dict[str, Any]:
    print("=== Carpark address mining ===", flush=True)
    ensure_db()
    conn = sqlite3.connect(db_path())
    keywords = ["create", "nus", "university", "kent", "science", "drive", "tower", "clementi", "one north", "fusionopolis"]
    hits: Dict[str, List[tuple]] = {}
    for kw in keywords:
        rows = conn.execute(
            "SELECT s,o FROM triples WHERE ns='carpark' AND p LIKE '%label%' AND LOWER(o) LIKE ? LIMIT 5",
            (f"%{kw}%",),
        ).fetchall()
        if rows:
            hits[kw] = rows
    all_labels = conn.execute(
        "SELECT COUNT(DISTINCT o) FROM triples WHERE ns='carpark' AND p LIKE '%label%'"
    ).fetchone()[0]
    conn.close()
    print(f"  distinct labels: {all_labels}, keyword hits: {len(hits)}", flush=True)
    return {"distinct_carpark_labels": all_labels, "keyword_hits": hits}


def build_report() -> Dict[str, Any]:
    t0 = time.perf_counter()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "timeseries_http": probe_timeseries_http(),
        "visualisation": probe_visualisation_assets(),
        "scope_derivations": probe_scope_and_derivations(),
        "ontop_geometry": probe_ontop_geometry(),
        "carpark_addresses": probe_carpark_address_mining(),
        "elapsed_seconds": 0.0,
    }
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    report = build_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    print(f"\nWrote {OUT} ({report['elapsed_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
