"""Fast ASK probe — confirms namespace exists without full COUNT."""
from __future__ import annotations

import json
import ssl
import time
import urllib.parse
import urllib.request

CHEM = "https://theworldavatar.io/chemistry/blazegraph/namespace"
UA = "curl/8.0"
CTX = ssl.create_default_context()

NS = ["kb", "ontocompchem", "ontokin", "ontomops", "ontopesscan", "ontoprovenance", "ontospecies", "ontozeolite"]


def ask(ns: str, timeout: int = 15) -> dict:
    ep = f"{CHEM}/{ns}/sparql"
    q = "ASK { ?s ?p ?o }"
    url = ep + "?" + urllib.parse.urlencode({"query": q})
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            body = r.read().decode()
            ok = '"boolean" : true' in body or '"boolean":true' in body or '"boolean": true' in body
            return {"exists": ok, "status": r.status, "ms": round((time.perf_counter() - t0) * 1000)}
    except urllib.error.HTTPError as e:
        return {"exists": False, "http": e.code, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as e:
        return {"exists": None, "error": str(e)[:80], "ms": round((time.perf_counter() - t0) * 1000)}


def sample(ns: str, timeout: int = 30) -> dict:
    ep = f"{CHEM}/{ns}/sparql"
    q = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1"
    url = ep + "?" + urllib.parse.urlencode({"query": q})
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    t0 = time.perf_counter()
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
            raw = r.read()
            return {"ok": True, "bytes": len(raw), "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:80], "ms": round((time.perf_counter() - t0) * 1000)}


def main() -> None:
    print("Host: https://theworldavatar.io/chemistry/blazegraph\n")
    out = {}
    for ns in NS:
        a = ask(ns)
        s = sample(ns) if a.get("exists") else {"skipped": True}
        out[ns] = {"endpoint": f"{CHEM}/{ns}/sparql", "ask": a, "sample": s}
        print(f"  {ns}: ask={a.get('exists')} ({a.get('ms')}ms) sample={s.get('ok', s.get('http', s.get('error')))}")

    from mini_marie.cache_paths import mini_marie_cache_root
    p = mini_marie_cache_root() / "chemistry_blazegraph" / "ask_probe.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"\nWrote {p}")


if __name__ == "__main__":
    main()
