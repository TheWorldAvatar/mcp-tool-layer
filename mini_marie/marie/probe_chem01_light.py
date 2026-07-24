"""Light LIMIT 1 probe on chem-01 droplet (178.128.105.213:3838)."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

HOSTS = {
    "chem01_ip": "http://178.128.105.213:3838/blazegraph",
    "theworldavatar_io": "https://theworldavatar.io/chemistry/blazegraph",
}
UA = "curl/8.0"

NS = [
    "kb", "ontocompchem", "ontokin", "ontomops", "ontopesscan",
    "ontoprovenance", "ontospecies", "ontozeolite",
]


def sparql(host: str, ns: str, query: str, timeout: int = 30) -> dict:
    import ssl

    url = f"{host}/namespace/{ns}/sparql?" + urllib.parse.urlencode({"query": query})
    ctx = ssl.create_default_context() if url.startswith("https") else None
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as r:
            body = r.read(3000).decode("utf-8", errors="replace")
            rows = re.findall(r'"value"\s*:\s*"([^"]*)"', body)
            return {"ok": True, "status": r.status, "ms": round((time.perf_counter() - t0) * 1000), "values": rows[:6], "raw": body[:400]}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:100], "ms": round((time.perf_counter() - t0) * 1000)}


def main() -> None:
    report = {}
    for hname, host in HOSTS.items():
        print(f"=== {hname}: {host} ===\n")
        report[hname] = {}
        for ns in NS:
            limit1 = sparql(host, ns, "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1", timeout=45)
            report[hname][ns] = {"limit1": limit1}
            if limit1.get("ok"):
                empty = '"bindings" : [ ]' in limit1.get("raw", "")
                tag = "empty" if empty else " | ".join(limit1.get("values", [])[:3])
                print(f"  {ns}: OK ({limit1['ms']}ms) {tag[:120]}")
            else:
                print(f"  {ns}: {limit1.get('error')} ({limit1.get('ms')}ms)")
        print()

    from mini_marie.cache_paths import mini_marie_cache_root
    out = mini_marie_cache_root() / "chemistry_blazegraph" / "chem01_vs_twa_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
