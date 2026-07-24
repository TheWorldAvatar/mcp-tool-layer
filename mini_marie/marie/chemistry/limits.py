"""Hardcoded limits for chemistry MCP live queries and cache warm."""

from __future__ import annotations

ONLINE_PROBE_LIMIT = 5
DEFAULT_ONLINE_PROBE_LIMIT = ONLINE_PROBE_LIMIT
SPARQL_TIMEOUT = 90
SPARQL_TIMEOUT_HEAVY = 120

HEAVY_NAMESPACES = frozenset({"ontospecies", "ontozeolite"})

# Full-tier warm caps (fragile Blazegraph — never truly uncapped on 50M+ namespaces)
WARM_MAX_ROWS_BY_NAMESPACE: dict[str, int] = {
    "ontospecies": 10_000,
    "ontozeolite": 10_000,
    "ontokin": 50_000,
    "ontocompchem": 500_000,
    "ontoprovenance": 500_000,
    "ontomops": 1_000,
    "ontopesscan": 1_000,
}
DEFAULT_WARM_MAX_ROWS = 50_000

WARM_DELAY_SECONDS = 3.0
WARM_MAX_RETRIES = 5
WARM_RETRY_BACKOFF_BASE = 5.0
WARM_RETRY_BACKOFF_MAX = 60.0

# Namespaces with live A-box worth warming
WARM_NAMESPACES = frozenset(
    {"ontospecies", "ontokin", "ontocompchem", "ontozeolite", "ontoprovenance"}
)


def sparql_timeout(namespace: str) -> int:
    return SPARQL_TIMEOUT_HEAVY if namespace in HEAVY_NAMESPACES else SPARQL_TIMEOUT


def probe_limit(requested: int | None = None) -> int:
    """Cap SELECT row limits at ONLINE_PROBE_LIMIT."""
    if requested is None:
        return ONLINE_PROBE_LIMIT
    return min(int(requested), ONLINE_PROBE_LIMIT)


def warm_max_rows(namespace: str) -> int:
    return WARM_MAX_ROWS_BY_NAMESPACE.get(namespace, DEFAULT_WARM_MAX_ROWS)
