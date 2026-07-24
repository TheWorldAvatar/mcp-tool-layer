"""
Probe Singapore / other Ontop SPARQL endpoints for buildings and land lots.

Usage:
  python -m mini_marie.zaha.sg_old.probe_ontop
  python -m mini_marie.zaha.sg_old.probe_ontop --json-out data/mini_marie_cache/ontop_probe.json
"""

from __future__ import annotations

import argparse
import json
import socket
import time
from pathlib import Path
from typing import Any, Dict, List
from urllib import error, parse, request

UA = "curl/8.0"

CANDIDATE_ONTOP_URLS = [
    "https://sg-old.theworldavatar.io/ontop/sparql/",
    "http://sg-old.theworldavatar.io/ontop/sparql/",
    "http://174.138.23.221:3839/ontop/sparql/",
    "http://174.138.23.221:3838/ontop/sparql/",
    "https://174.138.23.221:3839/ontop/sparql/",
    "http://174.138.23.221:3839/ontop/ui/sparql",
    "http://sg-ontop:8080/sparql",
    "http://sg-ontop:8080/ontop/sparql/",
]

DISCOVERY = {
    "ping": "ASK { }",
    "building_count": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <https://www.theworldavatar.com/kg/ontobuiltenv/Building> .
}
""",
    "building_count_citygml": """
SELECT (COUNT(?b) AS ?n) WHERE {
  ?b a <http://www.opengis.net/citygml/building/2.0/Building> .
}
""",
    "landlot_count": """
SELECT (COUNT(?l) AS ?n) WHERE {
  ?l a <http://www.theworldavatar.com/kg/ontocompany/LandLot> .
}
""",
    "landlot_details_count": """
SELECT (COUNT(?l) AS ?n) WHERE {
  ?l a <http://www.theworldavatar.com/kg/ontocompany/LandLotDetails> .
}
""",
    "landplot_count": """
