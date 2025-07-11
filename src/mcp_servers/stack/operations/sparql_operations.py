#!/usr/bin/env python
"""
SPARQL operations
Functions for querying SPARQL endpoints and processing results.
"""

import json
from typing import Dict, List, Any, Literal, Optional
import requests
import logging

# Setup logger
logger = logging.getLogger("ontop_sparql_agent")

def _post_sparql(endpoint: str, query: str) -> Dict[str, Any]:
    """POST a SPARQL query, expect JSON bindings, raise for HTTP ≠ 200."""
    headers = {
        "Accept": "application/sparql-results+json",
        # YASGUI sends form-urlencoded; we do the same
        "Content-Type": "application/x-www-form-urlencoded",
    }
    resp = requests.post(endpoint, data={"query": query}, headers=headers, timeout=60)
    resp.raise_for_status()
    return resp.json()

def _simplify_bindings(raw: Dict[str, Any]) -> List[Dict[str, str]]:
    """
    Convert SPARQL 1.1 JSON results into a list of plain dicts:
    {"var": {"type":"literal","value":"42",…}}  →  {"var":"42", …}
    """
    out: List[Dict[str, str]] = []
    for row in raw.get("results", {}).get("bindings", []):
        out.append({var: cell["value"] for var, cell in row.items()})
    return out

def query_sparql(
    *,
    endpoint_url: str = "http://localhost:3838/ontop/ui/sparql",
    query: str,
    raw_json: bool = False,
) -> Any:
    try:
        resp = _post_sparql(endpoint_url, query)
    except Exception as exc:  # noqa: broad-except
        # Suppress error and return a soft-failure response
        logger.warning("SPARQL request failed (suppressed): %s", exc)
        return {
            "status": "skipped",
            "error": str(exc),
            "note": "SPARQL query was skipped due to connection or runtime error."
        }

    if "boolean" in resp:
        return resp["boolean"]

    if raw_json:
        return resp

    return _simplify_bindings(resp) 