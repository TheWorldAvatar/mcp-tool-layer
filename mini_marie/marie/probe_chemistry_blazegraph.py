"""
Quick probe of https://theworldavatar.io/chemistry/blazegraph — reachability, namespaces, download size estimate.

Usage:
  python -m mini_marie.probe_chemistry_blazegraph
"""

from __future__ import annotations

import json
import re
import ssl
import time
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, parse, request

from mini_marie.cache_paths import mini_marie_cache_root

HOST = "https://theworldavatar.io"
BASE = f"{HOST}/chemistry/blazegraph"
UA = "curl/8.0"
CTX = ssl.create_default_context()
OUT = mini_marie_cache_root() / "chemistry_blazegraph" / "probe_report.json"

# Common Blazegraph namespace guesses (chemistry / compchem / marie stack)
NS_CANDIDATES = [
    "kb", "chemistry", "chem", "ontocompchem", "ontokin", "ontospecies", "ontomops",
    "ontomops_ogm", "ontozeolite", "marie", "rdf", "spARQL", "ontosyn", "ontoreaction",
    "species", "reactions", "compounds", "pubchem", "chebi", "wikidata",
]

ASK = "ASK { }"
COUNT_TRIPLES = "SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }"
COUNT_SUBJECTS = "SELECT (COUNT(DISTINCT ?s) AS ?n) WHERE { ?s ?p ?o }"
SAMPLE_TRIPLE = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1"
SAMPLE_PAGE = "SELECT ?s ?p ?o WHERE { ?s ?p ?o } ORDER BY ?s ?p ?o LIMIT 500"


def _http(url: str, *, timeout: float = 30, accept: str = "*/*") -> Dict[str, Any]:
    t0 = time.perf_counter()
    req = request.Request(url, headers={"User-Agent": UA, "Accept": accept}, method="GET")
    try:
        with request.urlopen(req, timeout=timeout, context=CTX) as resp:
            raw = resp.read()
            return {
                "ok": True,
                "status": resp.status,
                "bytes": len(raw),
                "ctype": resp.headers.get("Content-Type", ""),
                "ms": round((time.perf_counter() - t0) * 1000),
                "body_preview": raw[:400].decode("utf-8", errors="replace"),
            }
    except error.HTTPError as exc:
        body = exc.read(600).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:300]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:250], "ms": round((time.perf_counter() - t0) * 1000)}


def _sparql(endpoint: str, query: str, *, timeout: float = 120) -> Dict[str, Any]:
    t0 = time.perf_counter()
    url = endpoint + ("&" if "?" in endpoint else "?") + parse.urlencode({"query": query})
    req = request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
        method="GET",
    )
    try:
        with request.urlopen(req, timeout=timeout, context=CTX) as resp:
            raw = resp.read()
            payload = json.loads(raw.decode("utf-8"))
            rows = []
            for b in payload.get("results", {}).get("bindings", []):
                rows.append({k: v.get("value") for k, v in b.items()})
            return {
                "ok": True,
                "status": resp.status,
                "bytes": len(raw),
                "ms": round((time.perf_counter() - t0) * 1000),
                "rows": rows,
                "boolean": payload.get("boolean"),
            }
    except error.HTTPError as exc:
        body = exc.read(800).decode("utf-8", errors="replace")
        return {"ok": False, "http": exc.code, "ms": round((time.perf_counter() - t0) * 1000), "body": body[:400]}
    except Exception as exc:
        return {"ok": False, "error": str(exc)[:300], "ms": round((time.perf_counter() - t0) * 1000)}


def _val(rows: List[Dict], key: str = "n") -> Optional[int]:
    if rows and key in rows[0]:
        try:
            return int(float(rows[0][key]))
        except (TypeError, ValueError):
            pass
    return None


def probe_surface() -> Dict[str, Any]:
    print("=== HTTP surface ===", flush=True)
    paths = [
        "/chemistry/blazegraph/",
        "/chemistry/blazegraph/ui/",
        "/chemistry/blazegraph/sparql",
        "/chemistry/blazegraph/namespace/kb/sparql",
        "/chemistry/blazegraph/status",
        "/chemistry/blazegraph/counters",
    ]
    out = {}
    for p in paths:
        r = _http(HOST + p)
        out[p] = r
        tag = r.get("status") or r.get("http") or r.get("error", "?")[:40]
        print(f"  {p}: {tag} ({r.get('bytes', 0)} B)", flush=True)
    return out


