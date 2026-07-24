"""GET-based SPARQL client for sg-old (POST blocked; Python UA often 403)."""

from __future__ import annotations

import json
from typing import Any, Dict, List
from urllib import error, parse, request

DEFAULT_UA = "curl/8.0"
DEFAULT_TIMEOUT = 120


def execute_sparql_get(
    query: str,
    endpoint: str,
    *,
    timeout: int = DEFAULT_TIMEOUT,
    user_agent: str = DEFAULT_UA,
) -> List[Dict[str, Any]]:
    full = endpoint + ("&" if "?" in endpoint else "?") + parse.urlencode({"query": query})
    req = request.Request(
        full,
        headers={
            "Accept": "application/sparql-results+json",
            "User-Agent": user_agent,
        },
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout) as resp:
            payload = json.loads(resp.read().decode("utf-8"))
    except error.HTTPError as exc:
        body = exc.read().decode("utf-8", errors="replace")
        raise RuntimeError(f"SPARQL HTTP {exc.code} at {endpoint}: {body[:500]}") from exc
    except error.URLError as exc:
        raise RuntimeError(f"SPARQL request failed for {endpoint}: {exc.reason}") from exc

    rows: List[Dict[str, Any]] = []
    for binding in payload.get("results", {}).get("bindings", []):
        row: Dict[str, Any] = {}
        for var, term in binding.items():
            row[var] = term.get("value")
        rows.append(row)
    return rows
