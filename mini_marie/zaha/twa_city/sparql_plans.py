"""
SPARQL plan builders for TWA city workflows.

Each plan exposes the same query shape with configurable LIMIT for online probing
vs offline replay (strip limit or raise cap).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from mini_marie.zaha.twa_city.twa_city_operations import (
    ONTOBUILTENV_PREFIX,
    _escape_literal,
    _usage_type_iri,
    execute_sparql,
    resolve_city,
)

DEFAULT_ONLINE_LIMIT = 10
# Deprecated: offline no longer re-queries remote; use full-tier pre-cache instead.
DEFAULT_OFFLINE_CAP = 500_000


@dataclass
class SparqlPlan:
    """Executable SPARQL step with limit metadata for record/replay."""

    tool: str
    city: str
    endpoint: str
    query: str
    limit_applied: Optional[int]
    limit_stripped: bool
    args: Dict[str, Any]

    def execute(self, timeout: int = 180) -> List[Dict[str, Any]]:
        return execute_sparql(self.query, self.endpoint, timeout=timeout)


def strip_limit_clause(query: str) -> str:
    """Remove trailing LIMIT clause from SPARQL."""
    return re.sub(r"\s+LIMIT\s+\d+\s*$", "", query.strip(), flags=re.IGNORECASE)


def apply_limit_clause(query: str, limit: Optional[int]) -> tuple[str, Optional[int], bool]:
    """Return query with LIMIT applied or stripped."""
    base = strip_limit_clause(query)
    if limit is None:
        return base, None, True
    return f"{base}\nLIMIT {int(limit)}", int(limit), False


def _values_block(iri_list: List[str]) -> str:
    tokens = " ".join(f"<{iri}>" for iri in iri_list if iri)
    return f"VALUES ?building {{ {tokens} }}"


def plan_list_buildings_with_height(
    city: str,
    limit: Optional[int] = DEFAULT_ONLINE_LIMIT,
) -> SparqlPlan:
    """All buildings with height (probe subset online, larger set offline)."""
    endpoint = resolve_city(city)
    base = """
PREFIX bldg: <http://www.opengis.net/citygml/building/2.0/>
SELECT ?building ?height
WHERE {
  ?building a bldg:Building ;
            bldg:measuredHeight ?height .
}
ORDER BY DESC(?height)
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="list_buildings_with_height",
        city=city,
        endpoint=endpoint,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={"city": city},
    )


def plan_rank_buildings_by_height(
    city: str,
    limit: Optional[int] = DEFAULT_ONLINE_LIMIT,
) -> SparqlPlan:
    """Rank buildings by height with optional usage and label."""
    endpoint = resolve_city(city)
    base = f"""
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
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="rank_buildings_by_height",
        city=city,
        endpoint=endpoint,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={"city": city},
    )


def plan_fetch_building_locations(
    city: str,
    building_iris: List[str],
    wkt_row_cap: Optional[int] = 200,
) -> SparqlPlan:
    """Fetch footprint WKT for a bounded list of building IRIs (VALUES clause)."""
    if not building_iris:
        raise ValueError("building_iris must not be empty")
    endpoint = resolve_city(city)
    values = _values_block(building_iris)
    base = f"""
PREFIX bldg: <http://www.opengis.net/citygml/building/2.0/>
PREFIX geo: <http://www.opengis.net/ont/geosparql#>
PREFIX be: <{ONTOBUILTENV_PREFIX}>
SELECT ?building ?height ?wkt ?usage_type ?label
WHERE {{
  {values}
  ?building bldg:measuredHeight ?height ;
            geo:hasGeometry ?g .
  ?g geo:asWKT ?wkt .
  FILTER(CONTAINS(LCASE(STR(?wkt)), "polygon"))
  OPTIONAL {{
    ?building be:hasPropertyUsage ?u .
    ?u a ?usage_type .
  }}
  OPTIONAL {{ ?building <http://www.w3.org/2000/01/rdf-schema#label> ?label }}
}}
"""
    query, applied, stripped = apply_limit_clause(base, wkt_row_cap)
    return SparqlPlan(
        tool="fetch_building_locations",
        city=city,
        endpoint=endpoint,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={"city": city, "building_iris": building_iris},
    )


def plan_buildings_by_usage(
    city: str,
    usage_type: str,
    limit: Optional[int] = DEFAULT_ONLINE_LIMIT,
) -> SparqlPlan:
    endpoint = resolve_city(city)
    usage_iri = _escape_literal(_usage_type_iri(usage_type))
    base = f"""
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
"""
    query, applied, stripped = apply_limit_clause(base, limit)
    return SparqlPlan(
        tool="buildings_by_usage",
        city=city,
        endpoint=endpoint,
        query=query,
        limit_applied=applied,
        limit_stripped=stripped,
        args={"city": city, "usage_type": usage_type},
    )


PLAN_BUILDERS = {
    "list_buildings_with_height": lambda **kw: plan_list_buildings_with_height(
        kw["city"], kw.get("limit")
    ),
    "rank_buildings_by_height": lambda **kw: plan_rank_buildings_by_height(
        kw["city"], kw.get("limit")
    ),
    "fetch_building_locations": lambda **kw: plan_fetch_building_locations(
        kw["city"], kw["building_iris"], kw.get("wkt_row_cap")
    ),
    "buildings_by_usage": lambda **kw: plan_buildings_by_usage(
        kw["city"], kw["usage_type"], kw.get("limit")
    ),
}


def build_plan(
    tool: str,
    mode: str,
    online_limit: int = DEFAULT_ONLINE_LIMIT,
    **args: Any,
) -> SparqlPlan:
    """Build SPARQL plan: online=LIMIT probe; warm=uncapped full atomic (pre-cache)."""
    if tool not in PLAN_BUILDERS:
        raise ValueError(f"Unknown plan tool: {tool}")

    if mode == "online":
        if tool == "fetch_building_locations":
            iris = args.get("building_iris") or []
            building_count = len(iris) if iris else int(online_limit)
            wkt_cap = max(10, building_count * 50)
            return PLAN_BUILDERS[tool](wkt_row_cap=wkt_cap, **args)
        return PLAN_BUILDERS[tool](limit=int(online_limit), **args)

    # warm / full pre-cache: no LIMIT on list scans; generous WKT cap for location batches
    if tool == "fetch_building_locations":
        iris = args.get("building_iris") or []
        wkt_cap = max(1000, len(iris) * 50) if iris else None
        return PLAN_BUILDERS[tool](wkt_row_cap=wkt_cap, **args)
    return PLAN_BUILDERS[tool](limit=None, **args)
