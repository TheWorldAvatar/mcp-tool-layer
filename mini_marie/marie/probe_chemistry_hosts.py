"""Probe chemistry KG namespaces across known TWA hosts."""
from __future__ import annotations

import json
import re
import time
import urllib.parse
import urllib.request

UA = "curl/8.0"
NS = ["kb", "ontocompchem", "ontokin", "ontomops", "ontopesscan", "ontoprovenance", "ontospecies", "ontozeolite", "ontomops_ogm"]

HOSTS = {
    "chemistry_twa": "https://theworldavatar.io/chemistry/blazegraph/namespace/{ns}/sparql",
    "178_128_3838": "http://178.128.105.213:3838/blazegraph/namespace/{ns}/sparql",
    "68_183_3838": "http://68.183.227.15:3838/blazegraph/namespace/{ns}/sparql",
    "68_183_3840_ontop_mof": "http://68.183.227.15:3840/ontop/sparql/",
}


def query(url: str, q: str, timeout: int) -> dict:
    full = url + ("&" if "?" in url else "?") + urllib.parse.urlencode({"query": q})
    t0 = time.perf_counter()
    req = urllib.request.Request(full, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            m = re.search(r'"value"\s*:\s*"([^"]+)"', body)
            return {"ok": True, "status": r.status, "value": m.group(1) if m else body[:60], "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as exc:
        return {"ok": False, "error": type(exc).__name__, "detail": str(exc)[:80], "ms": round((time.perf_counter() - t0) * 1000)}


def main() -> None:
    report: dict = {"hosts": {}, "ontomofs": {}}
    for hname, pat in HOSTS.items():
        if "{ns}" not in pat:
            # OntoMOFs single endpoint
            c = query(pat, "SELECT (COUNT(?m) AS ?n) WHERE { ?m a <https://www.theworldavatar.com/kg/ontomofs_vkg/MetalOrganicFramework> }", 30)
            report["ontomofs"] = {"host": "68.183.227.15:3840", "endpoint": pat, "mof_count": c}
            print(f"OntoMOFs: {c}")
            continue
        print(f"\n=== {hname} ===")
        report["hosts"][hname] = {}
        timeout = 12 if hname == "chemistry_twa" else 45
        for ns in NS:
            ep = pat.format(ns=ns)
            ask = query(ep, "ASK { ?s ?p ?o }", timeout)
            entry = {"endpoint": ep, "ask": ask}
            if ask.get("ok") and ask.get("value") == "true":
                cnt = query(ep, "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", min(timeout * 4, 180))
                entry["count"] = cnt
            report["hosts"][hname][ns] = entry
            tag = ask.get("value") or ask.get("error")
            extra = ""
            if entry.get("count", {}).get("value"):
                extra = f" count={entry['count']['value']}"
            print(f"  {ns}: {tag}{extra} ({ask.get('ms')}ms)")

    from mini_marie.cache_paths import mini_marie_cache_root
    out = mini_marie_cache_root() / "chemistry_blazegraph" / "hosts_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
