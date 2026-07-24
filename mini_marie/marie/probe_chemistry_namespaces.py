"""Fast namespace probe for chemistry Blazegraph + OntoMOFs host."""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request

UA = "curl/8.0"
CTX = ssl.create_default_context()
TIMEOUT = 20

CHEM = "https://theworldavatar.io/chemistry/blazegraph/namespace"
MOF_ONTOP = "http://68.183.227.15:3840/ontop/sparql/"

NAMESPACES = [
    "kb", "ontocompchem", "ontokin", "ontomops", "ontopesscan",
    "ontoprovenance", "ontospecies", "ontozeolite", "ontomofs", "ontomof",
]


def count(endpoint: str) -> dict:
    q = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
    url = endpoint + ("&" if "?" in endpoint else "?") + urllib.parse.urlencode({"query": q})
    t0 = time.perf_counter()
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=CTX) as r:
            body = r.read().decode("utf-8", errors="replace")
            m = re.search(r'"value"\s*:\s*"(\d+)"', body)
            return {
                "ok": True,
                "status": r.status,
                "triples": int(m.group(1)) if m else None,
                "ms": round((time.perf_counter() - t0) * 1000),
            }
    except urllib.error.HTTPError as e:
        return {"ok": False, "http": e.code, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e)[:120], "ms": round((time.perf_counter() - t0) * 1000)}


def main() -> None:
    print("=== Chemistry Blazegraph (theworldavatar.io) ===\n")
    chem = {}
    for ns in NAMESPACES:
        ep = f"{CHEM}/{ns}/sparql"
        r = count(ep)
        chem[ns] = {"endpoint": ep, **r}
        tag = f"{r.get('triples'):,} triples" if r.get("triples") is not None else (r.get("http") or r.get("error"))
        print(f"  {ns}: {tag} ({r.get('ms')}ms)")

    print("\n=== OntoMOFs (separate stack — Ontop not Blazegraph) ===\n")
    mof = count(MOF_ONTOP)
    mof_mofs = count(MOF_ONTOP)  # same endpoint
    # MOF count query
    q2 = "SELECT (COUNT(?m) AS ?n) WHERE { ?m a <https://www.theworldavatar.com/kg/ontomofs_vkg/MetalOrganicFramework> }"
    url2 = MOF_ONTOP + "?" + urllib.parse.urlencode({"query": q2})
    req2 = urllib.request.Request(url2, headers={"User-Agent": UA, "Accept": "application/sparql-results+json"})
    mof_count = None
    try:
        with urllib.request.urlopen(req2, timeout=TIMEOUT) as r:
            body = r.read().decode()
            m = re.search(r'"value"\s*:\s*"(\d+)"', body)
            mof_count = int(m.group(1)) if m else None
    except Exception:
        pass
    print(f"  host: 68.183.227.15:3840")
    print(f"  endpoint: {MOF_ONTOP}")
    print(f"  all triples: {mof.get('triples')}")
    print(f"  MOF instances: {mof_count}")

    report = {
        "chemistry_blazegraph_host": "https://theworldavatar.io/chemistry/blazegraph",
        "chemistry_ui": "https://theworldavatar.io/chemistry/blazegraph/ui/#splash",
        "namespaces": chem,
        "ontomofs_host": "http://68.183.227.15:3840",
        "ontomofs_endpoint": MOF_ONTOP,
        "ontomofs_triples": mof.get("triples"),
        "ontomofs_mof_count": mof_count,
        "note": "ontomofs/MOFs live on Ontop at 68.183.227.15:3840; chemistry namespaces on theworldavatar.io/chemistry/blazegraph",
    }
    from mini_marie.cache_paths import mini_marie_cache_root
    out = mini_marie_cache_root() / "chemistry_blazegraph" / "namespace_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")


if __name__ == "__main__":
    main()