SELECT (COUNT(?l) AS ?n) WHERE {
  { ?l a <https://www.theworldavatar.com/kg/ontoplot/LandPlot> . }
  UNION
  { ?l a <http://www.theworldavatar.com/kg/ontoplot/LandPlot> . }
}
""",
    "gfa_triples": """
SELECT (COUNT(*) AS ?n) WHERE {
  ?s ?p ?o .
  FILTER(CONTAINS(LCASE(STR(?p)), "gfa") || CONTAINS(LCASE(STR(?p)), "grossplotratio"))
}
""",
    "sample_building": """
SELECT ?b ?label WHERE {
  ?b a <https://www.theworldavatar.com/kg/ontobuiltenv/Building> .
  OPTIONAL { ?b <http://www.w3.org/2000/01/rdf-schema#label> ?label }
} LIMIT 3
""",
    "sample_landlot": """
SELECT ?l ?type WHERE {
  ?l a ?type .
  FILTER(
    CONTAINS(STR(?type), "LandLot") ||
    CONTAINS(STR(?type), "LandPlot") ||
    CONTAINS(STR(?type), "landplot")
  )
} LIMIT 5
""",
}


def tcp_open(host: str, port: int, timeout: float = 3) -> Dict[str, Any]:
    t0 = time.perf_counter()
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        return {"open": True, "ms": round((time.perf_counter() - t0) * 1000)}
    except OSError as e:
        return {"open": False, "ms": round((time.perf_counter() - t0) * 1000), "error": str(e)}
    finally:
        s.close()


def sparql_get(url: str, query: str, timeout: float) -> Dict[str, Any]:
    full = url + ("&" if "?" in url else "?") + parse.urlencode({"query": query})
    req = request.Request(
        full,
        headers={"Accept": "application/sparql-results+json", "User-Agent": UA},
    )
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": r.status, "ms": round((time.perf_counter() - t0) * 1000), "json": json.loads(body)}
    except error.HTTPError as e:
        return {"ok": False, "http": e.code, "ms": round((time.perf_counter() - t0) * 1000), "body": e.read(300).decode(errors="replace")}
    except Exception as e:
        return {"ok": False, "error": str(e), "ms": round((time.perf_counter() - t0) * 1000)}


def sparql_post(url: str, query: str, timeout: float) -> Dict[str, Any]:
    req = request.Request(
        url,
        data=query.encode(),
        method="POST",
        headers={
            "Accept": "application/sparql-results+json",
            "Content-Type": "application/sparql-query",
            "User-Agent": UA,
        },
    )
    t0 = time.perf_counter()
    try:
        with request.urlopen(req, timeout=timeout) as r:
            body = r.read().decode("utf-8", errors="replace")
            return {"ok": True, "status": r.status, "ms": round((time.perf_counter() - t0) * 1000), "json": json.loads(body)}
    except error.HTTPError as e:
        return {"ok": False, "http": e.code, "ms": round((time.perf_counter() - t0) * 1000)}
    except Exception as e:
        return {"ok": False, "error": str(e), "ms": round((time.perf_counter() - t0) * 1000)}


def bindings(result: Dict[str, Any]) -> List[Dict[str, str]]:
    if not result.get("ok"):
        return []
    data = result.get("json") or {}
    out: List[Dict[str, str]] = []
    for b in data.get("results", {}).get("bindings", []):
        row = {k: v.get("value", "") for k, v in b.items()}
        out.append(row)
    return out


def _host_port(url: str) -> tuple[str | None, int | None]:
    from urllib.parse import urlparse

    p = urlparse(url)
    return p.hostname, p.port or (443 if p.scheme == "https" else 80)


def probe_url(url: str, timeout: float, tcp_cache: Dict[str, Dict[str, Any]]) -> Dict[str, Any]:
    out: Dict[str, Any] = {"url": url, "queries": {}}
    host, port = _host_port(url)
    if host and port:
        key = f"{host}:{port}"
        if key not in tcp_cache:
            tcp_cache[key] = tcp_open(host, port)
        out["tcp"] = tcp_cache[key]
        if not tcp_cache[key].get("open"):
            out["reachable"] = False
            out["queries"]["ping"] = {"ok": False, "skipped": "tcp_closed"}
            return out
    # Try GET first (sg-old style), then POST (ontop style)
    for name, q in DISCOVERY.items():
        r = sparql_get(url, q, timeout)
        if not r.get("ok"):
            r = sparql_post(url, q, timeout)
        out["queries"][name] = {
            "ok": r.get("ok"),
            "ms": r.get("ms"),
            "http": r.get("http"),
            "error": r.get("error"),
            "rows": bindings(r),
        }
        if r.get("ok"):
            out["reachable"] = True
            out["method"] = "get" if out["queries"][name].get("ok") else "post"
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe Ontop for buildings and land lots")
    parser.add_argument("--timeout", type=float, default=12)
    parser.add_argument("--json-out", type=Path, default=Path("data/mini_marie_cache/ontop_probe.json"))
    args = parser.parse_args()

    report: Dict[str, Any] = {"tcp": {}, "endpoints": []}
    for host, port in [("sg-old.theworldavatar.io", 443), ("174.138.23.221", 3839), ("174.138.23.221", 3838)]:
        report["tcp"][f"{host}:{port}"] = tcp_open(host, port)

    print("TCP:")
    for k, v in report["tcp"].items():
        print(f"  {k}: {'OPEN' if v['open'] else 'CLOSED'}")

    print("\nOntop SPARQL probes:")
    for url in CANDIDATE_ONTOP_URLS:
        print(f"\n  {url}")
        ep = probe_url(url, args.timeout, report["tcp"])
        report["endpoints"].append(ep)
        if not ep.get("reachable"):
            ping = ep["queries"].get("ping", {})
            print(f"    UNREACHABLE: {ping.get('error') or ping.get('http')}")
            continue
        print("    REACHABLE")
        for key in ["building_count", "building_count_citygml", "landlot_count", "landplot_count", "gfa_triples"]:
            q = ep["queries"].get(key, {})
            val = (q.get("rows") or [{}])[0] if q.get("ok") else None
            print(f"    {key}: {val or q.get('error') or q.get('http')}")

    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.json_out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
