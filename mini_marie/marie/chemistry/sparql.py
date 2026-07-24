"""GET-based SPARQL for chemistry Blazegraph (theworldavatar.io)."""

from __future__ import annotations

import json
import ssl
import urllib.parse
import urllib.request
from typing import Any, Dict, List

UA = "curl/8.0"
CTX = ssl.create_default_context()
DEFAULT_TIMEOUT = 90


def execute_sparql_get(
    query: str,
    endpoint: str,
    timeout: int = DEFAULT_TIMEOUT,
) -> List[Dict[str, Any]]:
    url = endpoint + "?" + urllib.parse.urlencode({"query": query})
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
    )
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
        payload = json.loads(resp.read().decode("utf-8"))
    rows: List[Dict[str, Any]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        rows.append({var: term.get("value") for var, term in binding.items()})
    return rows


def format_tsv(rows: List[Dict[str, Any]]) -> str:
    if not rows:
        return "No results"
    keys = list(rows[0].keys())
    lines = ["\t".join(keys)]
    for row in rows:
        lines.append("\t".join(str(row.get(k, "")) for k in keys))
    return "\n".join(lines)
