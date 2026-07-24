"""
MOF TWA Operations

Query the OntoMOFs TWA via remote SPARQL endpoint.
All result limits are hardcoded to keep MCP tool responses bounded.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, request

logger = logging.getLogger(__name__)

MOFS_PREFIX = "https://www.theworldavatar.com/kg/ontomofs_vkg/"
DEFAULT_SPARQL_ENDPOINT = "http://68.183.227.15:3840/ontop/sparql/"
QUERIES_DIR = Path(__file__).resolve().parent / "queries"

# Hardcoded query limits (not exposed to MCP callers; online MCP caps at 10 rows)
RESULT_LIMIT = 10
TOPOLOGY_LIMIT = 10
SOURCE_DB_LIMIT = 10
MOFID_LOOKUP_LIMIT = 5
MOF_PROPERTIES_LIMIT = 10
SPARQL_TIMEOUT_SECONDS = 120


def execute_sparql(
    query: str,
    endpoint: str = DEFAULT_SPARQL_ENDPOINT,
    timeout: int = SPARQL_TIMEOUT_SECONDS,
) -> List[Dict[str, Any]]:
    """Execute a SPARQL SELECT query against the remote Ontop endpoint."""
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
        raise RuntimeError(f"SPARQL HTTP {exc.code}: {body}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"SPARQL request failed: {exc.reason}") from exc

    rows: List[Dict[str, Any]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        row: Dict[str, Any] = {}
        for var, term in binding.items():
            row[var] = term.get("value")
        rows.append(row)
    return rows


def load_query(name: str) -> str:
    """Load a .sparql file from the queries/ directory."""
    path = QUERIES_DIR / name
    if not path.exists():
        raise FileNotFoundError(f"Query not found: {path}")
    return path.read_text(encoding="utf-8")


def run_query_file(name: str, **kwargs: Any) -> List[Dict[str, Any]]:
    """Load and execute a named query file."""
    return execute_sparql(load_query(name), **kwargs)


def format_results_as_tsv(results: List[Dict[str, Any]]) -> str:
    """Format SPARQL results as a TSV string."""
    if not results:
        return "No results found"
    headers = list(results[0].keys())
    lines = ["\t".join(headers)]
    for row in results:
        lines.append("\t".join(str(row.get(h, "")) for h in headers))
    return "\n".join(lines)


def _escape_literal(value: str) -> str:
    """Escape a string for use in a SPARQL literal."""
    return value.replace("\\", "\\\\").replace('"', '\\"')


def get_mof_total_count() -> List[Dict[str, Any]]:
    """Count all MetalOrganicFramework instances in the TWA."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (COUNT(?mof) AS ?count)
WHERE {{ ?mof a mofs:MetalOrganicFramework . }}
"""
    return execute_sparql(query)


def get_source_database_stats() -> List[Dict[str, Any]]:
    """Count MOFs grouped by source database."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?db (COUNT(?mof) AS ?count)
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasSourcedb ?db .
}}
GROUP BY ?db
ORDER BY DESC(?count)
LIMIT {SOURCE_DB_LIMIT}
"""
    return execute_sparql(query)


def get_tobassco_co2_coverage() -> List[Dict[str, Any]]:
    """Count Tobassco MOFs with predicted CO2 uptake at 15 bar, 298 K."""
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT (COUNT(?mof) AS ?with_co2)
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2 .
}}
"""
    return execute_sparql(query)


def get_tobassco_co2_uptake_stats() -> List[Dict[str, Any]]:
    """Average, min, and max Tobassco CO2 uptake (P15, T298)."""
    return run_query_file("05_co2_uptake_stats_tobassco.sparql")


