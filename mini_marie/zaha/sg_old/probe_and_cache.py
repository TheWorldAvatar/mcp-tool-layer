"""
Systematic probe + full triple export for sg-old Blazegraph namespaces.

Usage:
  python -m mini_marie.zaha.sg_old.probe_and_cache
  python -m mini_marie.zaha.sg_old.probe_and_cache --namespace carpark
  python -m mini_marie.zaha.sg_old.probe_and_cache --skip-download
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, Dict, List, Optional, Set
from urllib.parse import urlparse

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get

HOST = "https://sg-old.theworldavatar.io"
WORKING_NAMESPACES = ["carpark", "company", "plot", "kb"]

DISCOVERY_QUERIES = {
    "stats": """
SELECT
  (COUNT(*) AS ?triples)
  (COUNT(DISTINCT ?s) AS ?subjects)
  (COUNT(DISTINCT ?p) AS ?predicates)
  (COUNT(DISTINCT ?o) AS ?objects)
WHERE { ?s ?p ?o }
""",
    "top_types": """
SELECT ?type (COUNT(?x) AS ?c) WHERE {
  ?x a ?type .
} GROUP BY ?type ORDER BY DESC(?c) LIMIT 40
""",
    "top_predicates": """
SELECT ?p (COUNT(*) AS ?c) WHERE {
  ?s ?p ?o .
} GROUP BY ?p ORDER BY DESC(?c) LIMIT 40
""",
    "literal_predicates": """
SELECT ?p (COUNT(*) AS ?c) WHERE {
  ?s ?p ?o .
  FILTER(isLiteral(?o))
} GROUP BY ?p ORDER BY DESC(?c) LIMIT 25
""",
    "uri_object_predicates": """
SELECT ?p (COUNT(*) AS ?c) WHERE {
  ?s ?p ?o .
  FILTER(isIRI(?o) && !isLiteral(?o))
} GROUP BY ?p ORDER BY DESC(?c) LIMIT 25
""",
}


def endpoint_for(ns: str) -> str:
    return f"{HOST}/blazegraph/namespace/{ns}/sparql"


def namespace_root(term: str) -> str:
    if not term or not str(term).startswith(("http://", "https://")):
        return ""
    t = str(term).strip()
    if "#" in t:
        return t.rsplit("#", 1)[0] + "#"
    p = urlparse(t)
    parts = [x for x in (p.path or "").split("/") if x]
    if len(parts) >= 2:
        return f"{p.scheme}://{p.netloc}/" + "/".join(parts[:-1]) + "/"
    return f"{p.scheme}://{p.netloc}/"


def prefix_histogram(rows: List[Dict[str, Any]], keys: List[str], limit: int = 35) -> List[Dict[str, Any]]:
    counts: Counter[str] = Counter()
    for row in rows:
        for k in keys:
            v = row.get(k)
            if v:
                ns = namespace_root(str(v))
                if ns:
                    counts[ns] += 1
    return [{"namespace": ns, "hits": c} for ns, c in counts.most_common(limit)]


def run_discovery(ns: str, timeout: int) -> Dict[str, Any]:
    ep = endpoint_for(ns)
    out: Dict[str, Any] = {"namespace": ns, "endpoint": ep, "queries": {}}
    for name, q in DISCOVERY_QUERIES.items():
        rows = execute_sparql_get(q, ep, timeout=timeout)
        out["queries"][name] = rows
    # Sample instance IRIs (non-ontology)
    sample_q = """
SELECT DISTINCT ?s WHERE {
  ?s ?p ?o .
  FILTER(isIRI(?s))
} LIMIT 80
"""
    samples = execute_sparql_get(sample_q, ep, timeout=timeout)
    out["sample_subjects"] = [r["s"] for r in samples if r.get("s")]
    out["prefixes_from_samples"] = prefix_histogram(
        [{"s": s} for s in out["sample_subjects"]], ["s"]
    )
    return out


def download_all_triples(
    ns: str,
    *,
    page_size: int,
    timeout: int,
    out_dir: Path,
) -> Dict[str, Any]:
    ep = endpoint_for(ns)
    all_rows: List[Dict[str, str]] = []
    offset = 0
    pages = 0
    while True:
        q = f"""
