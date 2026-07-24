"""Health checks for fragile chemistry Blazegraph corpus warms."""

from __future__ import annotations

import ssl
import time
import urllib.parse
import urllib.request
from typing import Any, Dict

from mini_marie.marie.chemistry.registry import endpoint

UA = "curl/8.0"
CTX = ssl.create_default_context()


def namespace_health_ok(namespace: str, timeout: int = 45) -> Dict[str, Any]:
    """Lightweight ASK before a corpus warm batch."""
    url = endpoint(namespace) + "?" + urllib.parse.urlencode({"query": "ASK { ?s ?p ?o }"})
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"}
    )
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as resp:
            body = resp.read().decode("utf-8", errors="replace")
            ok = '"boolean": true' in body.lower() or "true" in body.lower()
            return {
                "namespace": namespace,
                "ok": ok,
                "ms": round((time.perf_counter() - t0) * 1000),
            }
    except Exception as exc:
        return {
            "namespace": namespace,
            "ok": False,
            "error": str(exc)[:200],
            "ms": round((time.perf_counter() - t0) * 1000),
        }