def get_top_tobassco_co2_uptake() -> List[Dict[str, Any]]:
    """
    Top Tobassco MOFs by predicted CO2 uptake (seed query).
    Returns MOFid, optional PLD/LCD, and uptake in mmol/g.
    """
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?pld ?lcd ?co2_uptake
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake .
  OPTIONAL {{ ?mof mofs:hasPLD ?pld }}
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
}}
ORDER BY DESC(?co2_uptake)
LIMIT {RESULT_LIMIT}
"""
    return execute_sparql(query)


def get_top_tobassco_co2_valid_pore_geometry() -> List[Dict[str, Any]]:
    """Top Tobassco CO2 uptake where PLD and LCD are both > 0."""
    return run_query_file("02_top_co2_valid_pore_geometry.sparql")


def get_large_pore_co2_candidates() -> List[Dict[str, Any]]:
    """Tobassco MOFs with PLD >= 10 A and CO2 uptake below model cap (300)."""
    return run_query_file("04_large_pore_co2_candidates.sparql")


def get_tobassco_topology_counts() -> List[Dict[str, Any]]:
    """Count Tobassco MOFs grouped by RCSR topology symbol."""
    return run_query_file("03_top_topologies_tobassco.sparql")


def get_tobassco_mofs_by_topology(topology: str) -> List[Dict[str, Any]]:
    """Top Tobassco MOFs for a given RCSR topology, ordered by CO2 uptake."""
    topology_safe = _escape_literal(topology.strip().lower())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?topology ?pld ?lcd ?co2_uptake
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasRCSRSym ?topology ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake .
  OPTIONAL {{ ?mof mofs:hasPLD ?pld }}
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
  FILTER(LCASE(STR(?topology)) = "{topology_safe}")
}}
ORDER BY DESC(?co2_uptake)
LIMIT {RESULT_LIMIT}
"""
    return execute_sparql(query)


def get_tobassco_mofs_by_metal_node(metal_smiles: str) -> List[Dict[str, Any]]:
    """Top Tobassco MOFs containing a metal-node SMILES fragment."""
    fragment = _escape_literal(metal_smiles.strip())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?node_smiles ?pld ?lcd ?co2_uptake
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasNodeSmile ?node_smiles ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake .
  OPTIONAL {{ ?mof mofs:hasPLD ?pld }}
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
  FILTER(CONTAINS(STR(?node_smiles), "{fragment}"))
}}
ORDER BY DESC(?co2_uptake)
LIMIT {RESULT_LIMIT}
"""
    return execute_sparql(query)


def lookup_mof_by_mofid_fragment(mofid_fragment: str) -> List[Dict[str, Any]]:
    """Find MOFs whose MOFid-v1 string contains the given fragment."""
    fragment = _escape_literal(mofid_fragment.strip())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?sourcedb ?co2_uptake ?pld ?lcd ?topology
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb ?sourcedb ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake .
  OPTIONAL {{ ?mof mofs:hasPLD ?pld }}
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
  OPTIONAL {{ ?mof mofs:hasRCSRSym ?topology }}
  FILTER(CONTAINS(LCASE(STR(?mofid)), LCASE("{fragment}")))
}}
ORDER BY DESC(?co2_uptake)
LIMIT {MOFID_LOOKUP_LIMIT}
"""
    return execute_sparql(query)


def get_mof_properties_by_mofid_fragment(mofid_fragment: str) -> List[Dict[str, Any]]:
    """Return predicate/object pairs for MOFs matching a MOFid fragment."""
    fragment = _escape_literal(mofid_fragment.strip())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?p ?o
WHERE {{
  {{
    SELECT ?mof
    WHERE {{
      ?mof a mofs:MetalOrganicFramework ;
           mofs:hasMofidV1 ?mofid .
      FILTER(CONTAINS(LCASE(STR(?mofid)), LCASE("{fragment}")))
    }}
    LIMIT 1
  }}
  ?mof ?p ?o .
}}
LIMIT {MOF_PROPERTIES_LIMIT}
"""
    return execute_sparql(query)


def get_mofs_by_sourcedb(source_db: str) -> List[Dict[str, Any]]:
    """Top MOFs from a source database, ordered by CO2 uptake when available."""
    db = _escape_literal(source_db.strip())
    query = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?sourcedb ?co2_uptake ?pld ?lcd ?topology
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb ?sourcedb .
  OPTIONAL {{ ?mof mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake }}
  OPTIONAL {{ ?mof mofs:hasPLD ?pld }}
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
  OPTIONAL {{ ?mof mofs:hasRCSRSym ?topology }}
  FILTER(STR(?sourcedb) = "{db}")
}}
ORDER BY DESC(?co2_uptake)
LIMIT {RESULT_LIMIT}
"""
    return execute_sparql(query)
