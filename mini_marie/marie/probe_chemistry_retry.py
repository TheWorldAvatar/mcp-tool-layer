"""Retry chemistry Blazegraph namespaces with longer timeouts."""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request

HOST = "https://theworldavatar.io/chemistry/blazegraph/namespace"
UA = "curl/8.0"
CTX = ssl.create_default_context()

NS = [
    "kb", "ontocompchem", "ontokin", "ontomops", "ontopesscan",
    "ontoprovenance", "ontospecies", "ontozeolite", "ontomops_ogm",
]

QUERIES = {
    "ask": ("ASK { ?s ?p ?o }", 45),
    "limit1": ("SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1", 90),
    "count": ("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", 300),
}


def run(endpoint: str, query: str, timeout: int) -> dict:
    url = endpoint + "?" + urllib.parse.urlencode({"query": query})
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read()
            text = body.decode("utf-8", errors="replace")
            m = re.search(r'"value"\s*:\s*"([^"]+)"', text)
            return {
                "ok": True,
                "status": r.status,
                "bytes": len(body),
                "value": m.group(1) if m else ("true" if "true" in text.lower() else text[:80]),
                "ms": round((time.perf_counter() - t0) * 1000),
            }
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "detail": str(exc)[:120],
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def main() -> None:
    # UI ping first
    ui = run("https://theworldavatar.io/chemistry/blazegraph/ui/", "ASK { }", 30)
    print(f"UI ping: {ui}\n")

    report: dict = {"ui": ui, "namespaces": {}, "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
    for ns in NS:
        ep = f"{HOST}/{ns}/sparql"
        print(f"=== {ns} ===", flush=True)
        entry = {"endpoint": ep}
        for qname, (q, timeout) in QUERIES.items():
            r = run(ep, q, timeout)
            entry[qname] = r
            tag = r.get("value") if r.get("ok") else r.get("error")
            print(f"  {qname}: {tag} ({r.get('ms')}ms)", flush=True)
            if not r.get("ok") and qname == "ask":
                break  # skip heavier queries if ASK fails
        report["namespaces"][ns] = entry
        print()

    from mini_marie.cache_paths import mini_marie_cache_root
    out = mini_marie_cache_root() / "chemistry_blazegraph" / "retry_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"Wrote {out}")


if __name__ == "__main__":
    main()
