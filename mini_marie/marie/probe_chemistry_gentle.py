"""
Very limited chemistry Blazegraph probe — fragile endpoint, small batches only.

- Public host only (no parallel IP hammering)
- ASK { } liveness per namespace (cheap)
- LIMIT 1 sample on a few known-populated namespaces only
- 4s pause between every request; stop batch on first hard failure

Usage:
  python -m mini_marie.probe_chemistry_gentle
"""
from __future__ import annotations

import json
import re
import ssl
import time
import urllib.parse
import urllib.request

from mini_marie.cache_paths import mini_marie_cache_root

HOST = "https://theworldavatar.io/chemistry/blazegraph"
UA = "curl/8.0"
CTX = ssl.create_default_context()
PAUSE_SEC = 4
TIMEOUT_SEC = 30
MAX_READ = 1500

ALL_NS = [
    "kb", "ontocompchem", "ontokin", "ontomops", "ontopesscan",
    "ontoprovenance", "ontospecies", "ontozeolite",
]
# Representative populated namespaces — skip empty kb/ontomops/ontopesscan for LIMIT 1
SAMPLE_NS = ["ontocompchem", "ontospecies", "ontozeolite"]


def _pause() -> None:
    time.sleep(PAUSE_SEC)


def sparql(ns: str, query: str) -> dict:
    url = f"{HOST}/namespace/{ns}/sparql?" + urllib.parse.urlencode({"query": query})
    t0 = time.perf_counter()
    req = urllib.request.Request(
        url,
        headers={"User-Agent": UA, "Accept": "application/sparql-results+json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC, context=CTX) as r:
            body = r.read(MAX_READ).decode("utf-8", errors="replace")
            ms = round((time.perf_counter() - t0) * 1000)
            return {"ok": True, "status": r.status, "ms": ms, "body": body}
    except Exception as exc:
        return {
            "ok": False,
            "error": type(exc).__name__,
            "detail": str(exc)[:120],
            "ms": round((time.perf_counter() - t0) * 1000),
        }


def parse_ask(body: str) -> bool | None:
    if '"boolean" : true' in body or '"boolean": true' in body or '"boolean":true' in body:
        return True
    if '"boolean" : false' in body or '"boolean": false' in body or '"boolean":false' in body:
        return False
    return None


def main() -> None:
    print(f"Gentle probe: {HOST}")
    print(f"Pause {PAUSE_SEC}s between requests; stop on first failure\n")

    report: dict = {"host": HOST, "pause_sec": PAUSE_SEC, "ask": {}, "sample": {}}
    aborted = False

    print("Phase 1 — ASK { } liveness")
    for ns in ALL_NS:
        if aborted:
            report["ask"][ns] = {"skipped": True}
            continue
        result = sparql(ns, "ASK { }")
        report["ask"][ns] = result
        if result.get("ok"):
            alive = parse_ask(result.get("body", ""))
            print(f"  {ns}: OK ({result['ms']}ms) alive={alive}")
        else:
            print(f"  {ns}: FAIL {result.get('error')} ({result.get('ms')}ms) — stopping")
            aborted = True
        _pause()

    if not aborted:
        print("\nPhase 2 — LIMIT 1 on 3 representative namespaces")
        for ns in SAMPLE_NS:
            result = sparql(ns, "SELECT ?s ?p ?o WHERE { ?s ?p ?o } LIMIT 1")
            if result.get("ok"):
                vals = re.findall(r'"value"\s*:\s*"([^"]*)"', result.get("body", ""))
                empty = '"bindings" : [ ]' in result.get("body", "")
                tag = "empty" if empty else " | ".join(vals[:2])[:80]
                result["preview"] = tag
                print(f"  {ns}: OK ({result['ms']}ms) {tag}")
            else:
                print(f"  {ns}: FAIL {result.get('error')} ({result.get('ms')}ms) — stopping")
                aborted = True
            report["sample"][ns] = result
            if aborted:
                break
            _pause()

    report["aborted"] = aborted
    out = mini_marie_cache_root() / "chemistry_blazegraph" / "gentle_probe.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    # Drop raw body from saved JSON to keep artifact small
    slim = json.loads(json.dumps(report))
    for section in ("ask", "sample"):
        for entry in slim.get(section, {}).values():
            entry.pop("body", None)
    out.write_text(json.dumps(slim, indent=2), encoding="utf-8")
    print(f"\nWrote {out}")
    if aborted:
        print("Probe stopped early to avoid stressing endpoint.")


if __name__ == "__main__":
    main()
