"""
TWA City TWA operations — SPARQL queries for Bremen and Kaiserslautern building graphs.

Probe helpers (run_discovery, probe_endpoints) and MCP-backed query functions share execute_sparql.
Result limits for MCP tools are hardcoded below.
"""

from __future__ import annotations

import json
import socket
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request

QUERIES_DIR = Path(__file__).resolve().parent / "queries"
SPARQL_TIMEOUT_SECONDS = 120

BUILDING_PREFIX = "https://theworldavatar.io/kg/Building/"
ONTOBUILTENV_PREFIX = "https://www.theworldavatar.com/kg/ontobuiltenv/"

# Hardcoded MCP response limits
RESULT_LIMIT = 10
USAGE_TYPE_LIMIT = 25
LOOKUP_LIMIT = 5
PROPERTY_LIMIT = 30

# City TWA SPARQL endpoints (Ontop UI → append sparql/)
CITY_ENDPOINTS = {
    "bremen": "https://bremen.cmpg.io/ontop/sparql/",
    "kaiserslautern": "https://kaiserslautern.cmpg.io/ontop/sparql/",
}

DEFAULT_ENDPOINT_CANDIDATES: List[str] = list(CITY_ENDPOINTS.values())

STACK_INTERNAL_HINTS = {
    "kaiserslautern_postgis": "kaiserslautern-postgis:5432",
    "bremen_postgis": "bremen-stack-postgis:5432",
    "kaiserslautern_adminer": "https://kaiserslautern.cmpg.io/adminer/ui/?pgsql=kaiserslautern-postgis%3A5432",
    "bremen_adminer": "https://bremen.cmpg.io/adminer/ui/?pgsql=bremen-stack-postgis%3A5432&username=postgres",
}


