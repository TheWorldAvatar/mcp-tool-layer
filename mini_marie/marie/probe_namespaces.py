"""
Scan known SPARQL endpoints: connectivity, sample triples, namespace discovery.

Usage:
  python -m mini_marie.probe_namespaces
  python -m mini_marie.probe_namespaces --json-out data/mini_marie_cache/namespace_scan.json
  python -m mini_marie.probe_namespaces --endpoint http://68.183.227.15:3840/ontop/sparql/
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from mini_marie.zaha.probe_endpoint_matrix import CANDIDATE_URLS, TCP_MATRIX
from mini_marie.zaha.twa_city.twa_city_operations import execute_sparql, probe_sparql_endpoint, tcp_port_open

# Known TWA / project namespaces to COUNT-probe (http vs https variants matter for 3838/3839)
KNOWN_NAMESPACE_PREFIXES: List[str] = [
    "http://www.theworldavatar.com/kg/ontocompany/",
    "https://www.theworldavatar.com/kg/ontocompany/",
    "https://www.theworldavatar.com/kg/ontomofs_vkg/",
    "http://www.theworldavatar.com/kg/ontomops/",
    "https://www.theworldavatar.com/kg/ontomops/",
    "https://www.theworldavatar.com/kg/OntoSyn/",
    "https://www.theworldavatar.com/kg/ontobuiltenv/",
    "https://www.theworldavatar.com/kg/ontobuiltenv#",
    "https://theworldavatar.io/kg/",
    "http://www.theworldavatar.com/ontology/ontospecies/OntoSpecies.owl#",
    "http://www.opengis.net/citygml/building/2.0/",
    "http://www.opengis.net/ont/geosparql#",
    "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "http://www.w3.org/2000/01/rdf-schema#",
    "https://www.theworldavatar.com/kg/ontoplot/",
    "http://www.theworldavatar.com/kg/ontoplot/",
]


def namespace_root(term: str) -> str:
    """Map an IRI to a namespace root (trailing / or #)."""
    if not term or not str(term).startswith(("http://", "https://")):
        return ""
    t = str(term).strip()
    if "#" in t:
        return t.rsplit("#", 1)[0] + "#"
    parsed = urlparse(t)
    if not parsed.scheme or not parsed.netloc:
        return ""
    path = parsed.path or ""
    if path.endswith((".owl", ".ttl", ".rdf")):
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    if path:
        # Keep directory-style KG roots (drop last segment if instance-like)
        parts = [p for p in path.split("/") if p]
        if len(parts) >= 2 and re.search(r"[0-9a-f-]{8,}|mof_|Building/", t, re.I):
            path = "/" + "/".join(parts[:-1]) + "/"
        else:
            path = "/" + "/".join(parts) + ("/" if not path.endswith("/") else "")
        return f"{parsed.scheme}://{parsed.netloc}{path}"
    return f"{parsed.scheme}://{parsed.netloc}/"


def collect_namespaces_from_rows(rows: List[Dict[str, Any]]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in rows:
        for val in row.values():
            if not val:
                continue
            ns = namespace_root(str(val))
            if ns:
                counts[ns] += 1
    return counts


def count_triples_with_prefix(endpoint: str, prefix: str, timeout: int) -> Optional[int]:
    """COUNT subjects or any term starting with prefix (bounded)."""
    esc = prefix.replace("\\", "\\\\").replace('"', '\\"')
    query = f"""
SELECT (COUNT(*) AS ?n) WHERE {{
  {{
    ?s ?p ?o .
    FILTER(
      STRSTARTS(STR(?s), "{esc}") ||
      STRSTARTS(STR(?p), "{esc}") ||
      STRSTARTS(STR(?o), "{esc}")
    )
  }}
}}
"""
    try:
        rows = execute_sparql(query, endpoint=endpoint, timeout=timeout)
        if rows and rows[0].get("n") is not None:
            return int(rows[0]["n"])
    except Exception:
        return None
    return None


def scan_endpoint(url: str, *, sample_limit: int, timeout: int, probe_known: bool) -> Dict[str, Any]:
    ping = probe_sparql_endpoint(url, timeout=min(timeout, 15))
    out: Dict[str, Any] = {"url": url, "ping": ping}
    if not ping.get("ok"):
        return out

    sample_q = f"SELECT ?s ?p ?o WHERE {{ ?s ?p ?o }} LIMIT {int(sample_limit)}"
    try:
        sample_rows = execute_sparql(sample_q, endpoint=url, timeout=timeout)
        out["sample_rows"] = len(sample_rows)
        ns_counts = collect_namespaces_from_rows(sample_rows)
        out["namespaces_from_sample"] = [
            {"namespace": ns, "hits": c}
            for ns, c in ns_counts.most_common(40)
        ]
    except Exception as exc:
        out["sample_error"] = str(exc)
        sample_rows = []

    if probe_known:
        known: List[Dict[str, Any]] = []
        for prefix in KNOWN_NAMESPACE_PREFIXES:
            n = count_triples_with_prefix(url, prefix, timeout=min(timeout, 45))
            if n is not None and n > 0:
                known.append({"prefix": prefix, "triple_hits": n})
        known.sort(key=lambda x: -x["triple_hits"])
        out["known_namespace_hits"] = known

    # Type roots (common discovery)
    type_q = """
SELECT ?type (COUNT(?s) AS ?n) WHERE {
  ?s a ?type .
} GROUP BY ?type ORDER BY DESC(?n) LIMIT 15
"""
    try:
        types = execute_sparql(type_q, endpoint=url, timeout=timeout)
        out["top_types"] = types
        for row in types:
            ns = namespace_root(str(row.get("type", "")))
            if ns and "namespaces_from_sample" in out:
                pass
    except Exception as exc:
        out["types_error"] = str(exc)

    return out


def main() -> int:
    parser = argparse.ArgumentParser(description="Scan endpoints and discover RDF namespaces")
    parser.add_argument("--json-out", type=Path, default=Path("data/mini_marie_cache/namespace_scan.json"))
    parser.add_argument("--endpoint", action="append", help="Only scan these URLs (repeatable)")
    parser.add_argument("--sample-limit", type=int, default=150)
    parser.add_argument("--timeout", type=int, default=60)
    parser.add_argument("--skip-known-counts", action="store_true", help="Skip slow COUNT per known prefix")
    parser.add_argument("--tcp", action="store_true", help="Include TCP port matrix")
    args = parser.parse_args()

    urls = [args.endpoint] if args.endpoint else [e["url"] for e in CANDIDATE_URLS]
    report: Dict[str, Any] = {
        "known_prefixes_checked": KNOWN_NAMESPACE_PREFIXES,
        "endpoints": [],
    }

    if args.tcp:
        report["tcp"] = [
            {"host": h, "port": p, "open": tcp_port_open(h, p)}
            for h, ports in TCP_MATRIX
            for p in ports
        ]

    working: List[str] = []
    for url in urls:
        print(f"Scanning {url} ...", flush=True)
        ep_report = scan_endpoint(
            url,
            sample_limit=args.sample_limit,
            timeout=args.timeout,
            probe_known=not args.skip_known_counts,
        )
        report["endpoints"].append(ep_report)
        if ep_report.get("ping", {}).get("ok"):
            working.append(url)
            ns_top = ep_report.get("namespaces_from_sample") or []
            print(f"  OK sample={ep_report.get('sample_rows', 0)} namespaces={len(ns_top)}")
            for item in ns_top[:8]:
                print(f"    {item['namespace']} ({item['hits']})")
            known = ep_report.get("known_namespace_hits") or []
            if known:
                print(f"  known hits: {len(known)}")
                for item in known[:5]:
                    print(f"    {item['prefix']} -> {item['triple_hits']}")
        else:
            err = ep_report.get("ping", {}).get("error", "")
            print(f"  FAIL {str(err)[:100]}")

    report["working_endpoints"] = working
    args.json_out.parent.mkdir(parents=True, exist_ok=True)
    args.json_out.write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(f"\nWrote {args.json_out}")
    print(f"Working: {len(working)}/{len(urls)}")
    return 0 if working else 1


if __name__ == "__main__":
    raise SystemExit(main())
