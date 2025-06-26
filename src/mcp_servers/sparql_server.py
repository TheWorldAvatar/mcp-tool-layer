#!/usr/bin/env python
"""
ontop_sparql_agent.py · ONE exposed tool: query_sparql
Relays SPARQL queries to an existing endpoint and returns the results.

How to start (from repo root):
    $ python -m ontop_sparql_agent         # STDIO mode
or  $ python ontop_sparql_agent.py --port 3333   # FastAPI mode, optional
"""

import json
from typing import Dict, List, Any, Literal, Optional

import requests
from mcp.server.fastmcp import FastMCP
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

mcp = FastMCP("OntopSPARQLEndpoint")

################################################################################
# Utility
################################################################################
def _post_sparql(endpoint: str, query: str) -> Dict[str, Any]:
    """POST a SPARQL query, expect JSON bindings, raise for HTTP ≠ 200."""
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

################################################################################
# MCP tool
################################################################################
@mcp.tool("query_sparql")
def query_sparql(
    *,
    endpoint_url: str = "http://localhost:3838/ontop/ui/sparql",
    query: str,
    raw_json: bool = False,
) -> Any:
    """
    Send a SPARQL SELECT/ASK query to **endpoint_url** and return the answer.

    Args
    ----
    endpoint_url : full URL, e.g. "http://localhost:3838/ontop/ui/sparql"
    query        : the SPARQL 1.1 string
    raw_json     : if True, return the full SPARQL JSON document;
                   if False (default), return a simplified list of rows

    Returns
    -------
    • SELECT → list of dicts  (unless raw_json=True)
    • ASK    → bool
    • other  → raw JSON (construct queries are not altered)
    """
    try:
        resp = _post_sparql(endpoint_url, query)
    except Exception as exc:  # noqa: broad-except
        # Surface HTTP / connection errors nicely to the LLM caller
        raise RuntimeError(f"SPARQL request failed: {exc}") from exc

    # ASK queries have a boolean top-level key
    if "boolean" in resp:
        return resp["boolean"]

    if raw_json:
        return resp

    return _simplify_bindings(resp)





################################################################################
# Local test driver ------------------------------------------------------------
################################################################################
if __name__ == "__main__":
    mcp.run(transport="stdio")  # uncomment to expose via stdio
 