SELECT ?s ?p ?o WHERE {{
  ?s ?p ?o .
}}
ORDER BY ?s ?p ?o
LIMIT {int(page_size)}
OFFSET {int(offset)}
"""
        batch = execute_sparql_get(q, ep, timeout=timeout)
        pages += 1
        if not batch:
            break
        for row in batch:
            all_rows.append(
                {"s": row.get("s", ""), "p": row.get("p", ""), "o": row.get("o", "")}
            )
        if len(batch) < page_size:
            break
        offset += page_size

    ndjson_path = out_dir / f"{ns}_triples.ndjson"
    with ndjson_path.open("w", encoding="utf-8") as f:
        for row in all_rows:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")

    meta = {
        "namespace": ns,
        "endpoint": ep,
        "triple_rows": len(all_rows),
        "pages": pages,
        "file": str(ndjson_path.relative_to(mini_marie_cache_root())),
    }
    (out_dir / f"{ns}_meta.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return meta


def build_summary(all_discovery: Dict[str, Any]) -> Dict[str, Any]:
    summary: Dict[str, Any] = {"host": HOST, "namespaces": {}}
    for ns, disc in all_discovery.items():
        stats = (disc.get("queries") or {}).get("stats") or []
        st = stats[0] if stats else {}
        top_types = (disc.get("queries") or {}).get("top_types") or []
        summary["namespaces"][ns] = {
            "triples": st.get("triples"),
            "subjects": st.get("subjects"),
            "predicates": st.get("predicates"),
            "top_type": top_types[0] if top_types else None,
            "top_types_count": len(top_types),
            "prefixes": disc.get("prefixes_from_samples", [])[:10],
        }
    return summary


def main() -> int:
    parser = argparse.ArgumentParser(description="Probe and cache sg-old namespaces")
    parser.add_argument("--namespace", action="append", help="Only these namespaces")
    parser.add_argument("--timeout", type=int, default=90)
    parser.add_argument("--page-size", type=int, default=5000)
    parser.add_argument("--skip-download", action="store_true")
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=None,
        help="Default: data/mini_marie_cache/sg_old",
    )
    args = parser.parse_args()

    ns_list = args.namespace or WORKING_NAMESPACES
    out_dir = args.out_dir or (mini_marie_cache_root() / "sg_old")
    out_dir.mkdir(parents=True, exist_ok=True)

    all_discovery: Dict[str, Any] = {}
    downloads: List[Dict[str, Any]] = []

    print(f"Probing {HOST} — namespaces: {', '.join(ns_list)}\n")
    for ns in ns_list:
        print(f"=== {ns} ===")
        disc = run_discovery(ns, args.timeout)
        all_discovery[ns] = disc
        stats = disc["queries"]["stats"][0] if disc["queries"].get("stats") else {}
        print(
            f"  triples={stats.get('triples')} subjects={stats.get('subjects')} "
            f"predicates={stats.get('predicates')}"
        )
        types = disc["queries"].get("top_types") or []
        if types:
            print(f"  top type: {types[0].get('type')} ({types[0].get('c')})")
        if not args.skip_download:
            print(f"  downloading triples (page_size={args.page_size})...")
            meta = download_all_triples(
                ns, page_size=args.page_size, timeout=args.timeout, out_dir=out_dir
            )
            downloads.append(meta)
            print(f"  saved {meta['triple_rows']} rows -> {meta['file']}")

    probe_path = out_dir / "probe_report.json"
    probe_path.write_text(json.dumps(all_discovery, indent=2), encoding="utf-8")
    summary = build_summary(all_discovery)
    summary["downloads"] = downloads
    summary_path = out_dir / "summary.json"
    summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
    print(f"\nWrote {probe_path}")
    print(f"Wrote {summary_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