def discover_namespaces() -> Dict[str, Any]:
    print("\n=== Namespace discovery ===", flush=True)
    found: Dict[str, Any] = {}
    # Try default sparql + candidate namespaces
    endpoints = [f"{BASE}/sparql"] + [f"{BASE}/namespace/{ns}/sparql" for ns in NS_CANDIDATES]
    for ep in endpoints:
        ask = _sparql(ep, ASK, timeout=15)
        if not ask.get("ok"):
            continue
        name = ep.split("/namespace/")[-1].split("/sparql")[0] if "/namespace/" in ep else "default"
        cnt = _sparql(ep, COUNT_TRIPLES, timeout=180)
        subj = _sparql(ep, COUNT_SUBJECTS, timeout=180)
        triples = _val(cnt.get("rows") or [])
        subjects = _val(subj.get("rows") or [])
        if triples is None and not (cnt.get("rows")):
            continue
        found[name] = {
            "endpoint": ep,
            "triples": triples,
            "subjects": subjects,
            "count_ms": cnt.get("ms"),
            "count_bytes": cnt.get("bytes"),
        }
        print(f"  [OK] {name}: {triples:,} triples, {subjects:,} subjects", flush=True)
    return found


def estimate_download(namespaces: Dict[str, Any]) -> Dict[str, Any]:
    print("\n=== Download size estimation ===", flush=True)
    estimates: Dict[str, Any] = {}
    total_triples = 0
    total_bytes_est = 0

    for name, meta in namespaces.items():
        ep = meta["endpoint"]
        triples = meta.get("triples") or 0
        total_triples += triples

        sample = _sparql(ep, SAMPLE_PAGE, timeout=120)
        if not sample.get("ok") or not sample.get("rows"):
            estimates[name] = {"error": "sample failed", **meta}
            continue

        sample_rows = len(sample["rows"])
        sample_bytes = sample.get("bytes") or 0
        bytes_per_row = sample_bytes / max(sample_rows, 1)

        # NDJSON export: one JSON object per triple line, typically similar to SPARQL JSON row size
        ndjson_est = int(bytes_per_row * triples)
        # Conservative overhead for full URIs in export (+20%)
        ndjson_est_high = int(ndjson_est * 1.2)

        # Paginated full SELECT pages @ 5000 (sg-old pattern)
        page_size = 5000
        pages = (triples + page_size - 1) // page_size if triples else 0
        # HTTP response overhead per page ~2KB
        http_pages_est = int(ndjson_est + pages * 2048)

        estimates[name] = {
            **meta,
            "sample_rows": sample_rows,
            "sample_json_bytes": sample_bytes,
            "bytes_per_triple_est": round(bytes_per_row, 1),
            "ndjson_download_est_bytes": ndjson_est,
            "ndjson_download_est_human": _human(ndjson_est),
            "ndjson_download_est_high_human": _human(ndjson_est_high),
            "export_pages_at_5000": pages,
            "paginated_http_est_human": _human(http_pages_est),
        }
        total_bytes_est += ndjson_est
        print(
            f"  {name}: {triples:,} triples × {bytes_per_row:.0f} B ≈ {_human(ndjson_est)} "
            f"({pages} pages @ 5000)",
            flush=True,
        )

    return {
        "per_namespace": estimates,
        "total_triples": total_triples,
        "total_ndjson_est_bytes": total_bytes_est,
        "total_ndjson_est_human": _human(total_bytes_est),
        "total_ndjson_est_high_human": _human(int(total_bytes_est * 1.2)),
        "method": "COUNT(*) + sample 500 rows JSON response bytes × triple count",
    }


def _human(n: int) -> str:
    if n >= 1_073_741_824:
        return f"{n / 1_073_741_824:.2f} GiB"
    if n >= 1_048_576:
        return f"{n / 1_048_576:.2f} MiB"
    if n >= 1024:
        return f"{n / 1024:.2f} KiB"
    return f"{n} B"


def try_status_api() -> Dict[str, Any]:
    """Blazegraph /status or counters if exposed."""
    out = {}
    for path in ["/chemistry/blazegraph/status", "/chemistry/blazegraph/counters", "/chemistry/blazegraph/namespace"]:
        r = _http(HOST + path, accept="application/json")
        out[path] = r
    return out


def build_report() -> Dict[str, Any]:
    t0 = time.perf_counter()
    namespaces = discover_namespaces()
    report = {
        "probed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "ui_url": f"{HOST}/chemistry/blazegraph/ui/#splash",
        "surface": probe_surface(),
        "status_api": try_status_api(),
        "namespaces": namespaces,
        "download_estimate": estimate_download(namespaces) if namespaces else {"note": "no namespaces found"},
        "elapsed_seconds": 0.0,
    }
    report["elapsed_seconds"] = round(time.perf_counter() - t0, 1)
    return report


def main() -> int:
    report = build_report()
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(report, indent=2, default=str), encoding="utf-8")
    de = report.get("download_estimate", {})
    print(f"\n=== Summary ===")
    print(f"Namespaces found: {len(report.get('namespaces', {}))}")
    print(f"Total triples: {de.get('total_triples', 0):,}")
    print(f"Est. full NDJSON download: {de.get('total_ndjson_est_human', '?')} "
          f"(up to {de.get('total_ndjson_est_high_human', '?')})")
    print(f"Wrote {OUT} ({report['elapsed_seconds']}s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
