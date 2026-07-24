"""Verify sg_old NDJSON cache matches live triple counts."""

from __future__ import annotations

import json
from pathlib import Path

from mini_marie.cache_paths import mini_marie_cache_root
from mini_marie.zaha.sg_old.probe_and_cache import WORKING_NAMESPACES, endpoint_for
from mini_marie.zaha.sg_old.sparql_get import execute_sparql_get


def main() -> int:
    cache = mini_marie_cache_root() / "sg_old"
    ok = True
    print("Local cache vs live endpoint\n")
    for ns in WORKING_NAMESPACES:
        ndjson = cache / f"{ns}_triples.ndjson"
        meta_path = cache / f"{ns}_meta.json"
        if not ndjson.exists():
            print(f"{ns}: MISSING {ndjson.name}")
            ok = False
            continue
        lines = sum(1 for _ in ndjson.open(encoding="utf-8"))
        meta_rows = json.loads(meta_path.read_text(encoding="utf-8"))["triple_rows"] if meta_path.exists() else -1
        live_rows = int(
            execute_sparql_get("SELECT (COUNT(*) AS ?n) WHERE { ?s ?p ?o }", endpoint_for(ns), timeout=60)[0]["n"]
        )
        complete = lines == live_rows == meta_rows
        if not complete:
            ok = False
        status = "OK" if complete else "MISMATCH"
        print(
            f"{ns:8}  cached={lines:6}  meta={meta_rows:6}  live={live_rows:6}  [{status}]"
        )
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
