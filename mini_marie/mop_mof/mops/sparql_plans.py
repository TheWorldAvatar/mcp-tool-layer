"""
SPARQL plan builders for MOPs TWA workflows (local in-memory graph).

Configurable LIMIT for online probing vs offline replay.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mini_marie.mop_mof.mops.twa_operations import execute_sparql, format_results_as_tsv

DEFAULT_ONLINE_LIMIT = 10
DEFAULT_OFFLINE_CAP = 50_000
LOCAL_ENDPOINT = "local://twa_mops"


@dataclass
class SparqlPlan:
    tool: str
    endpoint: str
    query: str
    limit_applied: Optional[int]
    limit_stripped: bool
    args: Dict[str, Any]

    def execute(self) -> List[Dict[str, Any]]:
        return execute_sparql(self.query)


def strip_limit_clause(query: str) -> str:
    return re.sub(r"\s+LIMIT\s+\d+\s*$", "", query.strip(), flags=re.IGNORECASE)


def apply_limit_clause(query: str, limit: Optional[int]) -> tuple[str, Optional[int], bool]:
    base = strip_limit_clause(query)
    if limit is None:
        return base, None, True
    return f"{base}\nLIMIT {int(limit)}", int(limit), False


def plan_list_mops(limit: Optional[int] = DEFAULT_ONLINE_LIMIT) -> SparqlPlan:
    base = """
PREFIX ontomops: <https://www.theworldavatar.com/kg/ontomops/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT DISTINCT ?mopLabel ?ccdcNumber ?mopFormula
WHERE {
  ?mop a ontomops:MetalOrganicPolyhedron .
  ?mop rdfs:label ?mopLabel .
  OPTIONAL { ?mop ontomops:hasCCDCNumber ?ccdcNumber }
  OPTIONAL { ?mop ontomops:hasMOPFormula ?mopFormula }
}
ORDER BY ?mopLabel
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="list_mops",
        endpoint=LOCAL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={},
    )


def plan_list_syntheses(limit: Optional[int] = DEFAULT_ONLINE_LIMIT) -> SparqlPlan:
    base = """
PREFIX ontosyn: <https://www.theworldavatar.com/kg/OntoSyn/>
PREFIX rdfs: <http://www.w3.org/2000/01/rdf-schema#>

SELECT ?synthesis ?synthesisLabel
WHERE {
  ?synthesis a ontosyn:ChemicalSynthesis .
  ?synthesis rdfs:label ?synthesisLabel .
}
ORDER BY ?synthesisLabel
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="list_syntheses",
        endpoint=LOCAL_ENDPOINT,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={},
    )


PLAN_BUILDERS = {
    "list_mops": lambda **kw: plan_list_mops(kw.get("limit")),
    "list_syntheses": lambda **kw: plan_list_syntheses(kw.get("limit")),
}


def build_plan(tool: str, mode: str, online_limit: int, offline_cap: int, **args: Any) -> SparqlPlan:
    if tool not in PLAN_BUILDERS:
        raise ValueError(f"Unknown plan tool: {tool}")

    if mode == "online":
        return PLAN_BUILDERS[tool](limit=int(online_limit), **args)

    offline_limit = int(offline_cap) if offline_cap > 0 else None
    return PLAN_BUILDERS[tool](limit=offline_limit, **args)
