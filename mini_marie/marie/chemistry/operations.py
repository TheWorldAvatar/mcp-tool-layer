"""Shared MCP operations for chemistry namespaces."""

from __future__ import annotations

import json
from typing import Any, Dict, List

from mini_marie.marie.chemistry.limits import ONLINE_PROBE_LIMIT, sparql_timeout
from mini_marie.marie.chemistry.registry import NAMESPACES, endpoint, tbox_paths
from mini_marie.marie.chemistry.sparql import execute_sparql_get, format_tsv
from mini_marie.marie.chemistry.tbox_index import summarize_tbox


def namespace_info(ns: str) -> str:
    meta = NAMESPACES[ns]
    paths = tbox_paths(ns)
    summary = summarize_tbox(paths)
    payload: Dict[str, Any] = {
        "namespace": ns,
        "label": meta["label"],
        "description": meta["description"],
        "sparql_endpoint": endpoint(ns),
        "ontology_prefix": meta["ontology_prefix"],
        "tbox_files": meta["tbox_files"],
        "tbox_class_count": summary["class_count"],
        "tbox_property_count": summary["property_count"],
        "online_probe_limit": ONLINE_PROBE_LIMIT,
    }
    if meta.get("endpoint_note"):
        payload["note"] = meta["endpoint_note"]
    return json.dumps(payload, indent=2)


def live_triple_count(ns: str) -> str:
    q = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
    try:
        rows = execute_sparql_get(q, endpoint(ns), timeout=sparql_timeout(ns))
        n = rows[0].get("n") if rows else "?"
        return format_tsv([{"namespace": ns, "live_triple_count": n}])
    except Exception as exc:
        return format_tsv([{"namespace": ns, "error": str(exc)[:200]}])


def top_instance_types(ns: str, limit: int = ONLINE_PROBE_LIMIT) -> str:
    cap = min(int(limit), ONLINE_PROBE_LIMIT)
    q = f"""
SELECT ?type (COUNT(?s) AS ?n) WHERE {{
  ?s a ?type .
}} GROUP BY ?type ORDER BY DESC(?n) LIMIT {cap}
"""
    try:
        return format_tsv(execute_sparql_get(q, endpoint(ns), timeout=sparql_timeout(ns)))
    except Exception as exc:
        return format_tsv([{"error": str(exc)[:200]}])


def sample_triples(ns: str, limit: int = ONLINE_PROBE_LIMIT) -> str:
    cap = min(int(limit), ONLINE_PROBE_LIMIT)
    q = f"SELECT ?s ?p ?o WHERE {{ ?s ?p ?o }} LIMIT {cap}"
    try:
        return format_tsv(execute_sparql_get(q, endpoint(ns), timeout=sparql_timeout(ns)))
    except Exception as exc:
        return format_tsv([{"error": str(exc)[:200]}])
