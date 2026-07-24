"""
SPARQL plan builders for MOF TWA workflows.

Configurable LIMIT for online probing vs offline replay (strip or raise cap).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mini_marie.mop_mof.mof.mof_operations import (
    DEFAULT_SPARQL_ENDPOINT,
    MOFS_PREFIX,
    _escape_literal,
    execute_sparql,
)

DEFAULT_ONLINE_LIMIT = 10
DEFAULT_OFFLINE_CAP = 500_000


@dataclass
class SparqlPlan:
    tool: str
    endpoint: str
    query: str
    limit_applied: Optional[int]
    limit_stripped: bool
    args: Dict[str, Any]

    def execute(self, timeout: int = 180) -> List[Dict[str, Any]]:
        return execute_sparql(self.query, self.endpoint, timeout=timeout)


def strip_limit_clause(query: str) -> str:
    return re.sub(r"\s+LIMIT\s+\d+\s*$", "", query.strip(), flags=re.IGNORECASE)


def apply_limit_clause(query: str, limit: Optional[int]) -> tuple[str, Optional[int], bool]:
    base = strip_limit_clause(query)
    if limit is None:
        return base, None, True
    return f"{base}\nLIMIT {int(limit)}", int(limit), False


def plan_rank_tobassco_co2(limit: Optional[int] = DEFAULT_ONLINE_LIMIT) -> SparqlPlan:
    base = f"""
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
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="rank_tobassco_co2",
        endpoint=DEFAULT_SPARQL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={},
    )


def plan_rank_tobassco_co2_valid_geometry(limit: Optional[int] = DEFAULT_ONLINE_LIMIT) -> SparqlPlan:
    base = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?pld ?lcd ?co2_uptake
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake .
  OPTIONAL {{ ?mof mofs:hasPLD ?pld }}
  OPTIONAL {{ ?mof mofs:hasLCD ?lcd }}
  FILTER(?pld > 0 && ?lcd > 0)
}}
ORDER BY DESC(?co2_uptake)
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="rank_tobassco_co2_valid_geometry",
        endpoint=DEFAULT_SPARQL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={},
    )


def plan_rank_large_pore_co2_candidates(limit: Optional[int] = DEFAULT_ONLINE_LIMIT) -> SparqlPlan:
    base = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?mofid ?pld ?lcd ?co2_uptake
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasMofidV1 ?mofid ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasPredAdsorptionUptake_CO2P15T298mmolg ?co2_uptake ;
       mofs:hasPLD ?pld ;
       mofs:hasLCD ?lcd .
  FILTER(?pld >= 10 && ?co2_uptake < 300)
}}
ORDER BY DESC(?co2_uptake)
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="rank_large_pore_co2_candidates",
        endpoint=DEFAULT_SPARQL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={},
    )


def plan_rank_tobassco_by_topology(topology: str, limit: Optional[int] = DEFAULT_ONLINE_LIMIT) -> SparqlPlan:
    topology_safe = _escape_literal(topology.strip().lower())
    base = f"""
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
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="rank_tobassco_by_topology",
        endpoint=DEFAULT_SPARQL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={"topology": topology},
    )


def plan_list_topology_counts(limit: Optional[int] = 15) -> SparqlPlan:
    base = f"""
PREFIX mofs: <{MOFS_PREFIX}>
SELECT ?topology (COUNT(?mof) AS ?count)
WHERE {{
  ?mof a mofs:MetalOrganicFramework ;
       mofs:hasSourcedb "Tobassco" ;
       mofs:hasRCSRSym ?topology .
}}
GROUP BY ?topology
ORDER BY DESC(?count)
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="list_topology_counts",
        endpoint=DEFAULT_SPARQL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={},
    )


def plan_fetch_mof_properties(mofid_fragment: str, triple_limit: Optional[int] = 30) -> SparqlPlan:
    fragment = _escape_literal(mofid_fragment.strip())
    base = f"""
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
"""
    query, applied, stripped = apply_limit_clause(base, triple_limit)
    return SparqlPlan(
        tool="fetch_mof_properties",
        endpoint=DEFAULT_SPARQL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={"mofid_fragment": mofid_fragment},
    )


PLAN_BUILDERS = {
    "rank_tobassco_co2": lambda **kw: plan_rank_tobassco_co2(kw.get("limit")),
    "rank_tobassco_co2_valid_geometry": lambda **kw: plan_rank_tobassco_co2_valid_geometry(kw.get("limit")),
    "rank_large_pore_co2_candidates": lambda **kw: plan_rank_large_pore_co2_candidates(kw.get("limit")),
    "rank_tobassco_by_topology": lambda **kw: plan_rank_tobassco_by_topology(
        kw["topology"], kw.get("limit")
    ),
    "list_topology_counts": lambda **kw: plan_list_topology_counts(kw.get("limit")),
    "fetch_mof_properties": lambda **kw: plan_fetch_mof_properties(
        kw["mofid_fragment"], kw.get("triple_limit")
    ),
}


def build_plan(tool: str, mode: str, online_limit: int, offline_cap: int, **args: Any) -> SparqlPlan:
    if tool not in PLAN_BUILDERS:
        raise ValueError(f"Unknown plan tool: {tool}")

    if mode == "online":
        if tool == "fetch_mof_properties":
            return PLAN_BUILDERS[tool](triple_limit=min(30, online_limit * 3), **args)
        if tool == "list_topology_counts":
            return PLAN_BUILDERS[tool](limit=int(online_limit), **args)
        return PLAN_BUILDERS[tool](limit=int(online_limit), **args)

    if tool == "fetch_mof_properties":
        return PLAN_BUILDERS[tool](triple_limit=min(500, offline_cap), **args)
    offline_limit = int(offline_cap) if offline_cap > 0 else None
    return PLAN_BUILDERS[tool](limit=offline_limit, **args)