def execute_sparql(
    query: str,
    endpoint: str,
    timeout: int = SPARQL_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """Execute a SPARQL SELECT against an Ontop (or compatible) endpoint."""
    req = request.Request(
        endpoint,
        data=query.encode("utf-8"),
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/sparql-query",
        },
        method="POST",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SPARQL HTTP {exc.code} at {endpoint}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"SPARQL request failed for {endpoint}: {exc.reason}") from exc

    rows: List[Dict[str, Any]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        row: Dict[str, Any] = {}
        for var, term in binding.items():
            row[var] = term.get("value")
        rows.append(row)
    return rows


def load_query(name: str) -> str:
    path = QUERIES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Query not found: {path}")
    return path.read_text(encoding="utf-8")


def run_query_file(name: str, endpoint: str, **kwargs: Any) -> List[Dict[str, Any]]:
    return execute_sparql(load_query(name), endpoint=endpoint, **kwargs)


def format_results_as_tsv(results: List[Dict[str, Any]]) -> str:
    if not results:
        return "No results found"
    headers = list(results[0].keys())
    lines = ["\t".join(headers)]
    for row in results:
        lines.append("\t".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines)


def tcp_port_open(host: str, port: int, timeout: float = 2.0) -> bool:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((host, port))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def probe_sparql_endpoint(url: str, timeout: float = 8.0) -> Dict[str, Any]:
    """Ping an endpoint with a minimal SELECT."""
    ping = "SELECT * WHERE { ?s ?p ?o } LIMIT 1"
    try:
        rows = execute_sparql(ping, endpoint=url, timeout=int(timeout))
        return {"url": url, "ok": True, "row_count": len(rows), "error": None}
    except Exception as exc:
        return {"url": url, "ok": False, "row_count": 0, "error": str(exc)}


def probe_endpoints(
    candidates: Optional[List[str]] = None,
    hosts: Optional[List[str]] = None,
    ports: Optional[List[int]] = None,
    paths: Optional[List[str]] = None,
) -> List[Dict[str, Any]]:
    """
    Try SPARQL POST on candidate URLs.

    If hosts/ports are given, only open ports are tried (faster than blind HTTP).
    """
    paths = paths or ["/ontop/sparql/"]
    urls: List[str] = list(candidates or [])

    if hosts and ports:
        for host in hosts:
            for port in ports:
                if tcp_port_open(host, port):
                    for path in paths:
                        urls.append(f"http://{host}:{port}{path}")

    seen: set[str] = set()
    results: List[Dict[str, Any]] = []
    for url in urls:
        if url in seen:
            continue
        seen.add(url)
        results.append(probe_sparql_endpoint(url))
    return results


SHALLOW_DISCOVERY_QUERIES = [
    "00_sample_triples.sparql",
    "01_class_counts.sparql",
    "01b_building_count.sparql",
    "02_predicate_counts.sparql",
    "03_geosparql_predicates.sparql",
    "04_city_name_filter.sparql",
]

DEEP_DISCOVERY_QUERIES = [
    "10_one_building_properties.sparql",
    "11_building_predicates_on_sample.sparql",
    "12_height_stats.sparql",
    "13_top_buildings_by_height.sparql",
    "14_usage_type_counts.sparql",
    "15_property_coverage.sparql",
    "16_buildings_with_address_sample.sparql",
    "17_footprint_area_stats.sparql",
    "18_wkt_length_sample.sparql",
]


def _run_query_batch(endpoint: str, query_files: List[str]) -> Dict[str, Any]:
    out: Dict[str, Any] = {}
    for name in query_files:
        key = name.replace(".sparql", "")
        try:
            out[key] = run_query_file(name, endpoint)
        except Exception as exc:
            out[key] = {"error": str(exc)}
    return out


def run_discovery(endpoint: str) -> Dict[str, Any]:
    """Run standard introspection queries; per-query errors are captured, not raised."""
    return _run_query_batch(endpoint, SHALLOW_DISCOVERY_QUERIES)


def run_deep_discovery(endpoint: str) -> Dict[str, Any]:
    """Building-focused probes (bounded queries, safe for large graphs)."""
    return _run_query_batch(endpoint, DEEP_DISCOVERY_QUERIES)


def resolve_city(city: str) -> str:
    """Map city name to SPARQL endpoint URL."""
    key = city.strip().lower().replace(" ", "_").replace("-", "_")
    aliases = {
        "kl": "kaiserslautern",
        "kaiserslautern": "kaiserslautern",
        "bremen": "bremen",
    }
    key = aliases.get(key, key)
    if key not in CITY_ENDPOINTS:
        allowed = ", ".join(sorted(CITY_ENDPOINTS))
        raise ValueError(f"Unknown city {city!r}. Use one of: {allowed}")
    return CITY_ENDPOINTS[key]


def _escape_literal(value: str) -> str:
    return value.replace("\\", "\\\\").replace('"', '\\"')


def _usage_type_iri(usage_type: str) -> str:
    u = usage_type.strip()
    if u.startswith("http://") or u.startswith("https://"):
        return u
    local = u.rsplit("/", 1)[-1]
    return f"{ONTOBUILTENV_PREFIX}{local}"


def get_building_count(city: str) -> List[Dict[str, Any]]:
    """Count citygml:Building instances."""
    return run_query_file("01b_building_count.sparql", resolve_city(city))


def get_property_coverage(city: str) -> List[Dict[str, Any]]:
    """Per-metric counts of buildings with height, address, usage, etc."""
    return run_query_file("15_property_coverage.sparql", resolve_city(city))


def get_usage_type_counts(city: str) -> List[Dict[str, Any]]:
    """Building counts grouped by ontobuiltenv usage type."""
    return run_query_file("14_usage_type_counts.sparql", resolve_city(city))


def get_height_stats(city: str) -> List[Dict[str, Any]]:
    """Min, max, avg measuredHeight for buildings that have height."""
    return run_query_file("12_height_stats.sparql", resolve_city(city))


def get_top_buildings_by_height(city: str) -> List[Dict[str, Any]]:
    """Top buildings by measuredHeight (metres)."""
    endpoint = resolve_city(city)
    query = f"""
PREFIX bldg: <http://www.opengis.net/citygml/building/2.0/>
PREFIX be: <{ONTOBUILTENV_PREFIX}>
SELECT ?building ?height ?storeys ?usage_type ?label
WHERE {{
  ?building a bldg:Building ;
            bldg:measuredHeight ?height .
  OPTIONAL {{ ?building bldg:storeysAboveGround ?storeys }}
  OPTIONAL {{
    ?building be:hasPropertyUsage ?u .
    ?u a ?usage_type .
  }}
  OPTIONAL {{ ?building <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
}}
ORDER BY DESC(?height)
LIMIT {RESULT_LIMIT}
"""
    return execute_sparql(query, endpoint)


def get_buildings_by_usage(city: str, usage_type: str) -> List[Dict[str, Any]]:
    """Top buildings with a given ontobuiltenv usage type (e.g. Domestic, Office)."""
    endpoint = resolve_city(city)
    usage_iri = _escape_literal(_usage_type_iri(usage_type))
    query = f"""
PREFIX bldg: <http://www.opengis.net/citygml/building/2.0/>
PREFIX be: <{ONTOBUILTENV_PREFIX}>
SELECT ?building ?height ?storeys ?usage_type ?label
WHERE {{
  ?building a bldg:Building ;
            bldg:measuredHeight ?height ;
            be:hasPropertyUsage ?u .
  ?u a ?usage_type .
  OPTIONAL {{ ?building bldg:storeysAboveGround ?storeys }}
  OPTIONAL {{ ?building <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
  FILTER(STR(?usage_type) = "{usage_iri}")
}}
ORDER BY DESC(?height)
LIMIT {RESULT_LIMIT}
"""
    return execute_sparql(query, endpoint)


def lookup_building_by_uuid_fragment(city: str, uuid_fragment: str) -> List[Dict[str, Any]]:
    """Find buildings whose IRI contains the UUID substring."""
    endpoint = resolve_city(city)
    fragment = _escape_literal(uuid_fragment.strip())
    query = f"""
PREFIX bldg: <http://www.opengis.net/citygml/building/2.0/>
PREFIX be: <{ONTOBUILTENV_PREFIX}>
SELECT ?building ?height ?storeys ?usage_type ?label
WHERE {{
  ?building a bldg:Building ;
            bldg:measuredHeight ?height .
  OPTIONAL {{ ?building bldg:storeysAboveGround ?storeys }}
  OPTIONAL {{
    ?building be:hasPropertyUsage ?u .
    ?u a ?usage_type .
  }}
  OPTIONAL {{ ?building <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
  FILTER(CONTAINS(LCASE(STR(?building)), LCASE("{fragment}")))
}}
ORDER BY DESC(?height)
LIMIT {LOOKUP_LIMIT}
"""
    return execute_sparql(query, endpoint)


def get_building_properties(city: str, uuid_fragment: str) -> List[Dict[str, Any]]:
    """Predicate/object pairs for the first building matching a UUID fragment (no WKT)."""
    endpoint = resolve_city(city)
    fragment = _escape_literal(uuid_fragment.strip())
    query = f"""
PREFIX bldg: <http://www.opengis.net/citygml/building/2.0/>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
SELECT ?p ?o
WHERE {{
  {{
    SELECT ?building
    WHERE {{
      ?building a bldg:Building .
      FILTER(CONTAINS(LCASE(STR(?building)), LCASE("{fragment}")))
    }}
    LIMIT 1
  }}
  ?building ?p ?o .
  FILTER(?p != geo:asWKT)
}}
LIMIT {PROPERTY_LIMIT}
"""
    return execute_sparql(query, endpoint)
